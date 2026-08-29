from pathlib import Path


def test_layer_edits_are_queued_before_main_window_rebuilds_layer_tree() -> None:
    source = Path("laser_aligner/desktop/main_window.py").read_text(encoding="utf-8")

    expected = """        self.layer_panel.layerEdited.connect(
            self._layer_edited,
            QtCore.Qt.ConnectionType.QueuedConnection,
        )
"""
    assert expected in source
