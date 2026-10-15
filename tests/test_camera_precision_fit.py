from types import SimpleNamespace

import numpy as np
import pytest

from laser_aligner.config import WorkArea
from laser_aligner.geometry.foreground import ForegroundContourTree
from laser_aligner.project.raster_vectorize import RasterVectorizationError
from laser_aligner.vision import object_trace
from laser_aligner.vision.object_trace import TraceOptions


def _fit(options):
    image = np.full((100, 100, 3), 200, np.uint8)
    contour = np.array([[20, 20], [70, 20], [70, 70], [20, 70]], dtype=float)
    detection = SimpleNamespace(selected_default=True, diagnostics={})
    object_trace._fit_trace_candidate_native(
        image, detection, options, WorkArea(0, 50, 0, 50), WorkArea(0, 50, 0, 50),
        2.0, (contour,), ForegroundContourTree(0, (0,), (None,), (0,), (20, 20, 51, 51), False),
    )
    return detection


def test_precision_fit_honors_requested_tolerance_below_source_pitch():
    result = _fit(TraceOptions(precision_placement=True, native_fitting_tolerance_mm=.05))
    assert result.native_verified
    assert result.diagnostics["native_fitting_tolerance_mm"] == .05
    assert result.diagnostics["source_pixel_spacing_mm"] == .5


def test_precision_fit_rejects_insufficient_evidence_without_relaxation(monkeypatch):
    observed = []

    def reject(*args, **kwargs):
        observed.append(kwargs["fitting_tolerance_mm"])
        raise RasterVectorizationError("Cannot prove the contour fit")

    monkeypatch.setattr(object_trace, "fit_physical_contours_to_native_path", reject)
    with pytest.raises(RasterVectorizationError, match="Insufficient camera evidence.*0.05 mm"):
        _fit(TraceOptions(precision_placement=True, native_fitting_tolerance_mm=.05))
    assert observed == [.05]


def test_legacy_camera_fit_reports_effective_and_requested_tolerance():
    result = _fit(TraceOptions(native_fitting_tolerance_mm=.05))
    assert result.diagnostics["native_fitting_tolerance_mm"] == .5
    assert result.diagnostics["requested_fitting_tolerance_mm"] == .05


@pytest.mark.parametrize("value", ["true", 1, None])
def test_precision_fit_requires_boolean_option(value):
    with pytest.raises(ValueError, match="precision_placement"):
        TraceOptions(precision_placement=value)
