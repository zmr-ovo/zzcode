import json

import pytest

from zzcode.evaluation import ArtifactError, render_run_directory, render_run_report


def sample_artifacts(run_id="phase7-run"):
    manifest = {
        "run_id": run_id,
        "dataset_name": "zzcode-bench-v1",
        "dataset_digest": "abc123",
        "split": "test",
        "agent_commit": "deadbeef",
        "provider": "openai",
        "model_name_or_path": "example/model",
        "environment_id": "zzcode-py313",
        "image_digest": "sha256:123",
    }
    results = {
        "run_id": run_id,
        "summary": {
            "task_count": 2,
            "resolved": 1,
            "resolution_rate": 0.5,
            "dataset_gate_passed": 2,
            "status_counts": {"FULL": 1, "NO": 1},
        },
        "tasks": [
            {
                "instance_id": "ZZCODE-BUG-001",
                "dataset_gate_passed": True,
                "agent_status": "COMPLETED",
                "resolved_status": "FULL",
                "fail_to_pass_rate": 1.0,
                "pass_to_pass_rate": 1.0,
                "failure_category": None,
            },
            {
                "instance_id": "ZZCODE-BUG-002",
                "dataset_gate_passed": True,
                "agent_status": "COMPLETED",
                "resolved_status": "NO",
                "fail_to_pass_rate": 0.0,
                "pass_to_pass_rate": 1.0,
                "failure_category": "AGENT_ERROR",
            },
        ],
    }
    return manifest, results


def test_render_run_report_contains_reproducibility_and_scores():
    manifest, results = sample_artifacts()

    report = render_run_report(manifest, results)

    assert "Resolved: **1/2**" in report
    assert "Resolution rate: **50.0%**" in report
    assert "`abc123`" in report
    assert "| ZZCODE-BUG-001 | PASS | COMPLETED | FULL | 100.0% | 100.0% | - |" in report


def test_render_run_directory_writes_report_next_to_json(tmp_path):
    run_root = tmp_path / "phase7-run"
    run_root.mkdir()
    manifest, results = sample_artifacts()
    (run_root / "run_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (run_root / "results.json").write_text(json.dumps(results), encoding="utf-8")

    report = render_run_directory(run_root)

    assert report == run_root / "report.md"
    assert "Pass@1 summary" in report.read_text(encoding="utf-8")


def test_render_run_directory_rejects_mismatched_run_id(tmp_path):
    run_root = tmp_path / "phase7-run"
    run_root.mkdir()
    manifest, results = sample_artifacts("another-run")
    (run_root / "run_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (run_root / "results.json").write_text(json.dumps(results), encoding="utf-8")

    with pytest.raises(ArtifactError, match="run_id"):
        render_run_directory(run_root)
