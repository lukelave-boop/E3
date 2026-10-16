from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import pytest

_SOURCE = Path(__file__).parents[1] / "laser_aligner" / "web" / "app.js"
_COMPLETED = "E3_BROWSER_SCENARIO_COMPLETE"

_BOOTSTRAP = r"""
process.stderr.write(`E3_BROWSER_NODE_STARTED ${process.version}\n`);
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
"""

_PLACEMENT_SCENARIO = r"""
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
})();"""

_COMPLETION = r"""
let settled = false;
process.once('beforeExit', () => {
  if (!settled) {
    console.error('E3_BROWSER_INCOMPLETE: async scenario did not complete');
    process.exitCode = 1;
  }
});
try {
  Promise.resolve(vm.runInNewContext(source, context)).then(() => {
    settled = true;
    console.log('E3_BROWSER_SCENARIO_COMPLETE');
  }, error => {
    settled = true;
    console.error('E3_BROWSER_SCENARIO_FAILED', error);
    process.exitCode = 1;
  });
} catch (error) {
  settled = true;
  console.error('E3_BROWSER_SCENARIO_FAILED', error);
  process.exitCode = 1;
}
"""


@pytest.fixture(scope="module")
def node_binary():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Browser JavaScript behavior test requires an available Node runtime")
    return node


def _harness(scenario: str = _PLACEMENT_SCENARIO) -> str:
    return _BOOTSTRAP + "\nsource += " + json.dumps(scenario) + ";\n" + _COMPLETION


def _run_node(node: str, harness: str, *, timeout: float = 20) -> subprocess.CompletedProcess[str]:
    command = [node, "-e", harness, str(_SOURCE)]
    # Wait for the child itself, not for EOF from separate Windows pipe readers.
    # Files also preserve diagnostics when the child must be terminated.
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace") as stdout, tempfile.TemporaryFile(
        mode="w+", encoding="utf-8", errors="replace"
    ) as stderr:
        started = time.monotonic()
        process = subprocess.Popen(command, stdout=stdout, stderr=stderr, text=True)
        try:
            returncode = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            before_termination = process.poll()
            elapsed = time.monotonic() - started
            cleanup = _reap_node(process)
            stdout.seek(0)
            stderr.seek(0)
            raise AssertionError(
                f"Node browser harness exceeded {timeout}s after {elapsed:.3f}s; "
                f"runtime={node!r}; "
                f"PID={process.pid}; return code before termination={before_termination!r}; "
                f"after cleanup={process.returncode!r}; cleanup={cleanup}\n"
                f"stdout:\n{stdout.read()}\nstderr:\n{stderr.read()}"
            ) from None
        except BaseException:
            _reap_node(process)
            raise
        stdout.seek(0)
        stderr.seek(0)
        return subprocess.CompletedProcess(command, returncode, stdout.read(), stderr.read())


def _reap_node(process: subprocess.Popen) -> str:
    # A Popen context manager would perform an unbounded wait on exceptional exit.
    errors = []
    if process.poll() is None:
        try:
            process.kill()
        except OSError as error:
            errors.append(f"kill failed: {error}")
    try:
        process.wait(timeout=5)
    except (OSError, subprocess.TimeoutExpired) as error:
        errors.append(f"reap failed: {error}")
    return "; ".join(errors) or "reaped"


def test_browser_rejects_delayed_results_after_replacing_placement_capture(node_binary):
    # Allow bounded Node startup/scheduling time for the complete behavior scenario.
    result = _run_node(node_binary, _harness(), timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.splitlines() == [_COMPLETED], result.stdout + result.stderr


@pytest.mark.parametrize(
    ("scenario", "diagnostic"),
    [
        ("(async () => { await new Promise(() => {}); })();", "E3_BROWSER_INCOMPLETE"),
        ("throw new Error('synchronous fixture error');", "synchronous fixture error"),
        ("(async () => { throw new Error('asynchronous fixture error'); })();", "asynchronous fixture error"),
    ],
)
def test_browser_harness_rejects_incomplete_or_failed_scenarios(node_binary, scenario, diagnostic):
    result = _run_node(node_binary, _harness(scenario))
    assert result.returncode != 0
    assert _COMPLETED not in result.stdout
    assert "E3_BROWSER_NODE_STARTED" in result.stderr
    assert diagnostic in result.stderr


def test_browser_harness_requires_natural_exit_and_reaps_timed_out_child(node_binary, monkeypatch):
    processes = []
    real_popen = subprocess.Popen

    def record_process(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(subprocess, "Popen", record_process)
    # The scenario completes, but an actual Node handle keeps the child alive.
    # Completion must not force a successful exit or disable the same 20s bound.
    harness = "setInterval(() => {}, 1000);\n" + _harness("Promise.resolve();")
    with pytest.raises(AssertionError, match="return code before termination=None") as failure:
        _run_node(node_binary, harness)
    assert _COMPLETED in str(failure.value)
    assert "E3_BROWSER_NODE_STARTED" in str(failure.value)
    assert "cleanup=reaped" in str(failure.value)
    assert len(processes) == 1
    assert processes[0].returncode is not None
    assert processes[0].poll() == processes[0].returncode


def test_browser_harness_reports_cleanup_failure_without_unbounded_wait():
    waits = []

    class UnreapableChild:
        def poll(self):
            return None

        def kill(self):
            raise OSError("injected termination failure")

        def wait(self, *, timeout):
            waits.append(timeout)
            raise subprocess.TimeoutExpired("injected child", timeout)

    diagnostic = _reap_node(UnreapableChild())
    assert waits == [5]
    assert "injected termination failure" in diagnostic
    assert "reap failed" in diagnostic
