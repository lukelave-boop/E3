"""Metadata-only initializer tests using synthetic checkouts and private payloads."""

from __future__ import annotations

import builtins
import importlib.util
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PACKAGING = Path(__file__).resolve().parents[1] / "packaging"
SPEC = importlib.util.spec_from_file_location("e3_inventory_under_test", PACKAGING / "build_storage_inventory.py")
assert SPEC is not None and SPEC.loader is not None
inventory = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = inventory
SPEC.loader.exec_module(inventory)


def checkout(path: Path) -> Path:
    (path / "packaging").mkdir(parents=True)
    (path / "pyproject.toml").write_bytes(b"source marker")
    (path / "packaging" / "build_windows.ps1").write_bytes(b"source marker")
    return path


@pytest.fixture
def sample(tmp_path: Path):
    root = checkout(tmp_path / "checkout")
    payloads = {
        "dist/E3/E3.exe": b"app" * 10,
        "dist/E3/secrets/bridge-token.txt": b"do-not-read-private-payload",
        "installer-dist/Private-Home.exe": b"private-installer" * 10,
        "packaging/machine-seed/secrets/bridge-token.txt": b"seed-secret",
        "build/firmware-tools/compiler.bin": b"compiler",
        ".venv/Lib/runtime.bin": b"runtime",
        ".e3-builds/new-build/dist/E3/E3.exe": b"isolated-runtime",
    }
    for relative, contents in payloads.items():
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(contents)
    # Outside the finite generated scope; neither enumerated nor counted.
    (root / "config").mkdir()
    (root / "config" / "credentials.json").write_bytes(b"original credentials")
    return root, sum(map(len, payloads.values()))


def deny_all_payload_reads(monkeypatch: pytest.MonkeyPatch, root: Path):
    original_os_open = os.open
    original_path_open = Path.open
    original_builtin_open = builtins.open

    def forbidden(path):
        return isinstance(path, (str, bytes, os.PathLike)) and Path(path).is_relative_to(root)

    def guarded_os_open(path, flags, *args, **kwargs):
        if forbidden(path):
            pytest.fail(f"payload descriptor opened: {path}")
        return original_os_open(path, flags, *args, **kwargs)

    def guarded_path_open(path, *args, **kwargs):
        if forbidden(path):
            pytest.fail(f"payload Path.open called: {path}")
        return original_path_open(path, *args, **kwargs)

    def guarded_builtin_open(path, *args, **kwargs):
        if forbidden(path):
            pytest.fail(f"payload open called: {path}")
        return original_builtin_open(path, *args, **kwargs)

    monkeypatch.setattr(os, "open", guarded_os_open)
    monkeypatch.setattr(Path, "open", guarded_path_open)
    monkeypatch.setattr(builtins, "open", guarded_builtin_open)


def test_metadata_inventory_counts_private_outputs_without_any_payload_open(sample, monkeypatch):
    root, expected = sample
    deny_all_payload_reads(monkeypatch, root)
    result = inventory.inventory([root])
    assert result["complete"]
    assert result["logical_bytes"] == expected
    assert result["payload_contents_read"] is False
    assert result["passes"] == 2


def test_inventory_writes_only_requested_new_report(sample, tmp_path, monkeypatch):
    root, expected = sample
    deny_all_payload_reads(monkeypatch, root)
    report = tmp_path / "report.json"
    inventory.write_inventory(roots=[root], report_path=report)
    result = json.loads(report.read_text())
    assert result["logical_bytes"] == expected
    assert not (tmp_path / "state.json").exists()
    with pytest.raises(inventory.InventoryError, match="overwrite"):
        inventory.write_inventory(roots=[root], report_path=report)


def test_init_creates_guard_compatible_state_without_ownership_or_build_lease(sample, tmp_path, monkeypatch):
    root, expected = sample
    deny_all_payload_reads(monkeypatch, root)
    state = tmp_path / "state.json"
    report = tmp_path / "initialization-report.json"
    result = inventory.initialize(roots=[root], state_path=state, report_path=report)
    data = json.loads(state.read_text())
    assert result["initialized"]
    assert data["baseline_bytes"] == expected
    assert data["growth_headroom_bytes"] == 8 * 1024**3
    assert data["roots"] == [str(root)]
    assert data["leases"] == data["owned_outputs"] == []
    assert data["ephemeral"] is False
    assert data["observed_registries"] == []
    assert not state.with_name("state.json.lock").exists()


def test_existing_state_is_not_opened_or_overwritten(sample, tmp_path, monkeypatch):
    root, _ = sample
    state = tmp_path / "state.json"
    state.write_bytes(b"existing-user-state")
    original_open = Path.open

    def blocked(path, *args, **kwargs):
        if path == state:
            pytest.fail("existing state was read")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", blocked)
    with pytest.raises(inventory.InventoryError, match="overwrite"):
        inventory.initialize(roots=[root], state_path=state, report_path=tmp_path / "report.json")
    assert not (tmp_path / "report.json").exists()


