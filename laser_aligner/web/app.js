'use strict';

const state = {
  status: null,
  svgText: null,
  svgName: null,
  svgAspect: 1,
  overlayUrl: null,
  lastGcode: '',
  lastBounds: null,
  selectedBedPoint: null,
  pendingBedPixel: null,
  bedImage: null,
  refreshBusy: false,
  dragging: null,
  rotating: null,
};

const $ = (id) => document.getElementById(id);
const fmt = (value, digits = 2) => Number.isFinite(Number(value)) ? Number(value).toFixed(digits) : '—';

async function api(path, method = 'GET', payload = null) {
  const options = { method, headers: {} };
  if (payload !== null) {
    options.headers['Content-Type'] = 'application/json';
    options.body = JSON.stringify(payload);
  }
  const response = await fetch(path, options);
  let data;
  try {
    data = await response.json();
  } catch {
    throw new Error(`Server returned ${response.status} without JSON`);
  }
  if (!response.ok || data.ok === false) {
    throw new Error(data.error || `Request failed with status ${response.status}`);
  }
  return data;
}

let toastTimer = null;
function toast(message, error = false) {
  const element = $('toast');
  element.textContent = message;
  element.classList.toggle('error', error);
  element.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => element.classList.remove('show'), 4200);
}

function navigate(section) {
  document.querySelectorAll('.section').forEach((element) => element.classList.toggle('active', element.id === `section-${section}`));
  document.querySelectorAll('.nav-button').forEach((element) => element.classList.toggle('active', element.dataset.section === section));
  if (section === 'bed') loadBedImage();
  if (section === 'workspace') refreshWorkspaceImage(false);
}

document.querySelectorAll('.nav-button').forEach((button) => button.addEventListener('click', () => navigate(button.dataset.section)));

function setBadge(element, label, condition, warning = false) {
  element.textContent = label;
  element.classList.remove('good', 'warning', 'bad');
  element.classList.add(condition ? 'good' : warning ? 'warning' : 'bad');
}

function setInitialValue(element, value) {
  if (!element.dataset.initialized) {
    element.value = value;
    element.dataset.initialized = '1';
  }
}

