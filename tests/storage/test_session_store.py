import json


import pytest

from zzcode import FakeModelClient, MiniAgent, SessionStore, WorkspaceContext

from zzcode.core.messages import (
    Message,
    TextBlock,
    ModelResponse,
    tool_response,
)


from zzcode.storage.session import SessionError

class CompactionTestClient(FakeModelClient):
    """单独提供摘要响应，任务响应仍按录制序列消费。"""
    def complete(self, request):
        from zzcode.context.compaction import SUMMARY_SYSTEM
        if request.system == SUMMARY_SYSTEM:
            self.requests.append(request)
            return ModelResponse(Message("assistant", (TextBlock("保持接口不变；历史结果仅作观察，下一步继续检查。"),)))
        return super().complete(request)

def agent_at(path, outputs=(), **kwargs):
    (path / "target.txt").write_text("old\n")
    return MiniAgent(
        CompactionTestClient(outputs),
        WorkspaceContext.build(path),
        SessionStore(path / ".zzcode" / "sessions"),
        approval_policy="auto",
        **kwargs,
    )

def test_legacy_migration_preserves_original_and_rebuilds_projection(tmp_path):
    store = SessionStore(tmp_path)
    original = {
        "id": "legacy",
        "history": [{"role": "user", "content": "hello"}],
        "memory": {},
        "checkpoints": {
            "items": {"c1": {"checkpoint_id": "c1", "schema_version": "old"}},
            "current_id": "c1",
        },
    }
    old = tmp_path / "legacy.json"
    old.write_text(json.dumps(original))
    loaded = store.load("legacy")
    assert json.loads(old.read_text()) == original
    assert (tmp_path / "legacy.json.backup").read_bytes() == old.read_bytes()
    assert loaded["history"][0]["content"] == "hello"
    assert loaded["history"][0]["entry_id"]
    assert loaded["checkpoints"]["current_id"] == "c1"
    assert store.path("legacy").stat().st_mode & 0o777 == 0o600
    assert [
        json.loads(line)["type"]
        for line in store.path("legacy").read_text().splitlines()
    ] == ["message", "checkpoint", "state"]
    assert store.latest() == "legacy"

def test_migration_failure_does_not_publish_partial_session(tmp_path, monkeypatch):
    store = SessionStore(tmp_path)
    legacy = tmp_path / "legacy.json"
    legacy.write_text(json.dumps({"id": "legacy", "history": [], "memory": {}}))

    def fail(*args):
        raise SessionError("migration interruption")

    monkeypatch.setattr(SessionStore, "_append", fail)
    with pytest.raises(SessionError):
        store.load("legacy")
    assert not store.path("legacy").exists()
    assert legacy.exists()
    assert (tmp_path / "legacy.json.backup").exists()

def test_tail_fragment_recovery_but_middle_corruption_is_error(tmp_path):
    store = SessionStore(tmp_path)
    session = {"id": "session", "history": [{"role": "user", "content": "kept"}]}
    path = store.save(session)
    complete = path.read_bytes()
    with path.open("ab") as handle:
        handle.write(b'{"schema_version":')
    assert store.load("session")["history"][0]["content"] == "kept"
    assert path.read_bytes() == complete
    path.write_bytes(complete + b"bad line\n" + complete)
    with pytest.raises(SessionError, match="corrupt"):
        store.load("session")

def test_stale_writer_and_mutation_are_rejected(tmp_path):
    store = SessionStore(tmp_path)
    store.save({"id": "session", "history": [{"role": "user", "content": "initial"}]})
    first, second = store.load("session"), store.load("session")
    first["history"].append({"role": "user", "content": "new"})
    store.save(first)
    with pytest.raises(SessionError, match="revision"):
        store.save(second)
    fresh = store.load("session")
    fresh["history"][0]["content"] = "rewrite"
    with pytest.raises(SessionError, match="append-only"):
        store.save(fresh)

def test_reset_and_tool_batch_records(tmp_path):
    agent = agent_at(
        tmp_path, [tool_response("read_file", {"path": "target.txt"}), "done"]
    )
    agent.ask("read")
    types = [
        json.loads(line)["type"] for line in agent.session_path.read_text().splitlines()
    ]
    assert "tool_batch" in types and "checkpoint" in types
    checkpoint = agent.current_checkpoint()
    assert checkpoint["session_entry_id"]
    assert checkpoint["operation_ids"]
    agent.reset()
    resumed = agent.session_store.load(agent.session["id"])
    assert resumed["history"] == [] and resumed["compactions"] == []
    assert resumed["checkpoints"]["items"] == {}

def test_session_store_rejects_symlinks_and_active_writer(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    link = tmp_path / "link"
    link.symlink_to(root, target_is_directory=True)
    with pytest.raises(SessionError, match="symlink"):
        SessionStore(link)
    first, second = SessionStore(root), SessionStore(root)
    with first.writer("session"):
        with pytest.raises(SessionError, match="writer"):
            second.save({"id": "session", "history": []})

def test_checkpoint_projection_survives_interrupted_metadata_commit(tmp_path):
    store = SessionStore(tmp_path)
    session = {
        "id": "session",
        "history": [],
        "memory": {"old": "value"},
        "checkpoints": {"items": {}, "current_id": ""},
    }
    store.save(session)
    session["checkpoints"] = {
        "items": {"new": {"checkpoint_id": "new"}},
        "current_id": "new",
    }
    path = store.save(session)
    lines = path.read_bytes().splitlines(keepends=True)
    path.write_bytes(b"".join(lines[:-1]) + b'{"partial":')
    loaded = store.load("session")
    assert loaded["checkpoints"]["current_id"] == "new"

def test_legacy_edit_during_migration_is_not_overwritten(tmp_path, monkeypatch):
    store = SessionStore(tmp_path)
    legacy = tmp_path / "legacy.json"
    legacy.write_text(json.dumps({"id": "legacy", "history": [], "memory": {}}))
    original = SessionStore.save

    def changed(self, session):
        result = original(self, session)
        legacy.write_text(
            json.dumps(
                {"id": "legacy", "history": [{"role": "user", "content": "human edit"}]}
            )
        )
        return result

    monkeypatch.setattr(SessionStore, "save", changed)
    with pytest.raises(SessionError, match="changed"):
        store.load("legacy")
    assert not store.path("legacy").exists()
    assert "human edit" in legacy.read_text()
