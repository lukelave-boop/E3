import ctypes
import importlib.util
import json
import struct
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

spec = importlib.util.spec_from_file_location("ender_capture", Path(__file__).parents[1] / "scripts/capture_ender_usb.py")
capture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(capture)


def header(*, bus=1, dev=4, event=b"C", kind=3, status=0, length=3, tag=0xDEADBEEF):
    return capture.HEADER.pack(tag, event[0], kind, 0x81, dev, bus, 0, 0,
                               1000, 100, status, length, 8, b"SECRET!!", 0, 0, 0, 0)


def test_metadata_is_exact_header_only_and_redacts_kernel_addresses():
    ids = {}
    result = capture.metadata(header(), 1, 4, ids)
    assert capture.HEADER.size == 64
    assert result["request"] == 1 and result["length"] == 3
    assert result["usb_timestamp_us"] == 1_000_000_100
    assert result["direction"] == "in"
    assert "SECRET" not in json.dumps(result) and "3735928559" not in json.dumps(result)
    assert capture.metadata(header(tag=0xDEADBEEF), 1, 4, ids)["request"] == 1


@pytest.mark.parametrize("change", [{"bus": 2}, {"dev": 5}])
def test_other_devices_are_discarded_before_id_tracking(change):
    ids = {}
    assert capture.metadata(header(**change), 1, 4, ids) is None
    assert not ids


@pytest.mark.parametrize("raw", [b"short", header(event=b"X"), header(kind=8)])
def test_malformed_headers_rejected(raw):
    with pytest.raises(ValueError):
        capture.metadata(raw, 1, 4, {})


@pytest.fixture
def device(tmp_path):
    root = tmp_path / "ttyUSB0" / "device"
    root.mkdir(parents=True)
    for name, content in {"idVendor": "1a86", "idProduct": "7523", "busnum": "1", "devnum": "4"}.items():
        (root / name).write_text(content)
    return root


def test_device_selection_restricts_tty_and_vid_pid(device, tmp_path):
    assert capture.device_for_tty("ttyUSB0", tmp_path) == device
    with pytest.raises(ValueError):
        capture.device_for_tty("../../dev/sda", tmp_path)
    (device / "idVendor").write_text("046d")
    with pytest.raises(ValueError, match="WCH"):
        capture.device_for_tty("ttyUSB0", tmp_path)


class Monitor:
    def __init__(self, rows):
        self.rows = iter(rows)
        self.calls = 0

    def read(self):
        return next(self.rows, None)

    def stats(self):
        self.calls += 1
        return {"queued": 0, "dropped_since_previous_stats": self.calls}


def clock():
    state = iter(i * .1 for i in range(1000))
    return lambda: next(state)


def test_bounded_capture_sums_drops_and_preserves_error(device, tmp_path):
    output = tmp_path / "trace.jsonl"
    monitor = Monitor([header(dev=5), None, header(status=-32), header()])
    summary = capture.collect(monitor, device, 2, output, clock=clock(), sleep=lambda _: None)
    rows = [json.loads(row) for row in output.read_text().splitlines()]
    assert [row["status"] for row in rows] == [-32, 0]
    assert summary["selected_events"] == 2 and summary["usb_errors"] == 1
    assert summary["capture_dropped_events"] == 3
    assert summary["other_device_headers_discarded"] == 1
    assert summary["reason"] == "time limit"


def test_byte_limit_preserves_whole_records(device, tmp_path, monkeypatch):
    monkeypatch.setattr(capture, "MAX_BYTES", 1)
    output = tmp_path / "trace.jsonl"
    summary = capture.collect(Monitor([header()]), device, 2, output, clock=clock(), sleep=lambda _: None)
    assert summary["reason"] == "8 MiB limit"
    assert output.read_bytes() == b""


def test_missing_final_statistics_are_unknown_not_zero(device, tmp_path):
    class UnavailableStats(Monitor):
        def stats(self):
            if self.calls:
                raise OSError("monitor disappeared")
            return super().stats()
    summary = capture.collect(UnavailableStats([header()]), device, 1,
                              tmp_path / "trace", clock=clock(), sleep=lambda _: None)
    assert summary["selected_events"] == 1
    assert summary["capture_dropped_events"] is None
    assert summary["queued_at_end"] is None


def test_address_change_stops_before_recording_new_device(device, tmp_path, monkeypatch):
    identities = iter([(1, 4), (1, 6)])
    monkeypatch.setattr(capture, "identity", lambda _: next(identities))
    summary = capture.collect(Monitor([header(dev=6)]), device, 2, tmp_path / "trace", clock=clock())
    assert "address changed" in summary["reason"]
    assert summary["selected_events"] == 0


def test_binary_api_requests_no_payload_and_opens_only_usbmon(monkeypatch):
    calls = []
    def ioctl(fd, request, arg, mutate):
        if request & 255 == 10:
            address, data, length = struct.unpack("@PPP", arg)
            assert data == length == 0
            ctypes.memmove(address, header(), 64)
        else:
            arg[:] = struct.pack("=II", 2, 3)
    monkeypatch.setitem(sys.modules, "fcntl", SimpleNamespace(ioctl=ioctl))
    monkeypatch.setattr(capture.os, "O_NONBLOCK", 2048, raising=False)
    monkeypatch.setattr(capture.os, "open", lambda path, flags: calls.append(path) or 123)
    monkeypatch.setattr(capture.os, "close", lambda fd: None)
    monitor = capture.UsbMonitor(1)
    assert monitor.read() == header()
    assert monitor.stats()["dropped_since_previous_stats"] == 3
    monitor.close()
    assert calls == ["/dev/usbmon1"]