def test_active_guard_lock_is_preserved_and_prevents_init(sample, tmp_path):
    root, _ = sample
    lock = tmp_path / "state.json.lock"
    lock.write_bytes(b"another-builder")
    with pytest.raises(inventory.InventoryError, match="locked"):
        inventory.initialize(roots=[root], state_path=tmp_path / "state.json", report_path=tmp_path / "report.json")
    assert lock.read_bytes() == b"another-builder"


def test_access_error_writes_incomplete_report_and_never_initializes(sample, tmp_path, monkeypatch):
    root, _ = sample
    original = os.scandir

    def inaccessible(path):
        if Path(path) == root / "packaging" / "machine-seed":
            raise PermissionError("synthetic denied directory")
        return original(path)

    monkeypatch.setattr(os, "scandir", inaccessible)
    state, report = tmp_path / "state.json", tmp_path / "report.json"
    with pytest.raises(inventory.InventoryError, match="incomplete"):
        inventory.initialize(roots=[root], state_path=state, report_path=report)
    assert not state.exists()
    result = json.loads(report.read_text())
    assert result["logical_bytes"] is None
    assert not result["complete"]
    assert "denied" in " ".join(result["errors"])


def test_drift_between_passes_refuses_baseline(sample, tmp_path, monkeypatch):
    root, _ = sample
    original = inventory._scan_pass
    count = 0

    def drift(roots):
        nonlocal count
        result = original(roots)
        count += 1
        if count == 1:
            (root / "build" / "new-cache.bin").write_bytes(b"new")
        return result

    monkeypatch.setattr(inventory, "_scan_pass", drift)
    with pytest.raises(inventory.InventoryError, match="incomplete"):
        inventory.initialize(roots=[root], state_path=tmp_path / "state.json", report_path=tmp_path / "report.json")
    assert not (tmp_path / "state.json").exists()


@pytest.mark.parametrize("attribute", ["hardlink", "reparse"])
def test_uncertain_identity_or_links_never_become_zero_bytes(sample, monkeypatch, attribute):
    root, _ = sample
    target = root / "dist" / "E3" / "E3.exe"
    original = Path.lstat

    def suspect(path, *args, **kwargs):
        value = original(path, *args, **kwargs)
        if path != target:
            return value
        fields = {name: getattr(value, name) for name in dir(value) if name.startswith("st_")}
        if attribute == "hardlink":
            fields["st_nlink"] = 2
        else:
            fields["st_file_attributes"] = fields.get("st_file_attributes", 0) | inventory._REPARSE
        return SimpleNamespace(**fields)

    monkeypatch.setattr(Path, "lstat", suspect)
    result = inventory.inventory([root])
    assert result["complete"] is False
    assert result["logical_bytes"] is None


def test_ancestor_reparse_is_rejected_before_enumeration(sample, monkeypatch):
    root, _ = sample
    original = inventory._checked_stat

    def rejected(path):
        if path == root:
            raise inventory.InventoryError("synthetic root reparse")
        return original(path)

    monkeypatch.setattr(inventory, "_checked_stat", rejected)
    monkeypatch.setattr(os, "scandir", lambda path: pytest.fail("enumeration followed rejected root"))
    assert inventory.inventory([root])["complete"] is False


def test_only_explicit_roots_are_enumerated_and_nested_scopes_are_deduplicated(sample):
    root, expected = sample
    other = checkout(root / ".e3-builds" / "nested-source")
    payload = other / "dist" / "E3" / "E3.exe"
    payload.parent.mkdir(parents=True)
    payload.write_bytes(b"nested payload")
    nested_markers = len(b"source marker") * 2
    result = inventory.inventory([root, other, root])
    assert result["complete"]
    assert result["logical_bytes"] == expected + len(b"nested payload") + nested_markers


def test_report_and_state_may_not_be_created_inside_checkout(sample, tmp_path):
    root, _ = sample
    with pytest.raises(inventory.InventoryError, match="outside"):
        inventory.write_inventory(roots=[root], report_path=root / "report.json")
    with pytest.raises(inventory.InventoryError, match="outside"):
        inventory.initialize(roots=[root], report_path=tmp_path / "report.json", state_path=root / "state.json")


def test_absent_root_is_incomplete_not_an_empty_valid_baseline(tmp_path):
    result = inventory.inventory([tmp_path / "absent"])
    assert not result["complete"]
    assert result["logical_bytes"] is None


def test_initializer_schema_is_readable_by_existing_guard(sample, tmp_path):
    root, _ = sample
    sys.path.insert(0, str(PACKAGING))
    try:
        import build_storage_guard as guard
    finally:
        sys.path.pop(0)
    state = tmp_path / "state.json"
    inventory.initialize(roots=[root], report_path=tmp_path / "report.json", state_path=state)
    assert guard._load_state(state)["leases"] == []
    assert set(inventory.GENERATED_RELATIVE_PATHS) == {str(path.relative_to(root)).replace("\\", "/") for path in guard._census_paths(root)}


def test_inventory_never_imports_guard_or_retention_implementation():
    source = (PACKAGING / "build_storage_inventory.py").read_text()
    assert "import build_storage_guard" not in source
    assert "import artifact_retention" not in source
    assert "subprocess" not in source
    assert "read_bytes(" not in source
    assert "read_text(" not in source
