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


def test_both_embedded_identities_are_required_before_manifest_publication():
    jobs = workflow("publish-development-update.yml")["jobs"]
    for platform, embedded in (
        ("windows", "dist/E3/build-info.json"),
        ("linux", "squashfs-root/usr/bin/E3/build-info.json"),
    ):
        steps = jobs[platform]["steps"]
        upload = next(i for i, step in enumerate(steps)
                      if step.get("uses", "").startswith("actions/upload-artifact@"))
        verification = next(i for i, step in enumerate(steps)
                            if "verify_release_metadata.py" in step.get("run", ""))
        assert verification < upload
        check = steps[verification]
        assert "if" not in check and "continue-on-error" not in check
        assert embedded in check["run"]
        assert f"--build-info {platform}-x86_64" in check["run"]
        assert f"build-info-{platform}.json" in steps[upload]["with"]["path"]
        if platform == "windows":
            assert r". .\packaging\inno_setup.ps1" in check["run"]
            assert "Get-E3InstallerProductVersion -InstallerPath installer-dist/E3-Setup.exe -Verbose" in check["run"]
            assert "if ($LASTEXITCODE -ne 0) { throw" in check["run"]
        else:
            assert "--appimage-extract usr/bin/E3/build-info.json" in check["run"]

    steps = jobs["publish"]["steps"]
    publication = next(i for i, step in enumerate(steps)
                       if "publish_development_update.py publish" in step.get("run", ""))
    verification = next(i for i, step in enumerate(steps)
                        if "verify_release_metadata.py" in step.get("run", ""))
    assert verification < publication
    check = steps[verification]
    assert "if" not in check and "continue-on-error" not in check
    for required in (
        "--build-info windows-x86_64 release/build-info-windows.json",
        "--build-info linux-x86_64 release/build-info-linux.json",
        '--manifest "release/update-manifest-${short_sha}-${publication_id}.json"',
        '--revision "$GITHUB_SHA"',
    ):
        assert required in check["run"]


def test_linux_package_validation_has_no_publication_or_application_launch():
    validation = workflow("linux-packaging.yml")
    assert validation["permissions"] == {"contents": "read"}
    assert "pull_request" in validation["on"]
    assert "workflow_dispatch" in validation["on"]
    assert "pull_request_target" not in validation["on"]
    for step in validation["jobs"]["linux"]["steps"]:
        assert "if" not in step and "continue-on-error" not in step
        run = step.get("run", "")
        assert "publish_development_update.py" not in run
        assert "--appimage-extract-and-run" not in run
        assert "AppRun" not in run
        if step.get("uses", "").startswith("actions/checkout@"):
            assert step["with"]["persist-credentials"] == "false"
