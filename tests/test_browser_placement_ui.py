from __future__ import annotations

import ctypes
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

_SOURCE = Path(__file__).parents[1] / "laser_aligner" / "web" / "app.js"
_COMPLETED = "E3_BROWSER_SCENARIO_COMPLETE"

_BOOTSTRAP = r"""
process.stderr.write(`E3_BROWSER_NODE_STARTED ${process.version}\n`);
let started;
// File-backed stderr is synchronous; use writeSync once fs is loaded.
let writeStage = message => process.stderr.write(message);
const stage = (name, includeResources = false) => {
  try {
    const now = process.hrtime.bigint();
    started ??= now;
    const cpu = process.cpuUsage();
    writeStage('E3_BROWSER_STAGE ' + JSON.stringify({
      name, elapsed_ms: Number(now - started) / 1e6,
      cpu_user_us: cpu.user, cpu_system_us: cpu.system,
      active_resources: includeResources ? process.getActiveResourcesInfo() : undefined,
    }) + '\n');
  } catch {} // Diagnostics must not change scenario completion or failure.
};
stage('before_fs');
const fs = require('fs');
writeStage = message => fs.writeSync(2, message);
stage('after_fs');
stage('before_vm_module');
const vm = require('vm');
stage('after_vm_module');
stage('before_assert_module');
const assert = require('assert');
stage('after_assert_module');
const handlers = {};
const elements = {};
const element = id => elements[id] ||= {
  value: '0', dataset: {}, style: {}, checked: false,
  classList: {add(){},remove(){},toggle(){}},
  addEventListener(name, fn) { handlers[id + ':' + name] = fn; },
  removeAttribute(name) { delete this[name]; },
};
stage('before_context');
const context = {
  assert, handlers, elements, stage,
  document: {getElementById: element, querySelector: () => ({content:'token'}), querySelectorAll: () => []},
  window: {addEventListener(){}}, setTimeout(){}, clearTimeout(){},
  Blob, URL: {createObjectURL: () => 'blob:test', revokeObjectURL(){}},
};
stage('after_context');
stage('before_source_read');
let source = fs.readFileSync(process.argv[1], 'utf8').split('\nrestoreBedMappingPurpose();')[0];
stage('after_source_read');
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
  stage('before_generation');
  await generation;
  stage('after_generation');
  assert.strictEqual(state.lastGcode, '');
  assert(notices.at(-1).includes('photograph changed'));
  const detection = handlers['detectWorkpieceButton:click']();
  invalidatePlacementReview();
  resolve({detected:true, center_mm:[99,99], material_surface:state.status.material_placement.surface});
  stage('before_detection');
  await detection;
  stage('after_detection');
  assert.strictEqual(elements.designX.value, '0');
  assert(notices.at(-1).includes('photograph changed'));
  state.lastGcode = 'reviewed-old-job';
  const capture = handlers['captureWorkspaceButton:click']();
  assert.strictEqual(state.lastGcode, '');
  resolve({capture_id:'new', material_surface:state.status.material_placement.surface});
  stage('before_capture');
  await capture;
  stage('after_capture');
  assert.strictEqual(state.placementCapture.capture_id, 'new');
  assert(elements.workspaceImage.src.includes('capture_id=new'));
  api = async () => ({gcode:'fresh', metadata:{material_surface:state.status.material_placement.surface,
    bounds_mm:[0,0,1,1],path_count:1,cut_length_mm:1,travel_length_mm:1}, download_url:'/new',filename:'new'});
  stage('before_fresh_generation');
  await handlers['generateGcodeButton:click']();
  stage('after_fresh_generation');
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
  stage('before_svg_analysis');
  await handlers['svgFileInput:change']({target:{files:[{name:'exact.svg',text:async () => '<svg/>'}]}});
  stage('after_svg_analysis');
  assert.strictEqual(Number(elements.designWidth.value), 88.123456789);
  assert.strictEqual(Number(elements.designHeight.value), 44.987654321);
})();"""

