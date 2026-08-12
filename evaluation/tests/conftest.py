import json

import pytest


@pytest.fixture
def evaluation_dataset_roots(tmp_path):
    public_root = tmp_path / "public" / "zzcode-bench-v1"
    private_parent = tmp_path / "private"
    private_root = private_parent / public_root.name
    instance_id = "ZZCODE-BUG-001"

    problem_path = public_root / "instances" / instance_id / "problem_statement.md"
    problem_path.parent.mkdir(parents=True)
    problem_path.write_text("Fix stale summaries without breaking fresh summaries.\n", encoding="utf-8")
    (problem_path.parent / "task.json").write_text(
        json.dumps(
            {
                "instance_id": instance_id,
                "task_type": "bug_fix",
                "resource_limits": {"timeout_seconds": 900, "max_tool_steps": 30},
            }
        ),
        encoding="utf-8",
    )
    manifest = {
        "schema_version": 1,
        "instance_id": instance_id,
        "repo": "local/zzcode",
        "base_commit": "abcdef1234567890",
        "problem_statement_path": f"instances/{instance_id}/problem_statement.md",
        "environment_id": "zzcode-py313-v1",
    }
    (public_root / "manifest.jsonl").write_text(json.dumps(manifest) + "\n", encoding="utf-8")
    split_root = public_root / "splits"
    split_root.mkdir()
    (split_root / "dev.txt").write_text(instance_id + "\n", encoding="utf-8")

    grading_root = private_root / instance_id
    grading_root.mkdir(parents=True)
    (grading_root / "gold.patch").write_text("diff --git a/a.py b/a.py\n", encoding="utf-8")
    (grading_root / "test.patch").write_text("diff --git a/test_a.py b/test_a.py\n", encoding="utf-8")
    (grading_root / "grading.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "instance_id": instance_id,
                "gold_patch": "gold.patch",
                "test_patch": "test.patch",
                "FAIL_TO_PASS": ["hidden_tests/test_memory.py::test_stale_summary"],
                "PASS_TO_PASS": ["tests/test_memory.py"],
            }
        ),
        encoding="utf-8",
    )
    return public_root, private_parent, private_root
