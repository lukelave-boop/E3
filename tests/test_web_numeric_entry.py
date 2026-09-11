from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest


def test_browser_numeric_commit_rejects_incomplete_drafts_without_rewriting_them():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required to execute the browser numeric parser")
    source = (Path(__file__).resolve().parents[1] / "laser_aligner/web/app.js").read_text(encoding="utf-8")
    start = source.index("function numericValue(")
    end = source.index("\n}\n", start) + 2
    function = source[start:end]
    script = """
const assert = require('node:assert/strict');
const input = {value: ''};
const $ = () => input;
""" + function + """
for (const text of ['', ' ', '-', '.', '1e', 'NaN', 'Infinity', '1e999', '0x10', '1.2.3']) {
  input.value = text;
  assert.throws(() => numericValue('field'));
  assert.equal(input.value, text);
}
for (const [text, expected] of [['0', 0], ['0.1', .1], ['40', 40], ['-4.5', -4.5], ['1e2', 100]]) {
  input.value = text;
  assert.equal(numericValue('field'), expected);
  assert.equal(input.value, text);
}
console.log(JSON.stringify({valid: 5, rejected: 10}));
"""
    completed = subprocess.run([node, "-"], input=script, text=True, capture_output=True, check=True)
    assert json.loads(completed.stdout) == {"valid": 5, "rejected": 10}
