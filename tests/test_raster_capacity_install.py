import importlib.util
import json
from pathlib import Path

import pytest

from scripts import package_raster_capacity as packager


@pytest.fixture
def kit(tmp_path, monkeypatch):
    # Exercise hash-bound upgrade behavior even in a shallow CI checkout.
    # The real pinned predecessor is separately checked by the Pi installer.
    baseline = b"# recorded predecessor fixture\n"
    monkeypatch.setattr(
        packager, "source",
        lambda revision, name: (
            baseline if revision == packager.BASELINE
            else (
                b"MAX_STREAM_COMMANDS = 1_000_000\n" if name == packager.FILES[0]
                else b"# historical capacity service fixture\n" if name == packager.FILES[1]
                else (packager.ROOT / name).read_bytes().replace(b"\r\n", b"\n")
            )
        ),
    )
    bundle = packager.package(tmp_path / "bundle", "HEAD")
    spec = importlib.util.spec_from_file_location("raster_installer", bundle / "install_raster_capacity.py")
    installer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(installer)
    project = tmp_path / "project"
    (project / "laser_aligner/gcode").mkdir(parents=True)
    service = project / packager.FILES[1]
    service.parent.mkdir(parents=True)
    service.write_bytes(baseline)
    monkeypatch.setattr(installer, "inactive", lambda: None)
    return installer, project, bundle


def test_companion_dry_run_apply_and_retry_preserve_unrelated_data(kit):
    installer, project, bundle = kit
    config = project / "operator-config.json"
    config.write_text("preserve", encoding="utf-8")
    old_service = (project / packager.FILES[1]).read_bytes()
    result = installer.install(project, bundle=bundle)
    assert not result["applied"]
    assert not (project / packager.FILES[0]).exists()
    result = installer.install(project, bundle=bundle, apply=True)
    assert len(result["files"]) == 2
    assert not result["service_started"]
    assert Path(result["files"][1]["backup"]).read_bytes() == old_service
    for name in packager.FILES:
        assert (project / name).read_bytes() == (bundle / name).read_bytes()
    assert config.read_text(encoding="utf-8") == "preserve"
    assert all(item["status"] == "already_current"
               for item in installer.install(project, bundle=bundle)["files"])
    assert set(json.loads((bundle / "manifest.json").read_text())["predecessors"][0]["files"]) == set(packager.FILES)


def test_companion_rejects_unknown_source_before_any_write(kit):
    installer, project, bundle = kit
    service = project / packager.FILES[1]
    original = service.read_bytes() + b"\n# operator edit\n"
    service.write_bytes(original)
    with pytest.raises(ValueError, match="unknown local changes"):
        installer.install(project, bundle=bundle, apply=True)
    assert service.read_bytes() == original
    assert not (project / packager.FILES[0]).exists()


def test_companion_requires_stopped_service_before_any_write(kit, monkeypatch):
    installer, project, bundle = kit

    def active():
        raise ValueError("service is active")

    monkeypatch.setattr(installer, "inactive", active)
    with pytest.raises(ValueError, match="service is active"):
        installer.install(project, bundle=bundle, apply=True)
    assert not (project / packager.FILES[0]).exists()
