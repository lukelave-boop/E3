"""Fixed configured focus areas, including the operator's retained camera point."""
from dataclasses import replace

import pytest

from laser_aligner.config import WorkArea
from laser_aligner.errors import CalibrationError, MachineError, SafetyError
from laser_aligner.machine.focus_bounds import FocusXYBounds
from tests import test_focus_click_mapping as mapping_helpers
from tests import test_laser_focus as focus_helpers

click = mapping_helpers.click
mapped_context = mapping_helpers.mapped_context
focus = focus_helpers.focus
focus_machine = focus_helpers.focus_machine
machine_probe = focus_helpers.machine_probe


HONEYCOMB = ((18.218005, 29.679375), (228.217364, 30.198421),
             (227.698319, 240.197779), (17.698960, 239.678734))
POINT = (80.222, 213.031)


def test_retained_point_and_transfer_use_fixed_honeycomb_area():
    bounds = FocusXYBounds(WorkArea(10., 210., 10., 210.), HONEYCOMB)
    assert bounds.contains(POINT)
    assert bounds.contains_segment((15., 195.), (76.920, 174.423))
    assert bounds.contains_segment((76.920, 174.423), POINT)
    assert not FocusXYBounds(WorkArea(10., 210., 10., 210.), None).contains(POINT)


@pytest.mark.parametrize("point", [(229., 150.), (100., 241.), (18., 239.8),
                                   (float('nan'), 100.), (True, 100.)])
def test_point_outside_union_or_polygon_edge_rejected(point):
    assert not FocusXYBounds(WorkArea(10., 210., 10., 210.), HONEYCOMB).contains(point)


def test_union_does_not_authorize_convex_hull_gaps():
    bounds = FocusXYBounds(WorkArea(0., 2., 0., 2.), ((1., 1.), (3., 1.), (3., 3.), (1., 3.)))
    assert bounds.contains((.1, 1.9)) and bounds.contains((1.1, 2.9))
    assert not bounds.contains_segment((.1, 1.9), (1.1, 2.9))
    assert bounds.contains_segment((.1, .1), (2.9, 2.9))
    assert bounds.contains_segment((2.9, 2.9), (.1, .1))


def test_disconnected_areas_cannot_be_crossed():
    bounds = FocusXYBounds(WorkArea(0., 2., 0., 2.), ((3., 0.), (5., 0.), (5., 2.), (3., 2.)))
    assert not bounds.contains_segment((1., 1.), (4., 1.))


@pytest.mark.parametrize("polygon", [[], 'bad', ((0., 0.), (2., 2.), (0., 2.), (2., 0.)),
                                    ((0., 0.), (True, 2.), (0., 2.))])
def test_corrupt_polygon_cannot_silently_fall_back(polygon):
    with pytest.raises(SafetyError):
        FocusXYBounds(WorkArea(), polygon)


def test_camera_point_acceptance_and_bounds_change_invalidate_selection(mapped_context, monkeypatch):
    context, _ = mapped_context
    context.settings.machine.work_area = WorkArea(10., 210., 10., 210.)
    context.bed.calibration.provenance = context._bed_provenance()
    monkeypatch.setattr(context.bed, 'image_to_mm', lambda *args: POINT)
    with pytest.raises(CalibrationError, match='machine work area'):
        click(context)
    context.settings.laser.guarded_output_polygon_mm = HONEYCOMB
    selected = click(context)
    assert selected['target_machine_xy_mm'] == list(POINT)
    context.settings.laser.guarded_output_polygon_mm = None
    with pytest.raises(CalibrationError, match='changed after selection'):
        click(context, mapping_signature=selected['mapping_signature'])


def test_camera_rejects_point_outside_both_configured_areas(mapped_context, monkeypatch):
    context, _ = mapped_context
    context.settings.laser.guarded_output_polygon_mm = HONEYCOMB
    monkeypatch.setattr(context.bed, 'image_to_mm', lambda *args: (229., 150.))
    with pytest.raises(CalibrationError, match='Neither the machine rectangle'):
        click(context)


def test_full_probe_measure_return_teach_sequence_beyond_camera_rectangle(focus_machine):
    machine, state, primary = focus_machine
    machine.settings.work_area = WorkArea(10., 210., 10., 210.)
    machine.laser_settings.guarded_output_polygon_mm = HONEYCOMB
    machine.focus_control('reference', confirmed=True)
    machine.focus_control('set_xy_offset', confirmed=True, value=[3.302, 38.608])
    positioned = machine.focus_control('position_probe', confirmed=True, value=list(POINT))
    assert positioned['surface'] is None
    assert (primary.x, primary.y) == (76.920, 174.423)
    measured = machine.focus_control('measure', confirmed=True)
    mid = measured['surface']['id']
    machine.focus_control('align_laser', confirmed=True, measurement_id=mid)
    assert (primary.x, primary.y) == POINT
    machine.focus_control('jog', confirmed=True, value=-1., measurement_id=mid)
    assert state.serial.z == 29.
    machine.focus_control('teach', confirmed=True, measurement_id=mid)
    assert machine.settings.work_area.y_max == 210.


@pytest.mark.parametrize('bad', ['selected', 'probe', 'laser'])
def test_service_rechecks_every_endpoint_without_xy_motion(focus_machine, bad):
    machine, state, primary = focus_machine
    machine.laser_settings.guarded_output_polygon_mm = HONEYCOMB
    machine.focus_control('reference', confirmed=True)
    machine.focus_control('set_xy_offset', confirmed=True, value=[3.302, -40. if bad == 'probe' else 38.608])
    if bad == 'laser':
        machine.laser_settings.spot_offset_y_mm = -40.
    before = (primary.x, primary.y)
    with pytest.raises(SafetyError, match='endpoints'):
        machine.focus_control('position_probe', confirmed=True,
                              value=[100., 241.] if bad == 'selected' else list(POINT))
    assert (primary.x, primary.y) == before
    assert state.serial.z == 30.


def test_polygon_change_at_write_boundary_prevents_transfer(focus_machine, monkeypatch):
    machine, state, primary = focus_machine
    machine.laser_settings.guarded_output_polygon_mm = HONEYCOMB
    machine.focus_control('reference', confirmed=True)
    machine.focus_control('set_xy_offset', confirmed=True, value=[3.302, 38.608])
    before = (primary.x, primary.y)
    original = machine._begin_session_transaction

    def begin(session, sequence, write):
        if machine._jog_position_mm is None:
            machine.laser_settings.guarded_output_polygon_mm = None
        return original(session, sequence, write)

    monkeypatch.setattr(machine, '_begin_session_transaction', begin)
    with pytest.raises(MachineError, match='bounds changed'):
        machine.focus_control('position_probe', confirmed=True, value=list(POINT))
    assert (primary.x, primary.y) == before
    assert state.state.xy_sequence is None


def test_rectangle_change_also_invalidates_camera_selection(mapped_context):
    context, _ = mapped_context
    selected = click(context)
    context.settings.machine.work_area = replace(context.settings.machine.work_area, x_max=201.)
    context.bed.calibration.provenance = context._bed_provenance()
    with pytest.raises(CalibrationError, match='changed after selection'):
        click(context, mapping_signature=selected['mapping_signature'])


def test_extreme_finite_geometry_rejected_without_overflow():
    with pytest.raises(SafetyError):
        FocusXYBounds(WorkArea(), ((0., 0.), (1e308, 0.), (1e308, 1e308), (0., 1e308)))
    assert not FocusXYBounds(WorkArea(), None).contains((10**1000, 1.))
