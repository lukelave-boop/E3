"""Check the real release DAG, including paths that would bypass a failed gate.

GitHub applies success() implicitly to these job conditions. Test the dependency
edges and forbid status-function overrides rather than simulating Actions.
"""
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


def workflow(name):
    # BaseLoader retains GitHub's YAML key "on" as a string (not YAML 1.1 True).
    return yaml.load((ROOT / ".github/workflows" / name).read_text(encoding="utf-8"),
                     Loader=yaml.BaseLoader)


def test_release_uses_exact_commit_complete_compatibility_workflow():
    release = workflow("publish-development-update.yml")
    ci = workflow("ci.yml")
    gate = release["jobs"]["compatibility"]
    assert gate["uses"] == "./.github/workflows/ci.yml"
    assert "workflow_call" in ci["on"]
    assert set(ci["jobs"]) == {"test", "lint", "posix-controller-session"}
    assert ci["jobs"]["test"]["strategy"]["matrix"]["include"] == [
        {"python-version": "3.10", "desktop": "false"},
        {"python-version": "3.12", "desktop": "true"},
    ]
    for job in ci["jobs"].values():
        assert "if" not in job
        assert job.get("continue-on-error", "false") == "false"
        for step in job["steps"]:
            assert step.get("continue-on-error", "false") == "false"
            if step.get("uses", "").startswith("actions/checkout@"):
                assert "ref" not in step.get("with", {})


@pytest.mark.parametrize("name, dependencies", [
    ("windows", {"compatibility"}),
    ("linux", {"compatibility"}),
    ("publish", {"compatibility", "windows", "linux"}),
])
def test_failed_cancelled_or_skipped_gate_cannot_reach_release(name, dependencies):
    job = workflow("publish-development-update.yml")["jobs"][name]
    needs = job["needs"]
    assert set([needs] if isinstance(needs, str) else needs) == dependencies
    # No always()/!cancelled()/failure() can override implicit success().
    assert job["if"] == "github.ref == 'refs/heads/main'"
    assert job.get("continue-on-error", "false") == "false"
    for step in job["steps"]:
        if step.get("uses", "").startswith("actions/checkout@"):
            assert "ref" not in step.get("with", {})


def test_release_gate_covers_manual_and_push_with_write_only_at_publish():
    release = workflow("publish-development-update.yml")
    assert release["on"]["push"]["branches"] == ["main"]
    assert "workflow_dispatch" in release["on"]
    assert release["jobs"]["compatibility"]["if"] == "github.ref == 'refs/heads/main'"
    assert release["permissions"] == {"contents": "read"}
    for name, job in release["jobs"].items():
        assert job.get("permissions", release["permissions"]) == {
            "contents": "write" if name == "publish" else "read"
        }
