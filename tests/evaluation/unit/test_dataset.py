import json
import re

import pytest

from zzcode.evaluation import (
    DatasetValidationError,
    EvaluationDataset,
)


def test_load_minimal_dataset_keeps_private_data_out_of_inference_payload(
    evaluation_dataset_roots,
):
    public_root, private_parent, private_root = evaluation_dataset_roots

    dataset = EvaluationDataset.load(public_root, private_parent, "dev")
    payload = dataset.inference_payload("ZZCODE-BUG-001")
    private = dataset.private_spec("ZZCODE-BUG-001")

    serialized = json.dumps(payload, ensure_ascii=False)
    assert payload["problem_statement"].startswith("Fix stale summaries")
    assert payload["metadata"]["task_type"] == "bug_fix"
    assert "FAIL_TO_PASS" not in serialized
    assert "PASS_TO_PASS" not in serialized
    assert "gold.patch" not in serialized
    assert "test.patch" not in serialized
    assert private.gold_patch_path == private_root / "ZZCODE-BUG-001" / "gold.patch"


def test_dataset_digest_is_stable_and_changes_with_private_patch(evaluation_dataset_roots):
    public_root, private_parent, private_root = evaluation_dataset_roots
    first = EvaluationDataset.load(public_root, private_parent, "dev").digest()
    second = EvaluationDataset.load(public_root, private_parent, "dev").digest()

    assert first == second
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", first)

    gold_patch = private_root / "ZZCODE-BUG-001" / "gold.patch"
    gold_patch.write_text(gold_patch.read_text(encoding="utf-8") + "changed\n", encoding="utf-8")
    changed = EvaluationDataset.load(public_root, private_parent, "dev").digest()
    assert changed != first


def test_dataset_rejects_duplicate_manifest_ids(evaluation_dataset_roots):
    public_root, private_parent, _ = evaluation_dataset_roots
    manifest = public_root / "manifest.jsonl"
    manifest.write_text(manifest.read_text(encoding="utf-8") * 2, encoding="utf-8")

    with pytest.raises(DatasetValidationError, match="duplicate"):
        EvaluationDataset.load(public_root, private_parent, "dev")


def test_dataset_rejects_problem_statement_path_escape(evaluation_dataset_roots):
    public_root, private_parent, _ = evaluation_dataset_roots
    manifest_path = public_root / "manifest.jsonl"
    row = json.loads(manifest_path.read_text(encoding="utf-8"))
    row["problem_statement_path"] = "../../outside.md"
    manifest_path.write_text(json.dumps(row) + "\n", encoding="utf-8")

    with pytest.raises(DatasetValidationError, match="escapes"):
        EvaluationDataset.load(public_root, private_parent, "dev")


def test_dataset_rejects_problem_statement_from_another_instance(evaluation_dataset_roots):
    public_root, private_parent, _ = evaluation_dataset_roots
    other = public_root / "instances" / "ZZCODE-BUG-002" / "problem_statement.md"
    other.parent.mkdir()
    other.write_text("Another task", encoding="utf-8")
    manifest_path = public_root / "manifest.jsonl"
    row = json.loads(manifest_path.read_text(encoding="utf-8"))
    row["problem_statement_path"] = "instances/ZZCODE-BUG-002/problem_statement.md"
    manifest_path.write_text(json.dumps(row) + "\n", encoding="utf-8")

    with pytest.raises(DatasetValidationError, match="instance directory"):
        EvaluationDataset.load(public_root, private_parent, "dev")


def test_dataset_rejects_unknown_split_task(evaluation_dataset_roots):
    public_root, private_parent, _ = evaluation_dataset_roots
    (public_root / "splits" / "dev.txt").write_text("ZZCODE-BUG-999\n", encoding="utf-8")

    with pytest.raises(DatasetValidationError, match="unknown"):
        EvaluationDataset.load(public_root, private_parent, "dev")


def test_tasks_returns_copies_that_cannot_mutate_dataset_digest(evaluation_dataset_roots):
    public_root, private_parent, _ = evaluation_dataset_roots
    dataset = EvaluationDataset.load(public_root, private_parent, "dev")
    original_digest = dataset.digest()

    dataset.tasks()[0].metadata["task_type"] = "mutated"

    assert dataset.tasks()[0].metadata["task_type"] == "bug_fix"
    assert dataset.digest() == original_digest