function updateStatusView(data) {
  state.status = data;
  const settings = data.settings;
  const camera = data.camera;
  const lens = data.lens;
  const bed = data.bed;
  const machine = data.machine;
  const simulation = settings.app.simulation;

  setBadge($('modeBadge'), simulation ? 'Simulation mode' : (machine.hardware_enabled ? 'Hardware mode' : 'Hardware locked'), simulation || machine.hardware_enabled, !simulation);
  setBadge($('cameraBadge'), camera.connected ? 'Camera online' : 'Camera offline', camera.connected);
  setBadge($('machineBadge'), machine.connected ? `${machine.protocol} connected` : 'Controller offline', machine.connected, machine.backend === 'simulator');

  $('simulationPanel').classList.toggle('hidden', !simulation);
  $('syntheticControls').classList.toggle('hidden', !simulation);
  $('overviewCamera').textContent = camera.connected ? 'Ready' : 'Offline';
  $('overviewCameraDetail').textContent = camera.last_error || `${camera.width} × ${camera.height} at ${fmt(camera.fps, 1)} fps`;
  $('overviewLens').textContent = lens.calibrated ? 'Calibrated' : 'Not calibrated';
  $('overviewLensDetail').textContent = lens.calibrated ? `${lens.model.images_used} images · ${fmt(lens.model.mean_reprojection_error, 3)} px mean` : `${lens.usable_image_count}/${lens.pattern.minimum_images} usable captures`;
  $('overviewBed').textContent = bed.calibrated ? 'Mapped' : 'Not mapped';
  $('overviewBedDetail').textContent = bed.calibrated ? `${fmt(bed.calibration.rms_error_mm, 3)} mm RMS · ${bed.calibration.inlier_count}/${bed.calibration.point_count} inliers` : `${bed.points.length}/${bed.minimum_points} point pairs`;
  $('overviewMachine').textContent = machine.connected ? (machine.armed ? 'Armed' : 'Connected') : 'Disconnected';
  $('overviewMachineDetail').textContent = `${machine.backend} · motion ${machine.allow_motion || machine.backend === 'simulator' ? 'enabled' : 'blocked'}`;

  const area = settings.machine.work_area;
  $('workAreaX').textContent = `${fmt(area.x_min)} to ${fmt(area.x_max)} mm`;
  $('workAreaY').textContent = `${fmt(area.y_min)} to ${fmt(area.y_max)} mm`;
  const photo = settings.machine.photo_position;
  $('photoPosition').textContent = `X${fmt(photo.x)} Y${fmt(photo.y)}${photo.z === null ? '' : ` Z${fmt(photo.z)}`}`;
  $('laserScale').textContent = `S0–${settings.laser.power_max}`;

  $('cameraDevice').textContent = camera.device;
  $('cameraResolution').textContent = `${camera.width} × ${camera.height}`;
  $('cameraFps').textContent = `${fmt(camera.fps, 1)} fps`;
  $('cameraFrames').textContent = camera.frames_read;

  $('lensPatternDescription').textContent = `${lens.pattern.columns} × ${lens.pattern.rows} inner corners, ${lens.pattern.square_size_mm} mm squares; minimum ${lens.pattern.minimum_images} useful views.`;
  $('lensImageCount').textContent = lens.image_count;
  $('lensUsableCount').textContent = lens.usable_image_count;
  $('lensRms').textContent = lens.calibrated ? `${fmt(lens.model.rms_error, 4)} px` : '—';
  $('lensMeanError').textContent = lens.calibrated ? `${fmt(lens.model.mean_reprojection_error, 4)} px` : '—';

  renderBedStatus(bed);
  updateMachineView(machine);

  setInitialValue($('designX'), (area.x_min + area.x_max) / 2);
  setInitialValue($('designY'), (area.y_min + area.y_max) / 2);
  setInitialValue($('designWidth'), 50);
  setInitialValue($('designHeight'), 50);
  setInitialValue($('designPower'), settings.laser.default_power);
  $('designPower').max = settings.laser.power_max;
  setInitialValue($('designFeed'), settings.laser.engrave_feed_mm_min);
  setInitialValue($('travelFeed'), settings.laser.travel_feed_mm_min);
  setInitialValue($('serialPort'), settings.machine.port);
  setInitialValue($('serialBaud'), settings.machine.baudrate);
  if (!$('serialProtocol').dataset.initialized) {
    $('serialProtocol').value = settings.machine.protocol;
    $('serialProtocol').dataset.initialized = '1';
  }
  $('requiredArmPhrase').textContent = machine.arm_phrase;
  renderDesignOverlay();
}

async function refreshStatus(showError = false) {
  if (state.refreshBusy) return;
  state.refreshBusy = true;
  try {
    const data = await api('/api/status');
    updateStatusView(data);
  } catch (error) {
    if (showError) toast(error.message, true);
  } finally {
    state.refreshBusy = false;
  }
}

function renderBedStatus(bed) {
  $('bedPointCount').textContent = bed.points.length;
  $('bedInliers').textContent = bed.calibrated ? `${bed.calibration.inlier_count}/${bed.calibration.point_count}` : '—';
  $('bedRms').textContent = bed.calibrated ? `${fmt(bed.calibration.rms_error_mm, 4)} mm` : '—';
  $('bedMaxError').textContent = bed.calibrated ? `${fmt(bed.calibration.max_error_mm, 4)} mm` : '—';
  const body = $('bedPointsBody');
  body.replaceChildren();
  bed.points.forEach((point, index) => {
    const row = document.createElement('tr');
    row.classList.toggle('selected', state.selectedBedPoint === index);
    const values = ['', point.label || `Point ${index + 1}`, fmt(point.image_x), fmt(point.image_y), fmt(point.machine_x), fmt(point.machine_y)];
    values.forEach((value, column) => {
      const cell = document.createElement('td');
      if (column === 0) {
        const radio = document.createElement('input');
        radio.type = 'radio';
        radio.name = 'bedPointSelection';
        radio.checked = state.selectedBedPoint === index;
        cell.appendChild(radio);
      } else {
        cell.textContent = value;
      }
      row.appendChild(cell);
    });
    row.addEventListener('click', () => {
      state.selectedBedPoint = index;
      renderBedStatus(state.status.bed);
      drawBedCanvas();
    });
    body.appendChild(row);
  });
  drawBedCanvas();
}

