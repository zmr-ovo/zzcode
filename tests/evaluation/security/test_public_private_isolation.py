import json

import pytest

from zzcode.evaluation import (
    DatasetValidationError,
    EvaluationDataset,
    PrivateDataLeakageError,
)


def test_inference_payload_has_no_private_grading_content(evaluation_dataset_roots):
    public_root, private_parent, _ = evaluation_dataset_roots
    dataset = EvaluationDataset.load(public_root, private_parent, "dev")

    serialized = json.dumps(dataset.inference_payload("ZZCODE-BUG-001"), ensure_ascii=False)

    for forbidden in (
        "FAIL_TO_PASS",
        "PASS_TO_PASS",
        "hidden_tests/test_memory.py",
        "gold.patch",
        "test.patch",
    ):
        assert forbidden not in serialized


def test_dataset_rejects_nested_private_root(evaluation_dataset_roots):
    public_root, _, _ = evaluation_dataset_roots
    private_root = public_root / "private"
    private_root.mkdir()

    with pytest.raises(PrivateDataLeakageError, match="separate"):
        EvaluationDataset.load(public_root, private_root, "dev")


def test_dataset_rejects_public_root_nested_under_private_root(evaluation_dataset_roots):
    public_root, _, _ = evaluation_dataset_roots

    with pytest.raises(PrivateDataLeakageError, match="separate"):
        EvaluationDataset.load(public_root, public_root.parent, "dev")


def test_dataset_rejects_private_fields_nested_in_public_metadata(evaluation_dataset_roots):
    public_root, private_parent, _ = evaluation_dataset_roots
    task_json_path = public_root / "instances" / "ZZCODE-BUG-001" / "task.json"
    task_json = json.loads(task_json_path.read_text(encoding="utf-8"))
    task_json["grader"] = {"FAIL_TO_PASS": ["hidden test"]}
    task_json_path.write_text(json.dumps(task_json), encoding="utf-8")

    with pytest.raises(PrivateDataLeakageError, match="grader"):
        EvaluationDataset.load(public_root, private_parent, "dev")


@pytest.mark.parametrize("private_key", ["F2P", "P2P", "grading_config", "gold_patch_sha256"])
def test_dataset_rejects_private_field_aliases_in_public_metadata(
    evaluation_dataset_roots, private_key
):
    public_root, private_parent, _ = evaluation_dataset_roots
    task_json_path = public_root / "instances" / "ZZCODE-BUG-001" / "task.json"
    task_json = json.loads(task_json_path.read_text(encoding="utf-8"))
    task_json[private_key] = "private"
    task_json_path.write_text(json.dumps(task_json), encoding="utf-8")

    with pytest.raises(PrivateDataLeakageError, match=private_key):
        EvaluationDataset.load(public_root, private_parent, "dev")


def test_dataset_rejects_symlinked_problem_statement_escape(evaluation_dataset_roots):
    public_root, private_parent, _ = evaluation_dataset_roots
    outside = public_root.parent / "outside.md"
    outside.write_text("private adjacent content", encoding="utf-8")
    linked = public_root / "instances" / "ZZCODE-BUG-001" / "linked.md"
    linked.symlink_to(outside)
    manifest_path = public_root / "manifest.jsonl"
    row = json.loads(manifest_path.read_text(encoding="utf-8"))
    row["problem_statement_path"] = "instances/ZZCODE-BUG-001/linked.md"
    manifest_path.write_text(json.dumps(row) + "\n", encoding="utf-8")

    with pytest.raises(DatasetValidationError, match="escapes"):
        EvaluationDataset.load(public_root, private_parent, "dev")
