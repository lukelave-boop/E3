from __future__ import annotations

import ctypes
import os
import sys

import pytest

if not sys.platform.startswith("linux"):
    pytest.skip("native V4L2 tests require Linux", allow_module_level=True)

from laser_aligner.camera import v4l2_mjpeg


class _Mapping:
    def __init__(self, packet: bytes) -> None:
        self.packet = packet
        self.closed = False

    def __getitem__(self, item: slice) -> bytes:
        return self.packet[item]

    def close(self) -> None:
        self.closed = True


class _System:
    def __init__(self, packets: tuple[bytes, ...] = (b"first", b"latest")) -> None:
        self.packets = packets
        self.mappings: list[_Mapping] = []
        self.opened: list[tuple[str, int]] = []
        self.closed: list[int] = []
        self.requests: list[int] = []
        self.queued: list[int] = []
        self.dequeue_index = 0

    def open(self, path: str, flags: int) -> int:
        self.opened.append((path, flags))
        return 17

    def close(self, fd: int) -> None:
        self.closed.append(fd)

    def ioctl(self, fd: int, request: int, value: object) -> object:
        assert fd == 17
        self.requests.append(request)
        if request == v4l2_mjpeg.VIDIOC_QUERYCAP:
            value.capabilities = (  # type: ignore[attr-defined]
                v4l2_mjpeg.V4L2_CAP_VIDEO_CAPTURE | v4l2_mjpeg.V4L2_CAP_STREAMING
            )
        elif request == v4l2_mjpeg.VIDIOC_S_PARM:
            value.parm.capture.timeperframe.numerator = 1  # type: ignore[attr-defined]
            value.parm.capture.timeperframe.denominator = 30  # type: ignore[attr-defined]
        elif request == v4l2_mjpeg.VIDIOC_REQBUFS:
            value.count = 2  # type: ignore[attr-defined]
        elif request == v4l2_mjpeg.VIDIOC_QUERYBUF:
            value.length = 64  # type: ignore[attr-defined]
            value.m.offset = value.index * 64  # type: ignore[attr-defined]
        elif request == v4l2_mjpeg.VIDIOC_DQBUF:
            value.index = self.dequeue_index % len(self.packets)  # type: ignore[attr-defined]
            value.bytesused = len(self.packets[value.index])  # type: ignore[attr-defined]
            self.dequeue_index += 1
        elif request == v4l2_mjpeg.VIDIOC_QBUF:
            self.queued.append(value.index)  # type: ignore[attr-defined]
        return value

    def mmap(self, fd: int, length: int, offset: int) -> _Mapping:
        assert fd == 17
        assert length == 64
        mapping = _Mapping(self.packets[offset // 64])
        self.mappings.append(mapping)
        return mapping

    def readable(self, fd: int, timeout: float) -> bool:
        return fd == 17 and timeout > 0


def test_native_v4l2_negotiates_mjpeg_fps_and_one_owner_lifecycle() -> None:
    system = _System()
    capture = v4l2_mjpeg.NativeV4L2MjpegCapture(
        "/dev/v4l/by-id/camera", 1920, 1080, 30, system=system
    )

    assert system.opened == [("/dev/v4l/by-id/camera", os.O_RDWR | os.O_NONBLOCK)]
    assert capture.width == 1920
    assert capture.height == 1080
    assert capture.negotiated_fps == 30
    assert v4l2_mjpeg.VIDIOC_S_FMT in system.requests
    assert v4l2_mjpeg.VIDIOC_STREAMON in system.requests
    assert capture.isOpened()

    capture.release()
    capture.release()

    assert system.requests.count(v4l2_mjpeg.VIDIOC_STREAMOFF) == 1
    assert system.closed == [17]
    assert all(mapping.closed for mapping in system.mappings)


def test_native_v4l2_dequeues_exact_bytes_and_always_requeues_buffer() -> None:
    system = _System((b"\xff\xd8one\xff\xd9", b"\xff\xd8newest\xff\xd9"))
    capture = v4l2_mjpeg.NativeV4L2MjpegCapture(
        "/dev/v4l/by-id/camera", 1920, 1080, 10, system=system
    )
    initial_queues = len(system.queued)

    assert capture.read() == (True, b"\xff\xd8one\xff\xd9")
    assert capture.read() == (True, b"\xff\xd8newest\xff\xd9")
    assert len(system.queued) == initial_queues + 2

    capture.release()


def test_native_v4l2_rejects_oversized_driver_packet_but_requeues() -> None:
    system = _System()
    capture = v4l2_mjpeg.NativeV4L2MjpegCapture(
        "/dev/v4l/by-id/camera",
        1920,
        1080,
        10,
        system=system,
        max_packet_bytes=4,
    )
    initial_queues = len(system.queued)
    try:
        try:
            capture.read()
        except OSError as exc:
            assert "bounded JPEG limit" in str(exc)
        else:  # pragma: no cover - assertion branch
            raise AssertionError("oversized packet was accepted")
        assert len(system.queued) == initial_queues + 1
    finally:
        capture.release()


def test_v4l2_abi_structures_match_linux_video_header_sizes() -> None:
    assert ctypes.sizeof(v4l2_mjpeg._Capability) == 104
    assert ctypes.sizeof(v4l2_mjpeg._Format) == 208
    assert ctypes.sizeof(v4l2_mjpeg._StreamParm) == 204
    assert ctypes.sizeof(v4l2_mjpeg._Buffer) in {68, 88}