_COMPLETION = r"""
let settled = false;
process.once('beforeExit', () => {
  stage('before_exit', true);
  if (!settled) {
    console.error('E3_BROWSER_INCOMPLETE: async scenario did not complete');
    process.exitCode = 1;
  }
});
try {
  stage('before_vm');
  const result = vm.runInNewContext(source, context);
  stage('after_vm');
  Promise.resolve(result).then(() => {
    settled = true;
    stage('before_completion');
    console.log('E3_BROWSER_SCENARIO_COMPLETE');
    stage('after_completion');
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
            cpu_seconds = _child_cpu_seconds(process)
            cleanup = _reap_node(process)
            stdout.seek(0)
            stderr.seek(0)
            raise AssertionError(
                f"Node browser harness exceeded {timeout}s after {elapsed:.3f}s; "
                f"runtime={node!r}; "
                f"PID={process.pid}; return code before termination={before_termination!r}; "
                f"CPU seconds before termination={cpu_seconds!r}; "
                f"after cleanup={process.returncode!r}; cleanup={cleanup}\n"
                f"stdout:\n{stdout.read()}\nstderr:\n{stderr.read()}"
            ) from None
        except BaseException:
            _reap_node(process)
            raise
        stdout.seek(0)
        stderr.seek(0)
        return subprocess.CompletedProcess(command, returncode, stdout.read(), stderr.read())


def _child_cpu_seconds(process: subprocess.Popen) -> float | None:
    # Read the actual child handle before termination; unavailable diagnostics
    # must never prevent cleanup or replace the original timeout failure.
    if sys.platform != "win32":
        return None
    try:
        from ctypes import wintypes

        get_times = ctypes.WinDLL("kernel32", use_last_error=True).GetProcessTimes
        get_times.argtypes = [wintypes.HANDLE, *([ctypes.POINTER(wintypes.FILETIME)] * 4)]
        get_times.restype = wintypes.BOOL
        times = [wintypes.FILETIME() for _ in range(4)]
        if not get_times(wintypes.HANDLE(int(process._handle)), *(ctypes.byref(value) for value in times)):
            return None
        return sum((value.dwHighDateTime << 32) | value.dwLowDateTime for value in times[2:]) / 10_000_000
    except Exception:
        return None


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
    stages = [json.loads(line.removeprefix("E3_BROWSER_STAGE "))
              for line in result.stderr.splitlines() if line.startswith("E3_BROWSER_STAGE ")]
    assert [item["name"] for item in stages] == [
        "before_fs", "after_fs", "before_vm_module", "after_vm_module",
        "before_assert_module", "after_assert_module", "before_context", "after_context",
        "before_source_read", "after_source_read", "before_vm", "before_generation", "after_vm",
        "after_generation", "before_detection", "after_detection", "before_capture", "after_capture",
        "before_fresh_generation", "after_fresh_generation", "before_svg_analysis", "after_svg_analysis",
        "before_completion", "after_completion", "before_exit",
    ], result.stderr
    assert [item["elapsed_ms"] for item in stages] == sorted(item["elapsed_ms"] for item in stages)
    assert all(item["elapsed_ms"] >= 0 and item["cpu_user_us"] >= 0 and item["cpu_system_us"] >= 0 for item in stages)
    assert isinstance(stages[-1]["active_resources"], list)


@pytest.mark.parametrize("diagnostic", ["cpuUsage", "getActiveResourcesInfo"])
def test_browser_stage_failure_preserves_scenario_result(node_binary, diagnostic):
    harness = f"process.{diagnostic} = () => {{ throw new Error('injected diagnostic failure'); }};\n" + _harness()
    result = _run_node(node_binary, harness)
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
    cpu_samples = []
    real_popen = subprocess.Popen
    real_cpu_seconds = _child_cpu_seconds

    def record_process(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(subprocess, "Popen", record_process)

    def record_cpu(process):
        value = real_cpu_seconds(process)
        cpu_samples.append((process.poll(), value))
        return value

    monkeypatch.setitem(globals(), "_child_cpu_seconds", record_cpu)
    # The scenario completes, but an actual Node handle keeps the child alive.
    # Completion must not force a successful exit or disable the same 20s bound.
    harness = "setInterval(() => {}, 1000);\n" + _harness("Promise.resolve();")
    with pytest.raises(AssertionError, match="return code before termination=None") as failure:
        _run_node(node_binary, harness)
    assert _COMPLETED in str(failure.value)
    assert "E3_BROWSER_NODE_STARTED" in str(failure.value)
    assert "cleanup=reaped" in str(failure.value)
    assert len(cpu_samples) == 1
    alive, cpu_seconds = cpu_samples[0]
    assert alive is None
    if sys.platform == "win32":
        assert isinstance(cpu_seconds, float) and cpu_seconds >= 0
    else:
        assert cpu_seconds is None
    assert f"CPU seconds before termination={cpu_seconds!r}" in str(failure.value)
    assert len(processes) == 1
    assert processes[0].returncode is not None
    assert processes[0].poll() == processes[0].returncode


@pytest.mark.parametrize("platform", ["linux", "win32"])
def test_child_cpu_diagnostics_are_optional(platform, monkeypatch):
    loads = []

    def unavailable(*args, **kwargs):
        loads.append(args)
        raise OSError("injected diagnostic API failure")

    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setattr(ctypes, "WinDLL", unavailable, raising=False)
    assert _child_cpu_seconds(object()) is None
    assert len(loads) == (1 if platform == "win32" else 0)


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
