from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_home_installer_seeds_exact_household_machine_state() -> None:
    wrapper = (ROOT / "packaging" / "build_home_installer.ps1").read_text(
        encoding="utf-8"
    )
    builder = (ROOT / "packaging" / "build_windows.ps1").read_text(
        encoding="utf-8"
    )
    installer = (ROOT / "packaging" / "E3-installer.iss").read_text(
        encoding="utf-8"
    )
    batch = (ROOT / "BUILD_E3_HOME_INSTALLER.bat").read_text(encoding="utf-8")

    assert "config\\network-local.json" in wrapper
    assert "data\\machines.json" in wrapper
    assert "secrets\\bridge-token.txt" in wrapper
    assert "data\\calibration_profiles" in wrapper
    assert "-MachineSeed" in wrapper
    assert "Do not publish" in wrapper

    assert "MachineRegistryPath" in builder
    assert '"data\\machines.json"' in builder
    assert 'machine-seed\\data\\machines.json' in installer
    assert "onlyifdoesntexist" in installer
    assert "build_home_installer.ps1" in batch


def _machine_seed_manifest() -> list[tuple[str, str, set[str]]]:
    installer = (ROOT / "packaging" / "E3-installer.iss").read_text(encoding="utf-8")
    entries: list[tuple[str, str, set[str]]] = []
    in_files = False
    for raw_line in installer.splitlines():
        line = raw_line.strip()
        if line == "[Files]":
            in_files = True
            continue
        if in_files and line.startswith("[") and line.endswith("]"):
            break
        if not in_files or not line.startswith('Source: "machine-seed\\'):
            continue

        parts = [part.strip() for part in line.split(";")]
        source = parts[0].split('"', 2)[1]
        destination = parts[1].split('"', 2)[1]
        flags = set()
        for part in parts[2:]:
            if part.startswith("Flags:"):
                flags.update(part.removeprefix("Flags:").strip().split())
        entries.append((source, destination, flags))
    return entries


def _destination_relative_path(destination: str) -> Path:
    marker = r"{localappdata}\E3 Positioning System"
    assert destination.startswith(marker)
    suffix = destination[len(marker):].lstrip("\\")
    return Path(*suffix.split("\\")) if suffix else Path()


def _source_relative_path(source: str) -> tuple[Path, bool]:
    marker = "machine-seed\\"
    assert source.startswith(marker)
    relative = source[len(marker):]
    recursive = relative.endswith(r"\*")
    if recursive:
        relative = relative[:-2]
    return Path(*relative.split("\\")), recursive


def _simulate_machine_seed_install(
    manifest: list[tuple[str, str, set[str]]],
    *,
    seed_root: Path,
    state_root: Path,
) -> None:
    for source_text, destination_text, flags in manifest:
        source_relative, recursive = _source_relative_path(source_text)
        source = seed_root / source_relative
        destination = state_root / _destination_relative_path(destination_text)

        sources = (
            sorted(path for path in source.rglob("*") if path.is_file())
            if recursive
            else [source]
        )
        for item in sources:
            relative = item.relative_to(source) if recursive else Path(item.name)
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if "onlyifdoesntexist" in flags and target.exists():
                continue
            target.write_bytes(item.read_bytes())


def _write_seed_tree(seed_root: Path) -> dict[Path, bytes]:
    payloads = {
        Path("config/network-local.json"): b'{"seed":"config"}',
        Path("data/machines.json"): b'{"seed":"machines"}',
        Path("data/calibration_profiles/test-profile/profile.json"): b'{"seed":"profile"}',
        Path("data/calibration_profiles/test-profile/bed_calibration.json"): b'{"seed":"calibration"}',
        Path("secrets/bridge-token.txt"): b"seed-bridge-token-with-enough-entropy",
    }
    for relative, payload in payloads.items():
        path = seed_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    return payloads


def test_home_installer_update_preserves_existing_machine_state_byte_for_byte(
    tmp_path: Path,
) -> None:
    manifest = _machine_seed_manifest()
    assert manifest

    # Every household-state file is persistent: updates may seed a missing file,
    # but may never replace an existing user's copy or remove it on uninstall.
    for _source, _destination, flags in manifest:
        assert "onlyifdoesntexist" in flags
        assert "uninsneveruninstall" in flags

    seed_root = tmp_path / "machine-seed"
    state_root = tmp_path / "existing-user-state"
    _write_seed_tree(seed_root)

    customized = {
        Path("config/network-local.json"): b'{"user":"custom-config"}',
        Path("data/machines.json"): b'{"user":"custom-machines"}',
        Path("data/calibration_profiles/test-profile/profile.json"): b'{"user":"custom-profile"}',
        Path("data/calibration_profiles/test-profile/bed_calibration.json"): b'{"user":"custom-calibration"}',
        Path("secrets/bridge-token.txt"): b"user-custom-bridge-token-do-not-replace",
    }
    for relative, payload in customized.items():
        path = state_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)

    before = {
        relative: (state_root / relative).read_bytes()
        for relative in customized
    }
    _simulate_machine_seed_install(
        manifest,
        seed_root=seed_root,
        state_root=state_root,
    )
    after = {
        relative: (state_root / relative).read_bytes()
        for relative in customized
    }
    assert after == before


def test_home_installer_first_install_still_seeds_missing_machine_state(
    tmp_path: Path,
) -> None:
    manifest = _machine_seed_manifest()
    seed_root = tmp_path / "machine-seed"
    state_root = tmp_path / "new-user-state"
    seed_payloads = _write_seed_tree(seed_root)

    _simulate_machine_seed_install(
        manifest,
        seed_root=seed_root,
        state_root=state_root,
    )

    for relative, expected in seed_payloads.items():
        assert (state_root / relative).read_bytes() == expected