async function loadBedImage() {
  const image = new Image();
  image.onload = () => {
    state.bedImage = image;
    const canvas = $('bedCalibrationCanvas');
    canvas.width = image.naturalWidth;
    canvas.height = image.naturalHeight;
    drawBedCanvas();
  };
  image.onerror = () => toast('Could not load the bed reference image.', true);
  image.src = `/api/calibration/bed/frame.jpg?t=${Date.now()}`;
}

function drawCross(context, x, y, color, size = 14) {
  context.strokeStyle = color;
  context.lineWidth = 3;
  context.beginPath();
  context.moveTo(x - size, y); context.lineTo(x + size, y);
  context.moveTo(x, y - size); context.lineTo(x, y + size);
  context.stroke();
}

function drawBedCanvas() {
  const canvas = $('bedCalibrationCanvas');
  if (!state.bedImage || !canvas.width) return;
  const context = canvas.getContext('2d');
  context.clearRect(0, 0, canvas.width, canvas.height);
  context.drawImage(state.bedImage, 0, 0, canvas.width, canvas.height);
  const points = state.status?.bed?.points || [];
  points.forEach((point, index) => {
    drawCross(context, point.image_x, point.image_y, index === state.selectedBedPoint ? '#e7b55c' : '#4fc3a1', 12);
    context.fillStyle = '#ffffff';
    context.font = '18px sans-serif';
    context.fillText(String(index + 1), point.image_x + 14, point.image_y - 12);
  });
  if (state.pendingBedPixel) {
    drawCross(context, state.pendingBedPixel.x, state.pendingBedPixel.y, '#ef6a72', 16);
  }
}

$('bedCalibrationCanvas').addEventListener('click', (event) => {
  const canvas = event.currentTarget;
  const rect = canvas.getBoundingClientRect();
  const x = (event.clientX - rect.left) * canvas.width / rect.width;
  const y = (event.clientY - rect.top) * canvas.height / rect.height;
  state.pendingBedPixel = { x, y };
  $('bedImageX').value = x.toFixed(2);
  $('bedImageY').value = y.toFixed(2);
  drawBedCanvas();
});

function updateMachineView(machine) {
  $('controllerState').value = machine.connected ? `${machine.protocol}${machine.armed ? ' · ARMED' : ''}` : 'Disconnected';
  $('controllerLog').textContent = machine.log.length ? machine.log.join('\n') : 'No controller messages.';
  const job = machine.job;
  $('jobProgress').textContent = job.running ? `${Math.round(job.progress * 100)}% · ${job.completed_lines}/${job.total_lines} lines` : (job.error ? `Stopped: ${job.error}` : 'Idle');
}

function workArea() {
  return state.status?.settings?.machine?.work_area || { x_min: 0, x_max: 220, y_min: 0, y_max: 220 };
}

function renderDesignOverlay() {
  const overlay = $('designOverlay');
  const rotationHandle = $('designRotationHandle');
  if (!state.svgText || !state.status) {
    overlay.style.display = 'none';
    rotationHandle.style.display = 'none';
    return;
  }
  const area = workArea();
  const x = Number($('designX').value);
  const y = Number($('designY').value);
  const width = Number($('designWidth').value);
  const height = Number($('designHeight').value);
  const rotation = Number($('designRotation').value);
  const xPercent = (x - area.x_min) / (area.x_max - area.x_min) * 100;
  const yPercent = (area.y_max - y) / (area.y_max - area.y_min) * 100;
  overlay.style.display = 'block';
  overlay.style.left = `${xPercent}%`;
  overlay.style.top = `${yPercent}%`;
  overlay.style.width = `${width / (area.x_max - area.x_min) * 100}%`;
  overlay.style.height = `${height / (area.y_max - area.y_min) * 100}%`;
  overlay.style.transform = `translate(-50%, -50%) rotate(${-rotation}deg)`;

  const stageRect = $('workspaceStage').getBoundingClientRect();
  if (stageRect.width > 0 && stageRect.height > 0) {
    const centerX = xPercent / 100 * stageRect.width;
    const centerY = yPercent / 100 * stageRect.height;
    const halfWidth = width / (area.x_max - area.x_min) * stageRect.width / 2;
    const halfHeight = height / (area.y_max - area.y_min) * stageRect.height / 2;
    const angle = -rotation * Math.PI / 180;
    const cornerX = halfWidth * Math.cos(angle) + halfHeight * Math.sin(angle);
    const cornerY = halfWidth * Math.sin(angle) - halfHeight * Math.cos(angle);
    const length = Math.hypot(cornerX, cornerY) || 1;
    const offset = 18;
    rotationHandle.style.display = 'block';
    rotationHandle.style.left = `${centerX + cornerX + cornerX / length * offset}px`;
    rotationHandle.style.top = `${centerY + cornerY + cornerY / length * offset}px`;
  }
}

