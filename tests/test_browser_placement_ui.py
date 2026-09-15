from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


def test_browser_rejects_delayed_results_after_replacing_placement_capture():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Browser JavaScript behavior test requires an available Node runtime")
    source = Path(__file__).parents[1] / "laser_aligner" / "web" / "app.js"
    harness = r"""
const fs = require('fs');
const vm = require('vm');
const assert = require('assert');
const handlers = {};
const elements = {};
const element = id => elements[id] ||= {
  value: '0', dataset: {}, style: {}, checked: false,
  classList: {add(){},remove(){},toggle(){}},
  addEventListener(name, fn) { handlers[id + ':' + name] = fn; },
  removeAttribute(name) { delete this[name]; },
};
const context = {
  assert, handlers, elements,
  document: {getElementById: element, querySelector: () => ({content:'token'}), querySelectorAll: () => []},
  window: {addEventListener(){}}, setTimeout(){}, clearTimeout(){},
  Blob, URL: {createObjectURL: () => 'blob:test', revokeObjectURL(){}},
};
let source = fs.readFileSync(process.argv[1], 'utf8').split('\nrestoreBedMappingPurpose();')[0];
source += `
(async () => {
  const notices = [];
  toast = message => notices.push(message);
  renderWorkpiecePolygon = () => {};
  renderDesignOverlay = () => {};
  placementPayload = () => ({});
  toolpathPayload = () => ({});
  refreshStatus = async () => {};
  state.svgText = '<svg/>';
  state.status = {material_placement:{enabled:true, surface:{measurement_id:'same-height'}}};
  state.placementCapture = {capture_id:'one'};
  let resolve;
  api = () => new Promise(done => { resolve = done; });
  const generation = handlers['generateGcodeButton:click']();
  invalidatePlacementReview();
  resolve({gcode:'obsolete', metadata:{material_surface:state.status.material_placement.surface}});
  await generation;
  assert.strictEqual(state.lastGcode, '');
  assert(notices.at(-1).includes('photograph changed'));
  const detection = handlers['detectWorkpieceButton:click']();
  invalidatePlacementReview();
  resolve({detected:true, center_mm:[99,99], material_surface:state.status.material_placement.surface});
  await detection;
  assert.strictEqual(elements.designX.value, '0');
  assert(notices.at(-1).includes('photograph changed'));
  state.lastGcode = 'reviewed-old-job';
  const capture = handlers['captureWorkspaceButton:click']();
  assert.strictEqual(state.lastGcode, '');
  resolve({capture_id:'new', material_surface:state.status.material_placement.surface});
  await capture;
  assert.strictEqual(state.placementCapture.capture_id, 'new');
  assert(elements.workspaceImage.src.includes('capture_id=new'));
  api = async () => ({gcode:'fresh', metadata:{material_surface:state.status.material_placement.surface,
    bounds_mm:[0,0,1,1],path_count:1,cut_length_mm:1,travel_length_mm:1}, download_url:'/new',filename:'new'});
  await handlers['generateGcodeButton:click']();
  assert.strictEqual(state.lastGcode, 'fresh');
  workArea = () => ({x_min:0,x_max:100,y_min:0,y_max:100});
  $('workspaceStage').getBoundingClientRect = () => ({width:100,height:100});
  state.dragging = {pointerId:1,startClientX:0,startClientY:0,startX:20.123456,startY:30.654321};
  handlers['designOverlay:pointermove']({pointerId:1,clientX:.123456789,clientY:.987654321});
  assert.strictEqual(Number(elements.designX.value), 20.123456 + .123456789);
  assert.strictEqual(Number(elements.designY.value), 30.654321 - .987654321);
  state.rotating = {pointerId:1,centerClientX:0,centerClientY:0,baseAngle:0};
  const radians = -12.3456789 * Math.PI / 180;
  handlers['designRotationHandle:pointermove']({pointerId:1,clientX:Math.cos(radians),clientY:Math.sin(radians),shiftKey:true});
  assert(Math.abs(Number(elements.designRotation.value) - 12.3456789) < 1e-10);
  elements.lockAspect = $('lockAspect');
  elements.lockAspect.checked = true;
  state.svgAspect = 1.23456789;
  elements.designWidth.value = '76.54321987';
  handlers['designWidth:input']();
  assert.strictEqual(Number(elements.designHeight.value), 76.54321987 / 1.23456789);
  api = async () => ({intrinsic_width_mm:88.123456789,intrinsic_height_mm:44.987654321,warnings:[]});
  await handlers['svgFileInput:change']({target:{files:[{name:'exact.svg',text:async () => '<svg/>'}]}});
  assert.strictEqual(Number(elements.designWidth.value), 88.123456789);
  assert.strictEqual(Number(elements.designHeight.value), 44.987654321);
})();`;
Promise.resolve(vm.runInNewContext(source, context)).catch(error => { console.error(error); process.exitCode = 1; });
"""
    result = subprocess.run([node, "-e", harness, str(source)], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
