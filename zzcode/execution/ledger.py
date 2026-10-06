"""操作状态是恢复依据；Trace 只负责观测，不参与执行决策。"""

import fcntl
import json
import os
import sqlite3
import subprocess
import threading
from contextlib import contextmanager
from dataclasses import asdict
from uuid import uuid4

from ..core.messages import block_from_dict


class PersistenceError(RuntimeError):
    pass


class OperationBusyError(RuntimeError):
    pass


def ensure_inactive(details):
    if details.get("container_id") and not details.get("container_removed"):
        try:
            inspection = subprocess.run([details["docker_binary"], "inspect", details["container_id"], "--format", "{{.State.Running}}"], capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise OperationBusyError("cannot establish interrupted container state") from exc
        if inspection.returncode != 0 or inspection.stdout.strip() != "false":
            raise OperationBusyError("interrupted container is active or its state is unknown")
    if details.get("pid"):
        try:
            os.killpg(details["pid"], 0)
        except ProcessLookupError:
            return
        raise OperationBusyError("an interrupted shell process group is still active")


class OperationLedger:
    def __init__(self, root):
        if root.resolve() != root:
            raise ValueError("operation store must not contain symlinks")
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / "operations.sqlite3"
        if self.path.is_symlink() or (root / "operations.lock").is_symlink():
            raise ValueError("operation files must not be symlinks")
        self.lock_file = (root / "operations.lock").open("a+")
        os.chmod(self.lock_file.name, 0o600)
        self.mutex = threading.RLock()
        self.depth = 0
        self.connection = sqlite3.connect(self.path, timeout=5, check_same_thread=False)
        os.chmod(self.path, 0o600)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA synchronous=FULL")
        self.connection.execute("""CREATE TABLE IF NOT EXISTS operations (
            operation_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, run_id TEXT NOT NULL, call_id TEXT NOT NULL,
            name TEXT NOT NULL, args_hash TEXT NOT NULL, state TEXT NOT NULL,
            recovery TEXT NOT NULL, details TEXT NOT NULL, result TEXT, started INTEGER NOT NULL DEFAULT 0,
            UNIQUE(session_id, call_id))""")
        self.connection.commit()

    @contextmanager
    def writer(self):
        # 子 Agent 共享同一账本，允许同线程嵌套；其他进程不能同时执行工具。
        with self.mutex:
            outermost = self.depth == 0
            if outermost:
                try:
                    fcntl.flock(self.lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as exc:
                    raise OperationBusyError("workspace has another active tool operation") from exc
            self.depth += 1
            try:
                yield
            finally:
                self.depth -= 1
                if outermost:
                    fcntl.flock(self.lock_file, fcntl.LOCK_UN)

    def _write(self, sql, parameters):
        try:
            with self.connection:
                cursor = self.connection.execute(sql, parameters)
                if cursor.rowcount != 1:
                    raise PersistenceError("operation transition did not update exactly one row")
        except sqlite3.Error as exc:
            raise PersistenceError("operation ledger commit failed; stop before retrying") from exc

    def plan(self, session_id, run_id, call_id, name, args_hash, recovery, details):
        operation_id = "op_" + uuid4().hex
        self._write("INSERT INTO operations (operation_id, session_id, run_id, call_id, name, args_hash, state, recovery, details) VALUES (?, ?, ?, ?, ?, ?, 'planned', ?, ?)",
                    (operation_id, session_id, run_id, call_id, name, args_hash, recovery, json.dumps(details)))
        return operation_id

    def start(self, operation_id):
        self._write("UPDATE operations SET state='running', started=1 WHERE operation_id=? AND state='planned'", (operation_id,))

    def update_details(self, operation_id, details):
        self._write("UPDATE operations SET details=? WHERE operation_id=?", (json.dumps(details), operation_id))

    def finish(self, result):
        self._write("UPDATE operations SET state=?, result=? WHERE operation_id=?",
                    (result.status, json.dumps(asdict(result)), result.operation_id))

    def get(self, session_id, call_id):
        row = self.connection.execute("SELECT * FROM operations WHERE session_id=? AND call_id=?", (session_id, call_id)).fetchone()
        return dict(row) if row else None

    def unsettled(self):
        rows = [dict(row) for row in self.connection.execute("SELECT * FROM operations WHERE state IN ('planned', 'running', 'unknown', 'partial_success') ORDER BY rowid")]
        return [row for row in rows if row["state"] != "partial_success" or self.result(row).timed_out or self.result(row).error_code in {"cancelled", "recovery_required"}]

    @staticmethod
    def result(row):
        return block_from_dict(json.loads(row["result"])) if row["result"] else None

    def counts(self, run_id):
        rows = list(self.connection.execute("SELECT state, started, COUNT(*) AS count FROM operations WHERE run_id=? GROUP BY state, started", (run_id,)))
        return {"proposed": sum(row["count"] for row in rows), "executed": sum(row["count"] for row in rows if row["started"]),
                "rejected": sum(row["count"] for row in rows if row["state"] == "rejected"),
                "cancelled": sum(row["count"] for row in rows if row["state"] == "cancelled"),
                "succeeded": sum(row["count"] for row in rows if row["state"] == "succeeded")}

    def resolve(self, operation_id, status, evidence):
        if status not in {"succeeded", "failed", "cancelled"} or not evidence.strip():
            raise ValueError("resolution requires an explicit terminal status and evidence")
        with self.writer():
            row = self.connection.execute("SELECT * FROM operations WHERE operation_id=?", (operation_id,)).fetchone()
            if row is None or row["operation_id"] not in {item["operation_id"] for item in self.unsettled()} or row["state"] not in {"unknown", "partial_success"}:
                raise ValueError("only unknown operations require manual resolution")
            ensure_inactive(json.loads(row["details"]))
            original = self.result(row)
            from dataclasses import replace
            result = replace(original, content=evidence, status=status, error_code="manually_resolved", timed_out=False)
            self.finish(result)