function renderWorkpiecePolygon(polygonMm) {
  const polygon = $('workpiecePolygon');
  if (!polygonMm || !polygonMm.length) {
    polygon.setAttribute('points', '');
    return;
  }
  const area = workArea();
  const points = polygonMm.map(([x, y]) => {
    const px = (x - area.x_min) / (area.x_max - area.x_min) * 100;
    const py = (area.y_max - y) / (area.y_max - area.y_min) * 100;
    return `${px},${py}`;
  });
  polygon.setAttribute('points', points.join(' '));
}

async function refreshWorkspaceImage(refresh) {
  const image = $('workspaceImage');
  const suffix = refresh ? `?refresh=1&t=${Date.now()}` : `?t=${Date.now()}`;
  image.src = `/api/workspace/frame.jpg${suffix}`;
}

$('workspaceImage').addEventListener('error', () => {
  toast('Workspace image is unavailable until bed mapping is solved.', true);
});

$('designOverlay').addEventListener('pointerdown', (event) => {
  if (!state.svgText) return;
  event.preventDefault();
  const overlay = event.currentTarget;
  overlay.setPointerCapture(event.pointerId);
  overlay.classList.add('dragging');
  state.dragging = {
    pointerId: event.pointerId,
    startClientX: event.clientX,
    startClientY: event.clientY,
    startX: Number($('designX').value),
    startY: Number($('designY').value),
  };
});

$('designOverlay').addEventListener('pointermove', (event) => {
  if (!state.dragging || state.dragging.pointerId !== event.pointerId) return;
  const stageRect = $('workspaceStage').getBoundingClientRect();
  const area = workArea();
  const dx = (event.clientX - state.dragging.startClientX) / stageRect.width * (area.x_max - area.x_min);
  const dy = -(event.clientY - state.dragging.startClientY) / stageRect.height * (area.y_max - area.y_min);
  $('designX').value = (state.dragging.startX + dx).toFixed(2);
  $('designY').value = (state.dragging.startY + dy).toFixed(2);
  renderDesignOverlay();
});

function finishDrag(event) {
  if (!state.dragging || state.dragging.pointerId !== event.pointerId) return;
  $('designOverlay').classList.remove('dragging');
  state.dragging = null;
}
$('designOverlay').addEventListener('pointerup', finishDrag);
$('designOverlay').addEventListener('pointercancel', finishDrag);


$('designRotationHandle').addEventListener('pointerdown', (event) => {
  if (!state.svgText) return;
  event.preventDefault();
  event.stopPropagation();
  const handle = event.currentTarget;
  const stageRect = $('workspaceStage').getBoundingClientRect();
  const overlayRect = $('designOverlay').getBoundingClientRect();
  handle.setPointerCapture(event.pointerId);
  handle.classList.add('dragging');
  state.rotating = {
    pointerId: event.pointerId,
    centerClientX: overlayRect.left + overlayRect.width / 2,
    centerClientY: overlayRect.top + overlayRect.height / 2,
    baseAngle: Math.atan2(-Number($('designHeight').value) / (workArea().y_max - workArea().y_min) * stageRect.height,
                          Number($('designWidth').value) / (workArea().x_max - workArea().x_min) * stageRect.width),
  };
});

$('designRotationHandle').addEventListener('pointermove', (event) => {
  if (!state.rotating || state.rotating.pointerId !== event.pointerId) return;
  const pointerAngle = Math.atan2(event.clientY - state.rotating.centerClientY,
                                  event.clientX - state.rotating.centerClientX);
  let rotation = (state.rotating.baseAngle - pointerAngle) * 180 / Math.PI;
  rotation = ((rotation + 180) % 360 + 360) % 360 - 180;
  if (event.shiftKey) rotation = Math.round(rotation / 15) * 15;
  $('designRotation').value = rotation.toFixed(event.shiftKey ? 0 : 1);
  renderDesignOverlay();
});

