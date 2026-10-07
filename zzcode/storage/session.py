"""版本化追加记录是事实来源；JSON 元数据只是可重建的索引。"""

import fcntl
import json
import os
import re
import tempfile
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from zzcode.context.workspace import now

VERSION = 1


class SessionError(RuntimeError):
    pass


class SessionStore:
    def __init__(self, root):
        self.root = Path(root)
        if self.root.is_symlink() or self.root.parent.is_symlink():
            raise SessionError("session store must not contain symlinks")
        self.root = self.root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, session_id):
        if not re.fullmatch(r"[A-Za-z0-9_-]+", session_id):
            raise SessionError("invalid session ID")
        return self.root / f"{session_id}.jsonl"

    @contextmanager
    def writer(self, session_id):
        path = self.path(session_id).with_suffix(".lock")
        if path.is_symlink():
            raise SessionError("session lock must not be a symlink")
        with path.open("a+") as handle:
            os.chmod(path, 0o600)
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise SessionError("another session writer is active") from exc
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def _read(self, path):
        if not path.exists():
            return []
        if path.is_symlink():
            raise SessionError("session must not be a symlink")
        records = []
        seen_ids = set()
        with path.open("r+b") as handle:
            while True:
                position = handle.tell()
                line = handle.readline()
                if not line:
                    break
                # 只有最后一个没有换行的残片可截断；中间损坏不能掩盖。
                if not line.endswith(b"\n"):
                    handle.truncate(position)
                    handle.flush()
                    os.fsync(handle.fileno())
                    break
                try:
                    entry = json.loads(line)
                    if entry["schema_version"] != VERSION or entry["sequence"] != len(
                        records
                    ):
                        raise ValueError("unsupported version or sequence")
                    if entry["type"] not in {
                        "message",
                        "tool_batch",
                        "checkpoint",
                        "compaction",
                        "state",
                        "reset",
                    }:
                        raise ValueError("unknown entry type")
                    if (
                        not isinstance(entry["payload"], dict)
                        or not isinstance(entry["entry_id"], str)
                        or entry["entry_id"] in seen_ids
                    ):
                        raise ValueError("invalid entry")
                except (ValueError, KeyError, TypeError) as exc:
                    raise SessionError(
                        f"corrupt session record at byte {position}"
                    ) from exc
                seen_ids.add(entry["entry_id"])
                records.append(entry)
        return records

    @staticmethod
    def _project(records):
        session = {
            "history": [],
            "checkpoints": {"items": {}, "current_id": ""},
            "compactions": [],
        }
        for entry in records:
            kind, payload = entry["type"], entry["payload"]
            if kind in {"message", "tool_batch"}:
                session["history"].append({**payload, "entry_id": entry["entry_id"]})
            elif kind == "checkpoint":
                session["checkpoints"]["items"][payload["checkpoint_id"]] = payload
                session["checkpoint_current_id"] = payload["checkpoint_id"]
            elif kind == "compaction":
                session["compactions"].append(payload)
            elif kind == "reset":
                session["history"] = []
                session["compactions"] = []
                session["checkpoints"] = {"items": {}, "current_id": ""}
                session["checkpoint_current_id"] = ""
                session.update(payload)
            else:
                session.update(payload)
        session["_entry_count"] = len(records)
        return session

    def _append(self, path, records, additions):
        if path.is_symlink():
            raise SessionError("session must not be a symlink")
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            os.chmod(path, 0o600)
            for kind, payload, entry_id in additions:
                entry = dict(
                    schema_version=VERSION,
                    sequence=len(records),
                    entry_id=entry_id or "entry_" + uuid4().hex,
                    created_at=now(),
                    type=kind,
                    payload=payload,
                )
                handle.write(json.dumps(entry, ensure_ascii=False).encode() + b"\n")
                records.append(entry)
            handle.flush()
            os.fsync(handle.fileno())
        descriptor = os.open(self.root, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def save(self, session):
        path = self.path(session["id"])
        try:
            with self.writer(session["id"]):
                records = self._read(path)
                previous = self._project(records)
                if session.get("_entry_count", len(records)) != len(records):
                    raise SessionError(
                        "session revision changed; reload before writing"
                    )
                additions = []
                history = session["history"]
                old = previous["history"]
                if old and not history:
                    additions.append(
                        ("reset", {"memory": session.get("memory", {})}, None)
                    )
                    old = []
                    previous = self._project([])
                if len(history) < len(old) or any(
                    json.dumps(a, sort_keys=True) != json.dumps(b, sort_keys=True)
                    for a, b in zip(history, old)
                ):
                    raise SessionError("saved history is append-only")
                for item in history[len(old) :]:
                    entry_id = item.setdefault("entry_id", "entry_" + uuid4().hex)
                    kind = (
                        "tool_batch"
                        if any(
                            block["type"] == "tool_call"
                            for block in item.get("message", {}).get("content", [])
                        )
                        else "message"
                    )
                    additions.append(
                        (
                            kind,
                            {k: v for k, v in item.items() if k != "entry_id"},
                            entry_id,
                        )
                    )
                for identifier, checkpoint in (
                    session.get("checkpoints", {}).get("items", {}).items()
                ):
                    if identifier not in previous["checkpoints"]["items"]:
                        additions.append(("checkpoint", checkpoint, None))
                known = {item["compaction_id"] for item in previous["compactions"]}
                for compaction in session.get("compactions", []):
                    if compaction["compaction_id"] not in known:
                        additions.append(("compaction", compaction, None))
                metadata = {
                    k: v
                    for k, v in session.items()
                    if k
                    not in {"history", "checkpoints", "compactions", "_entry_count"}
                }
                metadata["checkpoint_current_id"] = session.get("checkpoints", {}).get(
                    "current_id", ""
                )
                additions.append(("state", metadata, None))
                self._append(path, records, additions)
                session["_entry_count"] = len(records)
                # 索引写入失败不丢事实，下次仍从 JSONL 重建。
                index = path.with_suffix(".index.json")
                with tempfile.NamedTemporaryFile(
                    "w", dir=self.root, prefix=".index-", delete=False
                ) as handle:
                    temporary = handle.name
                    json.dump(
                        {"schema_version": VERSION, "entry_count": len(records)}, handle
                    )
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, index)
        except OSError as exc:
            raise SessionError(
                "session persistence failed; stop before another operation"
            ) from exc
        return path

    def load(self, session_id):
        path = self.path(session_id)
        if not path.exists():
            legacy = path.with_suffix(".json")
            if legacy.is_symlink():
                raise SessionError("legacy session must not be a symlink")
            with self.writer(session_id):
                if not path.exists():
                    legacy_data = legacy.read_bytes()
                    session = json.loads(legacy_data)
                    if session.get("id") != session_id:
                        raise SessionError("legacy session ID mismatch")
                    backup = legacy.with_suffix(".json.backup")
                    if backup.is_symlink():
                        raise SessionError("migration backup must not be a symlink")
                    if not backup.exists():
                        with tempfile.NamedTemporaryFile(
                            "wb", dir=self.root, prefix=".backup-", delete=False
                        ) as handle:
                            temporary = handle.name
                            handle.write(legacy_data)
                            handle.flush()
                            os.fsync(handle.fileno())
                        os.replace(temporary, backup)
                    elif backup.read_bytes() != legacy_data:
                        raise SessionError(
                            "legacy backup differs; inspect it before migration"
                        )
                    # 完整验证临时副本后原子发布，迁移中断不会留下半个新版会话。
                    with tempfile.TemporaryDirectory(
                        prefix=".migration-", dir=self.root
                    ) as directory:
                        staging = SessionStore(directory)
                        session.pop("_entry_count", None)
                        staging.save(session)
                        migrated = staging.load(session_id)
                        if len(migrated["history"]) != len(session["history"]):
                            raise SessionError("migration history mismatch")
                        if legacy.read_bytes() != legacy_data:
                            raise SessionError(
                                "legacy session changed during migration"
                            )
                        os.replace(staging.path(session_id), path)
                        descriptor = os.open(self.root, os.O_RDONLY)
                        try:
                            os.fsync(descriptor)
                        finally:
                            os.close(descriptor)
        with self.writer(session_id):
            session = self._project(self._read(path))
        if "id" not in session or session["id"] != session_id:
            raise SessionError("session metadata missing or mismatched")
        session["checkpoints"]["current_id"] = session.pop("checkpoint_current_id", "")
        return session

    def latest(self):
        files = [
            *self.root.glob("*.jsonl"),
            *[
                p
                for p in self.root.glob("*.json")
                if not p.name.endswith(".index.json")
            ],
        ]
        return max(files, key=lambda p: p.stat().st_mtime).stem if files else None
