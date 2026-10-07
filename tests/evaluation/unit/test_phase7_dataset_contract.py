import json
from pathlib import Path

import pytest

from zzcode.evaluation import DatasetValidationError, EvaluationDataset


def dataset_root():
    return Path(__file__).resolve().parents[3] / "evaluation" / "datasets" / "zzcode-bench-v1"


def test_phase7_dataset_has_eight_partitioned_tasks():
    root = dataset_root()
    manifest = [json.loads(line) for line in (root / "manifest.jsonl").read_text().splitlines()]
    manifest_ids = {row["instance_id"] for row in manifest}
    dev = set((root / "splits" / "dev.txt").read_text().splitlines())
    test = set((root / "splits" / "test.txt").read_text().splitlines())
    all_tasks = set((root / "splits" / "all.txt").read_text().splitlines())

    assert len(manifest_ids) == 8
    assert len(dev) == 4
    assert len(test) == 4
    assert dev.isdisjoint(test)
    assert dev | test == all_tasks == manifest_ids

    for instance_id in manifest_ids:
        instance = root / "instances" / instance_id
        assert (instance / "problem_statement.md").is_file()
        metadata = json.loads((instance / "task.json").read_text())
        assert metadata["instance_id"] == instance_id


def test_phase7_dataset_lock_covers_every_split():
    root = dataset_root()
    lock = json.loads((root / "dataset-lock.json").read_text())

    assert lock["dataset"] == "zzcode-bench-v1"
    assert lock["schema_version"] == 1
    assert lock["splits"]["dev"]["task_count"] == 4
    assert lock["splits"]["test"]["task_count"] == 4
    assert lock["splits"]["all"]["task_count"] == 8
    assert all(
        row["digest"].startswith("sha256:") and len(row["digest"]) == 71
        for row in lock["splits"].values()
    )


def test_phase7_dataset_lock_matches_private_bundle():
    root = dataset_root()
    private = root.parents[1] / "private"
    if not private.is_dir():
        pytest.skip("private evaluation bundle is distributed separately")

    for split in ("dev", "test", "all"):
        assert EvaluationDataset.load(root, private, split).verify_lock() is True


def test_dataset_lock_rejects_changed_digest(evaluation_dataset_roots):
    public_root, private_parent, _ = evaluation_dataset_roots
    dataset = EvaluationDataset.load(public_root, private_parent, "dev")
    (public_root / "dataset-lock.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "dataset": public_root.name,
                "splits": {"dev": {"task_count": 1, "digest": "sha256:" + "0" * 64}},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(DatasetValidationError, match="digest mismatch"):
        dataset.verify_lock()