function finishRotation(event) {
  if (!state.rotating || state.rotating.pointerId !== event.pointerId) return;
  $('designRotationHandle').classList.remove('dragging');
  state.rotating = null;
}
$('designRotationHandle').addEventListener('pointerup', finishRotation);
$('designRotationHandle').addEventListener('pointercancel', finishRotation);
window.addEventListener('resize', renderDesignOverlay);

let aspectUpdate = false;
$('designWidth').addEventListener('input', () => {
  if ($('lockAspect').checked && !aspectUpdate && state.svgAspect > 0) {
    aspectUpdate = true;
    $('designHeight').value = (Number($('designWidth').value) / state.svgAspect).toFixed(2);
    aspectUpdate = false;
  }
  renderDesignOverlay();
});
$('designHeight').addEventListener('input', () => {
  if ($('lockAspect').checked && !aspectUpdate && state.svgAspect > 0) {
    aspectUpdate = true;
    $('designWidth').value = (Number($('designHeight').value) * state.svgAspect).toFixed(2);
    aspectUpdate = false;
  }
  renderDesignOverlay();
});
['designX', 'designY', 'designRotation'].forEach((id) => $(id).addEventListener('input', renderDesignOverlay));

$('svgFileInput').addEventListener('change', async (event) => {
  const file = event.target.files[0];
  if (!file) return;
  try {
    const text = await file.text();
    const analysis = await api('/api/design/analyze', 'POST', { svg: text });
    state.svgText = text;
    state.svgName = file.name;
    const width = Number(analysis.intrinsic_width_mm) || 50;
    const height = Number(analysis.intrinsic_height_mm) || 50;
    state.svgAspect = width / height;
    $('designWidth').value = width.toFixed(2);
    $('designHeight').value = height.toFixed(2);
    if (state.overlayUrl) URL.revokeObjectURL(state.overlayUrl);
    state.overlayUrl = URL.createObjectURL(new Blob([text], { type: 'image/svg+xml' }));
    $('designOverlay').src = state.overlayUrl;
    const warningText = analysis.warnings.length ? ` · ${analysis.warnings.join('; ')}` : '';
    $('svgSummary').textContent = `${file.name}: ${analysis.path_count} paths, ${analysis.point_count} flattened points, ${fmt(width)} × ${fmt(height)} mm${warningText}`;
    renderDesignOverlay();
    toast('SVG loaded and analyzed.');
  } catch (error) {
    toast(error.message, true);
  }
});

function placementPayload() {
  return {
    center_x_mm: Number($('designX').value),
    center_y_mm: Number($('designY').value),
    width_mm: Number($('designWidth').value),
    height_mm: Number($('designHeight').value),
    rotation_deg: Number($('designRotation').value),
  };
}

function toolpathPayload() {
  return {
    power: Number($('designPower').value),
    engrave_feed_mm_min: Number($('designFeed').value),
    travel_feed_mm_min: Number($('travelFeed').value),
    optimize_order: true,
  };
}

function showGenerated(result) {
  state.lastGcode = result.gcode;
  state.lastBounds = result.metadata.bounds_mm;
  $('gcodeOutput').value = result.gcode;
  $('gcodeMetrics').textContent = `${result.metadata.path_count} paths · ${fmt(result.metadata.cut_length_mm, 1)} mm cut · ${fmt(result.metadata.travel_length_mm, 1)} mm travel`;
  const link = $('downloadGcodeLink');
  link.href = result.download_url;
  link.textContent = `Download ${result.filename}`;
  link.classList.remove('hidden');
}

$('generateGcodeButton').addEventListener('click', async () => {
  if (!state.svgText) return toast('Load an SVG first.', true);
  try {
    const result = await api('/api/design/gcode', 'POST', {
      svg: state.svgText,
      name: state.svgName,
      placement: placementPayload(),
      toolpath: toolpathPayload(),
    });
    showGenerated(result);
    toast('G-code generated and checked against the configured work area.');
  } catch (error) {
    toast(error.message, true);
  }
});

