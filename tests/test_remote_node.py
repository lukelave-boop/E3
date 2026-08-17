import pytest

from laser_aligner.remote_node import main


def test_remote_node_requires_explicit_hardware_gate() -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--config", "unused.json"])
    assert exc.value.code == 2
