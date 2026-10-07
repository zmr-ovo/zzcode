import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from zzcode.evaluation import ArtifactError
from zzcode.evaluation.serialization import append_jsonl, load_json, write_json_atomic


def test_write_json_atomic_replaces_complete_document_and_leaves_no_temp(tmp_path):
    path = tmp_path / "state.json"
    write_json_atomic(path, {"status": "CREATED"})
    write_json_atomic(path, {"status": "RUNNING", "message": "中文"})

    assert load_json(path) == {"status": "RUNNING", "message": "中文"}
    assert list(tmp_path.glob("*.tmp")) == []


def test_write_json_atomic_can_refuse_overwrite(tmp_path):
    path = tmp_path / "immutable.json"
    write_json_atomic(path, {"version": 1}, overwrite=False)

    with pytest.raises(ArtifactError, match="will not be overwritten"):
        write_json_atomic(path, {"version": 2}, overwrite=False)

    assert load_json(path) == {"version": 1}


def test_append_jsonl_rejects_duplicate_unique_key(tmp_path):
    path = tmp_path / "events.jsonl"
    append_jsonl(path, {"instance_id": "A", "value": 1}, unique_key="instance_id")

    with pytest.raises(ArtifactError, match="duplicate"):
        append_jsonl(path, {"instance_id": "A", "value": 2}, unique_key="instance_id")


def test_append_jsonl_concurrent_writes_remain_complete(tmp_path):
    path = tmp_path / "events.jsonl"

    def append(index):
        append_jsonl(path, {"instance_id": f"TASK-{index}", "value": index}, unique_key="instance_id")

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(append, range(40)))

    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 40
    assert {row["instance_id"] for row in rows} == {f"TASK-{index}" for index in range(40)}