$('generateFrameButton').addEventListener('click', async () => {
  if (!state.lastBounds) return toast('Generate the design G-code first so its bounds are known.', true);
  try {
    const result = await api('/api/design/frame', 'POST', {
      bounds_mm: state.lastBounds,
      laser_enabled: false,
      feed_mm_min: Number($('travelFeed').value),
    });
    showGenerated(result);
    toast('Dry-motion framing program generated with no laser-enable command.');
  } catch (error) {
    toast(error.message, true);
  }
});

$('detectWorkpieceButton').addEventListener('click', async () => {
  try {
    const result = await api('/api/vision/workpiece', 'POST', {});
    if (!result.detected) {
      renderWorkpiecePolygon(null);
      return toast('No strong rectangular workpiece edge was detected.', true);
    }
    renderWorkpiecePolygon(result.polygon_mm);
    $('designX').value = result.center_mm[0].toFixed(2);
    $('designY').value = result.center_mm[1].toFixed(2);
    renderDesignOverlay();
    toast(`Detected candidate workpiece near X${fmt(result.center_mm[0])} Y${fmt(result.center_mm[1])}.`);
  } catch (error) {
    toast(error.message, true);
  }
});

$('refreshStatusButton').addEventListener('click', () => refreshStatus(true));
$('captureCameraButton').addEventListener('click', async () => {
  try { const result = await api('/api/camera/capture', 'POST', {}); toast(`Saved ${result.filename}`); }
  catch (error) { toast(error.message, true); }
});
$('applyCameraControlsButton').addEventListener('click', async () => {
  try {
    const result = await api('/api/camera/controls/apply', 'POST', {});
    const applied = Object.keys(result.applied).length;
    const skipped = Object.keys(result.skipped).length;
    toast(`Applied ${applied} camera controls; skipped ${skipped}.`);
  } catch (error) { toast(error.message, true); }
});
$('setSyntheticSceneButton').addEventListener('click', async () => {
  try {
    await api('/api/camera/synthetic-scene', 'POST', { scene: $('syntheticScene').value });
    $('lensPreview').src = `/api/camera/frame.jpg?undistort=0&t=${Date.now()}`;
    toast('Synthetic camera scene changed.');
  } catch (error) { toast(error.message, true); }
});

$('captureLensButton').addEventListener('click', async () => {
  try {
    const result = await api('/api/calibration/lens/capture', 'POST', {});
    toast(result.found ? `Captured ${result.corner_count} corners.` : 'Image saved, but the checkerboard was not detected.', !result.found);
    $('lensPreview').src = `/api/camera/frame.jpg?undistort=0&t=${Date.now()}`;
    await refreshStatus();
  } catch (error) { toast(error.message, true); }
});
$('solveLensButton').addEventListener('click', async () => {
  try { await api('/api/calibration/lens/solve', 'POST', {}); toast('Lens calibration solved and saved.'); await refreshStatus(); }
  catch (error) { toast(error.message, true); }
});
$('clearLensButton').addEventListener('click', async () => {
  try { await api('/api/calibration/lens/clear', 'POST', { delete_images: false }); toast('Lens result cleared; captured images retained.'); await refreshStatus(); }
  catch (error) { toast(error.message, true); }
});

$('captureBedButton').addEventListener('click', async () => {
  try { await api('/api/calibration/bed/capture', 'POST', {}); await loadBedImage(); toast('Fixed-pose bed image captured.'); }
  catch (error) { toast(error.message, true); }
});

