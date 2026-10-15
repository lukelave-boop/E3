from types import SimpleNamespace

import pytest

from laser_aligner.app import AppContext
from laser_aligner.calibration.camera_identity import remote_camera_identity
from tests.test_calibration_provenance import _install_test_bed, _settings


def test_identity_requires_stable_camera_path_and_binds_credential():
    device = '/dev/v4l/by-id/usb-camera-serial'
    identity = remote_camera_identity(device, 'a' * 32)
    assert identity and len(identity) == 64
    assert identity == remote_camera_identity(device, 'a' * 32)
    assert identity != remote_camera_identity(device + '-other', 'a' * 32)
    assert identity != remote_camera_identity(device, 'b' * 32)
    assert remote_camera_identity('/dev/video0', 'a' * 32) is None
    assert remote_camera_identity(device, '') is None


def _remote_context(tmp_path):
    settings = _settings(tmp_path)
    settings.camera.device = 'e3camera://home.local:8766'
    context = AppContext(settings)
    context.camera = SimpleNamespace(calibration_identity=lambda: 'a' * 64)
    _install_test_bed(context)
    return context


def test_verified_alias_roundtrip_preserves_mapping_and_persisted_binding(tmp_path):
    context = _remote_context(tmp_path)
    original = context.bed.calibration.to_dict()
    original_digest = context.bed_mapping_digest()
    assert context.bed_calibration_validity()['state'] == 'VALID'
    context.settings.camera.device = 'e3camera://100.64.0.7:8766'
    assert context.bed_calibration_validity()['state'] == 'VALID'
    assert context._bed_provenance() == original['provenance']
    assert context.bed.calibration.to_dict() == original
    assert context.bed_mapping_digest() == original_digest
    restarted = AppContext(context.settings)
    restarted.camera = SimpleNamespace(calibration_identity=lambda: 'a' * 64)
    assert restarted.bed_calibration_validity()['state'] == 'VALID'
    restarted.settings.camera.device = 'e3camera://home.local:8766'
    assert restarted.bed_calibration_validity()['state'] == 'VALID'


@pytest.mark.parametrize('identity', [None, 'b' * 64])
@pytest.mark.parametrize('endpoint', ['e3camera://home.local:8766', 'e3camera://100.64.0.7:8766'])
def test_bound_camera_requires_current_matching_identity(tmp_path, identity, endpoint):
    context = _remote_context(tmp_path)
    assert context.bed_calibration_validity()['state'] == 'VALID'
    context.camera.calibration_identity = lambda: identity
    context.settings.camera.device = endpoint
    assert context.bed_calibration_validity()['state'] == 'STALE'


def test_unknown_alias_and_changed_optics_are_not_enrolled(tmp_path):
    context = _remote_context(tmp_path)
    context.settings.camera.device = 'e3camera://100.64.0.7:8766'
    assert context.bed_calibration_validity()['state'] == 'STALE'
    context.settings.camera.device = 'e3camera://home.local:8766'
    assert context.bed_calibration_validity()['state'] == 'VALID'
    context.settings.camera.controls['focus_absolute'] = 80
    context.settings.camera.device = 'e3camera://100.64.0.7:8766'
    assert context.bed_calibration_validity()['state'] == 'STALE'


def test_corrupt_identity_binding_fails_closed(tmp_path):
    context = _remote_context(tmp_path)
    assert context.bed_calibration_validity()['state'] == 'VALID'
    path = next(context.camera_endpoint_bindings.root.glob('*.json'))
    path.write_text('{bad json')
    assert context.bed_calibration_validity()['state'] == 'STALE'