async function parkAtPhotoPose() {
  try {
    const result = await api('/api/machine/photo-position', 'POST', {});
    const position = result.position;
    toast(`Machine is idle at camera pose X${fmt(position.x)} Y${fmt(position.y)}. Capture the bed image now.`);
    await refreshStatus();
  } catch (error) {
    toast(error.message, true);
  }
}
$('parkPhotoButton').addEventListener('click', parkAtPhotoPose);
$('parkPhotoMachineButton').addEventListener('click', parkAtPhotoPose);
$('addBedPointButton').addEventListener('click', async () => {
  if (!state.pendingBedPixel) return toast('Click a calibration mark in the image first.', true);
  try {
    await api('/api/calibration/bed/point', 'POST', {
      image_x: state.pendingBedPixel.x,
      image_y: state.pendingBedPixel.y,
      machine_x: Number($('bedMachineX').value),
      machine_y: Number($('bedMachineY').value),
      label: $('bedPointLabel').value,
    });
    state.pendingBedPixel = null;
    $('bedImageX').value = '';
    $('bedImageY').value = '';
    await refreshStatus();
    toast('Calibration point added.');
  } catch (error) { toast(error.message, true); }
});
$('deleteBedPointButton').addEventListener('click', async () => {
  if (state.selectedBedPoint === null) return toast('Select a point row first.', true);
  try {
    await api('/api/calibration/bed/delete', 'POST', { index: state.selectedBedPoint });
    state.selectedBedPoint = null;
    await refreshStatus();
    toast('Calibration point deleted.');
  } catch (error) { toast(error.message, true); }
});
$('clearBedButton').addEventListener('click', async () => {
  try {
    await api('/api/calibration/bed/clear', 'POST', {});
    state.selectedBedPoint = null;
    state.pendingBedPixel = null;
    await refreshStatus();
    toast('Bed points and solved mapping cleared.');
  } catch (error) { toast(error.message, true); }
});
$('solveBedButton').addEventListener('click', async () => {
  try {
    await api('/api/calibration/bed/solve', 'POST', {});
    await refreshStatus();
    await refreshWorkspaceImage(true);
    toast('Bed mapping solved and saved.');
  } catch (error) { toast(error.message, true); }
});
$('captureWorkspaceButton').addEventListener('click', async () => {
  try { await api('/api/workspace/capture', 'POST', {}); await refreshWorkspaceImage(false); toast('Rectified workspace image refreshed.'); }
  catch (error) { toast(error.message, true); }
});

$('connectMachineButton').addEventListener('click', async () => {
  try {
    await api('/api/machine/connect', 'POST', {
      port: $('serialPort').value,
      baudrate: Number($('serialBaud').value),
      protocol: $('serialProtocol').value,
    });
    await refreshStatus();
    toast('Controller connected.');
  } catch (error) { toast(error.message, true); }
});
$('disconnectMachineButton').addEventListener('click', async () => {
  try { await api('/api/machine/disconnect', 'POST', {}); await refreshStatus(); toast('Controller disconnected.'); }
  catch (error) { toast(error.message, true); }
});
$('identityButton').addEventListener('click', async () => {
  try {
    const command = state.status?.machine?.protocol === 'marlin' ? 'M115' : '$I';
    const result = await api('/api/machine/command', 'POST', { command });
    toast(result.responses.join(' · ') || 'Command acknowledged.');
    await refreshStatus();
  } catch (error) { toast(error.message, true); }
});
$('sendCommandButton').addEventListener('click', async () => {
  try {
    const result = await api('/api/machine/command', 'POST', { command: $('manualCommand').value });
    toast(result.responses.join(' · ') || 'Command acknowledged.');
    await refreshStatus();
  } catch (error) { toast(error.message, true); }
});
$('armMachineButton').addEventListener('click', async () => {
  try { await api('/api/machine/arm', 'POST', { phrase: $('armPhrase').value }); await refreshStatus(); toast('Laser control armed temporarily.'); }
  catch (error) { toast(error.message, true); }
});
$('disarmMachineButton').addEventListener('click', async () => {
  try { await api('/api/machine/disarm', 'POST', {}); await refreshStatus(); toast('Laser control disarmed; M5 sent.'); }
  catch (error) { toast(error.message, true); }
});
$('runGeneratedButton').addEventListener('click', async () => {
  if (!state.lastGcode) return toast('Generate or load G-code first.', true);
  if (!$('runConfirmation').checked) return toast('Complete the run confirmation before sending a job.', true);
  try {
    await api('/api/machine/run', 'POST', { gcode: state.lastGcode, name: state.svgName || 'generated.gcode' });
    toast('Controller job started. Keep the machine attended.');
    await refreshStatus();
  } catch (error) { toast(error.message, true); }
});
$('emergencyStopButton').addEventListener('click', async () => {
  try { await api('/api/machine/stop', 'POST', { emergency: true }); await refreshStatus(); toast('Software stop sent. Use the hardware E-stop for a real emergency.'); }
  catch (error) { toast(error.message, true); }
});

refreshStatus(true).then(() => {
  loadBedImage();
  refreshWorkspaceImage(false);
});
setInterval(() => refreshStatus(false), 2000);
