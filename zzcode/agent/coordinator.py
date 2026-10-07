"""Agent 运行时核心逻辑。

ZZCode 就是包在模型外面的控制循环：负责组 prompt、解析模型输出、
校验并执行工具、写 trace、更新工作记忆，以及在合适的时候停下来。
"""

import json
import os
import re
import textwrap
import uuid
import hashlib
import sys
from collections import deque
from .contracts import RunRequest, AgentResult, freeze
from .events import build_event
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from zzcode.core.messages import Message, ToolCall
from zzcode.execution.output import SECRET_PATTERN
from zzcode.execution.gateway import ToolGateway
from zzcode.execution.policy import approve_operation
from zzcode.execution.shell import LocalExecutor
from zzcode.execution.ledger import PersistenceError, OperationBusyError
from zzcode.execution.files import file_hash
from zzcode.context import memory as memorylib
from zzcode.storage.session import SessionStore as SessionStore, SessionError
from zzcode.context.budget import TokenBudget
from zzcode.context.manager import ContextManager
from zzcode.storage.runs import RunStore
from zzcode.execution import tools as toolkit
from zzcode.context.workspace import IGNORED_PATH_NAMES, MAX_HISTORY, WorkspaceContext, clip, now

SENSITIVE_ENV_NAME_MARKERS = ("API_KEY", "TOKEN", "SECRET", "PASSWORD")
REDACTED_VALUE = "<redacted>"
DEFAULT_SHELL_ENV_ALLOWLIST = ("HOME", "LANG", "LC_ALL", "LC_CTYPE", "LOGNAME", "PATH", "PWD", "SHELL", "TERM", "TMPDIR", "TMP", "TEMP", "USER")
DEFAULT_FEATURE_FLAGS = {
    "memory": True,
    "relevant_memory": True,
    "context_reduction": True,
    "prompt_cache": True,
}
CHECKPOINT_SCHEMA_VERSION = "phase1-v1"
CHECKPOINT_NONE_STATUS = "no-checkpoint"
CHECKPOINT_FULL_VALID_STATUS = "full-valid"
CHECKPOINT_PARTIAL_STALE_STATUS = "partial-stale"
CHECKPOINT_WORKSPACE_MISMATCH_STATUS = "workspace-mismatch"
CHECKPOINT_SCHEMA_MISMATCH_STATUS = "schema-mismatch"
DURABLE_MEMORY_INTENT_PATTERN = re.compile(r"(?i)\b(capture|remember|save|store|persist|note)\b")
DURABLE_MEMORY_INTENT_ZH_PATTERN = re.compile(r"(记住|保存|记录|沉淀|长期记忆|持久记忆)")
DURABLE_MEMORY_LINE_PATTERNS = (
    ("project-conventions", re.compile(r"(?i)^Project convention:\s*(.+)$")),
    ("key-decisions", re.compile(r"(?i)^Decision:\s*(.+)$")),
    ("dependency-facts", re.compile(r"(?i)^Dependency:\s*(.+)$")),
    ("user-preferences", re.compile(r"(?i)^Preference:\s*(.+)$")),
    ("project-conventions", re.compile(r"^项目约定：\s*(.+)$")),
    ("key-decisions", re.compile(r"^决策：\s*(.+)$")),
    ("dependency-facts", re.compile(r"^依赖：\s*(.+)$")),
    ("user-preferences", re.compile(r"^偏好：\s*(.+)$")),
)
SECRET_SHAPED_TEXT_PATTERN = re.compile(r"(?i)(\b(api[_ -]?key|token|secret|password)\b|sk-[A-Za-z0-9_-]{6,})")


@dataclass
class PromptPrefix:
    # prefix 除了文本本身，还带一小份元数据，
    # 这样 runtime 才能明确判断 prefix 是否可以复用。
    text: str
    hash: str
    workspace_fingerprint: str
    tool_signature: str
    built_at: str




class ZZCode:
    def __init__(
        self,
        model_client,
        workspace,
        session_store,
        session=None,
        run_store=None,
        approval_policy="ask",
        max_steps=10,
        max_new_tokens=2048,
        depth=0,
        max_depth=1,
        read_only=False,
        shell_env_allowlist=None,
        secret_env_names=None,
        feature_flags=None,
        operation_ledger=None,
        max_run_seconds=300,
        context_window=None,
        max_output_tokens=None,
        executor=None,
    ):
        self._running = False
        self._events = deque()
        self._event_sequence = 0
        self.observation_errors = []
        self.last_result = None
        self._run_exception = None
        self._verification = []
        self._model_final = False
        capabilities = getattr(model_client, "capabilities", None)
        self.token_budget = TokenBudget(context_window if context_window is not None else getattr(capabilities, "context_window", 32768),
                                       max_output_tokens if max_output_tokens is not None else getattr(capabilities, "max_output_tokens", 4096),
                                       source="user-config" if context_window is not None else getattr(capabilities, "limits_source", "conservative-default"))
        self.model_client = model_client
        self.workspace = workspace
        self.root = Path(workspace.repo_root)
        self.executor = executor or LocalExecutor()
        if self.executor.backend == "docker" and self.executor.workspace != self.root.resolve():
            raise ValueError("executor workspace must match the agent workspace")
        self.session_store = session_store
        self.approval_policy = approval_policy
        self.max_run_seconds = max_run_seconds
        self.max_steps = max_steps
        self.max_new_tokens = max_new_tokens
        self.depth = depth
        self.max_depth = max_depth
        self.read_only = read_only
        self.shell_env_allowlist = tuple(shell_env_allowlist or DEFAULT_SHELL_ENV_ALLOWLIST)
        self.secret_env_names = {str(name).upper() for name in (secret_env_names or ())}
        self.feature_flags = dict(DEFAULT_FEATURE_FLAGS)
        if feature_flags:
            self.feature_flags.update({str(key): bool(value) for key, value in feature_flags.items()})
        self.run_store = run_store or RunStore(Path(workspace.repo_root) / ".zzcode" / "runs")
        self.session = session or {
            "id": datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6],
            "created_at": now(),
            "workspace_root": workspace.repo_root,
            "history": [],
            "memory": memorylib.default_memory_state(),
        }
        self._ensure_session_shape()
        self.memory = memorylib.LayeredMemory(
            self.session.setdefault("memory", memorylib.default_memory_state()),
            workspace_root=self.root,
        )
        self.session["memory"] = self.memory.to_dict()
        self.tools = self.build_tools()
        self.prefix_state = self.build_prefix()
        self.prefix = self.prefix_state.text
        self.context_manager = ContextManager(self)
        self.resume_state = self.evaluate_resume_state()
        self.persist_session()
        self.current_task_state = None
        self.current_run_dir = None
        self.last_prompt_metadata = {}
        self.last_completion_metadata = {}
        self.last_durable_promotions = []
        self.last_durable_rejections = []
        self.last_durable_superseded = []
        self.gateway = ToolGateway(self, operation_ledger)
        self._last_prefix_refresh = {
            "workspace_changed": False,
            "prefix_changed": False,
        }

    @classmethod
    def from_session(cls, model_client, workspace, session_store, session_id, **kwargs):
        return cls(
            model_client=model_client,
            workspace=workspace,
            session_store=session_store,
            session=session_store.load(session_id),
            **kwargs,
        )

    def _ensure_session_shape(self):
        self.session.setdefault("history", [])
        self.session.setdefault("compactions", [])
        self.session.setdefault("memory", memorylib.default_memory_state())
        checkpoints = self.session.setdefault("checkpoints", {})
        if not isinstance(checkpoints, dict):
            checkpoints = {}
            self.session["checkpoints"] = checkpoints
        checkpoints.setdefault("current_id", "")
        checkpoints.setdefault("items", {})
        runtime_identity = self.session.setdefault("runtime_identity", {})
        if not isinstance(runtime_identity, dict):
            self.session["runtime_identity"] = {}
        resume_state = self.session.setdefault("resume_state", {})
        if not isinstance(resume_state, dict):
            self.session["resume_state"] = {}

    def current_runtime_identity(self):
        return {
            "session_id": self.session.get("id", ""),
            "cwd": str(self.root),
            "executor": {"backend": self.executor.backend, "image": getattr(self.executor, "image_digest", "")},
            "model": str(getattr(self.model_client, "model", "")),
            "model_client": self.model_client.__class__.__name__,
            "approval_policy": self.approval_policy,
            "read_only": bool(self.read_only),
            "max_steps": int(self.max_steps),
            "max_new_tokens": int(self.max_new_tokens),
            "context_window": self.token_budget.window,
            "max_output_tokens": self.token_budget.max_output,
            "context_limits_source": self.token_budget.source,
            "feature_flags": dict(self.feature_flags),
            "shell_env_allowlist": list(self.shell_env_allowlist),
            "workspace_fingerprint": getattr(getattr(self, "prefix_state", None), "workspace_fingerprint", self.workspace.fingerprint()),
            "tool_signature": self.tool_signature(),
        }

    def checkpoint_state(self):
        self._ensure_session_shape()
        return self.session["checkpoints"]

    def current_checkpoint(self):
        state = self.checkpoint_state()
        checkpoint_id = str(state.get("current_id", "")).strip()
        if not checkpoint_id:
            return None
        return state.get("items", {}).get(checkpoint_id)

    def invalidate_stale_memory(self):
        invalidated = self.memory.invalidate_stale_file_summaries()
        self.session["memory"] = self.memory.to_dict()
        return invalidated

    def evaluate_resume_state(self):
        previous_resume_state = dict(self.session.get("resume_state", {}) or {})
        invalidated = self.invalidate_stale_memory()
        checkpoint = self.current_checkpoint()
        status = CHECKPOINT_NONE_STATUS
        stale_paths = list(invalidated)
        mismatch_fields = []
        if checkpoint:
            if checkpoint.get("schema_version") != CHECKPOINT_SCHEMA_VERSION:
                status = CHECKPOINT_SCHEMA_MISMATCH_STATUS
            else:
                for item in checkpoint.get("key_files", []):
                    path = str(item.get("path", "")).strip()
                    if not path:
                        continue
                    expected = item.get("freshness")
                    current = memorylib.file_freshness(path, self.root)
                    if expected != current and path not in stale_paths:
                        stale_paths.append(path)
                saved_identity = dict(checkpoint.get("runtime_identity", {}) or self.session.get("runtime_identity", {}) or {})
                current_identity = self.current_runtime_identity()
                identity_keys = (
                    "cwd",
                    "model",
                    "model_client",
                    "approval_policy",
                    "read_only",
                    "max_steps",
                    "max_new_tokens",
                    "feature_flags",
                    "shell_env_allowlist",
                    "workspace_fingerprint",
                    "tool_signature",
                )
                for key in identity_keys:
                    if key not in saved_identity:
                        continue
                    if saved_identity.get(key) != current_identity.get(key):
                        mismatch_fields.append(key)
                mismatch_fields.sort()
                if stale_paths:
                    status = CHECKPOINT_PARTIAL_STALE_STATUS
                elif mismatch_fields:
                    status = CHECKPOINT_WORKSPACE_MISMATCH_STATUS
                else:
                    status = CHECKPOINT_FULL_VALID_STATUS

        resume_state = {
            "status": status,
            "stale_paths": stale_paths,
            "runtime_identity_mismatch_fields": mismatch_fields,
            "stale_summary_invalidations": max(
                len(invalidated),
                int(previous_resume_state.get("stale_summary_invalidations", 0))
                if status == CHECKPOINT_PARTIAL_STALE_STATUS
                else 0,
            ),
        }
        self.session["resume_state"] = resume_state
        self.session["runtime_identity"] = self.current_runtime_identity()
        return resume_state

    def render_checkpoint_text(self):
        checkpoint = self.current_checkpoint()
        if not checkpoint:
            return ""
        lines = [
            "Task checkpoint:",
            f"- Resume status: {self.resume_state.get('status', CHECKPOINT_NONE_STATUS)}",
            f"- Current goal: {checkpoint.get('current_goal', '-') or '-'}",
            f"- Current blocker: {checkpoint.get('current_blocker', '-') or '-'}",
            f"- Next step: {checkpoint.get('next_step', '-') or '-'}",
        ]
        key_files = [str(item.get("path", "")).strip() for item in checkpoint.get("key_files", []) if str(item.get("path", "")).strip()]
        lines.append(f"- Key files: {', '.join(key_files) or '-'}")
        if checkpoint.get("completed"):
            lines.append("- Completed: " + " | ".join(str(item) for item in checkpoint.get("completed", [])))
        if checkpoint.get("excluded"):
            lines.append("- Excluded: " + " | ".join(str(item) for item in checkpoint.get("excluded", [])))
        if self.resume_state.get("stale_paths"):
            lines.append("- Stale paths: " + ", ".join(self.resume_state["stale_paths"]))
        summary = str(checkpoint.get("summary", "")).strip()
        if summary:
            lines.append(f"- Summary: {summary}")
        return "\n".join(lines)

    @staticmethod
    def remember(bucket, item, limit):
        if not item:
            return
        if item in bucket:
            bucket.remove(item)
        bucket.append(item)
        del bucket[:-limit]

    def build_tools(self):
        return toolkit.build_tool_registry(self)

    def tool_signature(self):
        payload = []
        for name in sorted(self.tools):
            tool = self.tools[name]
            payload.append(
                {
                    "name": name,
                    "schema": tool["schema"],
                    "risky": tool["risky"],
                    "description": tool["description"],
                }
            )
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()

    def build_prefix(self):
        tool_lines = []
        for name, tool in self.tools.items():
            risk = "approval required" if tool["risky"] else "safe"
            tool_lines.append(f"- {name} [{risk}] {tool['description']}")
        tool_text = "\n".join(tool_lines)
        # prefix 可以理解成 agent 的“工作手册”：
        # 它是谁、工具怎么调用、当前仓库是什么状态，都写在这里。
        text = textwrap.dedent(
            f"""\
            You are zzcode, a small local coding agent working inside a local repository.

            Rules:
            - Use tools instead of guessing about the workspace.
            - Call registered tools using the native tool calling interface.
            - Return your final answer as ordinary text when finished.
            - Never invent tool results.
            - Keep answers concise and concrete.
            - If the user asks you to create or update a specific file and the path is clear, use write_file or patch_file instead of repeatedly listing files.
            - Before writing tests for existing code, read the implementation first.
            - When writing tests, match the current implementation unless the user explicitly asked you to change the code.
            - New files should be complete and runnable, including obvious imports.
            - Do not repeat the same tool call with the same arguments if it did not help. Choose a different tool or return a final answer.
            - Required tool arguments must not be empty. Do not call read_file, write_file, patch_file, run_shell, or delegate with args={{}}.

            Tools:
            {tool_text}

            {self.workspace.text()}
            """
        ).strip()
        if self.executor.backend == "docker":
            text += "\nShell runs in an offline Docker workspace at /workspace. Use relative project paths. Credentials, .git, virtual environments and runtime state are excluded. Regular file changes synchronize before the next tool; host conflicts stop synchronization. Dependencies must be available in the prebuilt image; never enable networking as a fallback."
        return PromptPrefix(
            text=text,
            hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            workspace_fingerprint=self.workspace.fingerprint(),
            tool_signature=self.tool_signature(),
            built_at=now(),
        )

    def _apply_prefix_state(self, prefix_state):
        self.prefix_state = prefix_state
        self.prefix = prefix_state.text

    def refresh_prefix(self, force=False):
        previous_hash = getattr(getattr(self, "prefix_state", None), "hash", None)
        previous_workspace_fingerprint = getattr(getattr(self, "prefix_state", None), "workspace_fingerprint", None)

        # 工作区事实相对稳定，所以这里按整体刷新；
        # 只有这些事实真的变化了，才重建完整 prefix。
        refreshed_workspace = WorkspaceContext.build(self.root)
        refreshed_workspace_fingerprint = refreshed_workspace.fingerprint()
        workspace_changed = force or refreshed_workspace_fingerprint != previous_workspace_fingerprint
        if workspace_changed:
            self.workspace = refreshed_workspace

        prefix_state = self.build_prefix() if workspace_changed or force or previous_hash is None else self.prefix_state
        prefix_changed = force or previous_hash != prefix_state.hash
        if prefix_changed:
            self._apply_prefix_state(prefix_state)

        self._last_prefix_refresh = {
            "workspace_changed": workspace_changed,
            "prefix_changed": prefix_changed,
        }
        return dict(self._last_prefix_refresh)

    def memory_text(self):
        return self.memory.render_memory_text()

    def history_text(self):
        history = self.session["history"]
        if not history:
            return "- empty"

        lines = []
        seen_reads = set()
        recent_start = max(0, len(history) - 6)
        for index, item in enumerate(history):
            recent = index >= recent_start
            if item["role"] == "tool" and item["name"] == "read_file" and not recent:
                path = str(item["args"].get("path", ""))
                if path in seen_reads:
                    continue
                seen_reads.add(path)

            if item["role"] == "tool":
                limit = 900 if recent else 180
                lines.append(f"[tool:{item['name']}] {json.dumps(item['args'], sort_keys=True)}")
                lines.append(clip(item["content"], limit))
            else:
                limit = 900 if recent else 220
                lines.append(f"[{item['role']}] {clip(item['content'], limit)}")

        return clip("\n".join(lines), MAX_HISTORY)

    def feature_enabled(self, name):
        return bool(self.feature_flags.get(str(name), False))

    def prompt(self, user_message):
        prompt, _ = self._build_prompt_and_metadata(user_message)
        return prompt

    def persist_session(self):
        try:
            self.session_path = self.session_store.save(self.session)
        except SessionError as exc:
            task = getattr(self, "current_task_state", None)
            if task and task.status == "running":
                task.stop("session_persistence_error", status="failed", final_answer=str(exc))
                self.run_store.write_task_state(task)
            raise

    def record(self, item):
        self.session["history"].append(item)
        self.persist_session()

    @staticmethod
    def looks_sensitive_env_name(name):
        upper = str(name).upper()
        return any(upper == marker or upper.endswith(marker) or upper.endswith(f"_{marker}") for marker in SENSITIVE_ENV_NAME_MARKERS)

    def is_secret_env_name(self, name):
        upper = str(name).upper()
        return upper in self.secret_env_names or self.looks_sensitive_env_name(upper)

    def configured_secret_env_items(self):
        items = [
            (name, value)
            for name, value in os.environ.items()
            if str(name).upper() in self.secret_env_names and value
        ]
        items.sort(key=lambda item: item[0])
        return items

    def detected_secret_env_items(self):
        items = [
            (name, value)
            for name, value in os.environ.items()
            if self.is_secret_env_name(name) and value
        ]
        items.sort(key=lambda item: item[0])
        return items

    def secret_env_summary(self):
        names = [name for name, _ in self.configured_secret_env_items()]
        return {
            "secret_env_count": len(names),
            "secret_env_names": names,
        }

    def detected_secret_env_summary(self):
        names = [name for name, _ in self.detected_secret_env_items()]
        return {
            "secret_env_count": len(names),
            "secret_env_names": names,
        }

    def redact_text(self, text):
        text = str(text)
        for _, value in sorted(self.detected_secret_env_items(), key=lambda item: len(item[1]), reverse=True):
            text = text.replace(value, REDACTED_VALUE)
        return SECRET_PATTERN.sub(REDACTED_VALUE, text)

    def redact_artifact(self, value, key=None):
        if key and self.is_secret_env_name(key):
            return REDACTED_VALUE
        if isinstance(value, dict):
            return {
                str(item_key): self.redact_artifact(item_value, key=item_key)
                for item_key, item_value in value.items()
            }
        if isinstance(value, list):
            return [self.redact_artifact(item, key=key) for item in value]
        if isinstance(value, tuple):
            return [self.redact_artifact(item, key=key) for item in value]
        if isinstance(value, str):
            redacted = self.redact_text(value)
            return redacted
        return value

    def shell_env(self):
        env = {
            name: os.environ[name]
            for name in self.shell_env_allowlist
            if name in os.environ
        }
        env["PWD"] = str(self.root)
        if "PATH" not in env and os.environ.get("PATH"):
            env["PATH"] = os.environ["PATH"]
        return env

    def prompt_metadata(self, user_message, prompt):
        _, metadata = self._build_prompt_and_metadata(user_message)
        return metadata

    def _build_prompt_and_metadata(self, user_message):
        refresh = self.refresh_prefix()
        self.resume_state = self.evaluate_resume_state()
        prompt, metadata = self.context_manager.build(user_message)
        # 这里把“这轮 prompt 是怎么拼出来的”连同缓存相关状态一起记下来，
        # 后面 trace/report 才能解释清楚：为什么这一轮 prefix 变了、缓存有没有命中。
        metadata.update(
            {
                "prefix_chars": len(self.prefix),
                "workspace_chars": len(self.workspace.text()),
                "memory_chars": len(self.memory_text()),
                "history_chars": len(self.history_text()),
                "request_chars": len(user_message),
                "tool_count": len(self.tools),
                "workspace_docs": len(self.workspace.project_docs),
                "recent_commits": len(self.workspace.recent_commits),
                "prefix_hash": self.prefix_state.hash,
                "prompt_cache_key": self.prefix_state.hash,
                "workspace_fingerprint": self.prefix_state.workspace_fingerprint,
                "tool_signature": self.prefix_state.tool_signature,
                "workspace_changed": refresh["workspace_changed"],
                "prefix_changed": refresh["prefix_changed"],
                "prompt_cache_supported": bool(getattr(self.model_client, "supports_prompt_cache", False)),
                "resume_status": self.resume_state.get("status", CHECKPOINT_NONE_STATUS),
                "stale_summary_invalidations": int(self.resume_state.get("stale_summary_invalidations", 0)),
                "stale_paths": list(self.resume_state.get("stale_paths", [])),
                "runtime_identity_mismatch_fields": list(self.resume_state.get("runtime_identity_mismatch_fields", [])),
            }
        )
        metadata.update(self.detected_secret_env_summary())
        return prompt, metadata

    def emit_trace(self, task_state, event, payload=None):
        payload = self.redact_artifact(payload or {})
        payload["event"] = event
        payload["created_at"] = now()
        public_event = build_event(self, task_state, event, payload)
        if public_event:
            payload["public_event"] = public_event.to_dict()
        try:
            self.run_store.append_trace(task_state, payload)
        except Exception as exc:
            self.diagnostic("trace", exc)
        if public_event:
            self._events.append(public_event)
        return payload

    def capture_workspace_snapshot(self):
        snapshot = {}
        for directory, dirs, files in os.walk(self.root):
            dirs[:] = [name for name in dirs if name not in IGNORED_PATH_NAMES]
            for name in files:
                path = Path(directory) / name
                if name in IGNORED_PATH_NAMES or path.is_symlink():
                    continue
                snapshot[path.relative_to(self.root).as_posix()] = file_hash(path)
        return snapshot

    @staticmethod
    def diff_workspace_snapshots(before, after):
        changed_paths = []
        summaries = []
        all_paths = sorted(set(before) | set(after))
        for path in all_paths:
            if before.get(path) == after.get(path):
                continue
            changed_paths.append(path)
            if path not in before:
                summaries.append(f"created:{path}")
            elif path not in after:
                summaries.append(f"deleted:{path}")
            else:
                summaries.append(f"modified:{path}")
        return changed_paths, summaries

    def create_checkpoint(self, task_state, user_message, trigger):
        state = self.checkpoint_state()
        current = self.current_checkpoint()
        checkpoint_id = "ckpt_" + uuid.uuid4().hex[:8]
        key_files = []
        freshness = {}
        for path in self.memory.to_dict()["working"]["recent_files"]:
            file_freshness = memorylib.file_freshness(path, self.root)
            freshness[path] = file_freshness
            key_files.append({"path": path, "freshness": file_freshness})
        checkpoint = {
            "checkpoint_id": checkpoint_id,
            "session_entry_id": self.session["history"][-1]["entry_id"] if self.session["history"] else "",
            "compaction_id": self.session["compactions"][-1]["compaction_id"] if self.session["compactions"] else "",
            "operation_ids": [row[0] for row in self.gateway.ledger.connection.execute("SELECT operation_id FROM operations WHERE run_id=?", (task_state.run_id,))],
            "parent_checkpoint_id": current.get("checkpoint_id", "") if current else "",
            "schema_version": CHECKPOINT_SCHEMA_VERSION,
            "created_at": now(),
            "current_goal": str(user_message),
            "completed": [task_state.final_answer] if task_state.final_answer else [],
            "excluded": [],
            "current_blocker": "" if str(task_state.stop_reason or "") in ("", "final_answer_returned") else str(task_state.stop_reason),
            "next_step": self.infer_next_step(task_state),
            "key_files": key_files,
            "freshness": freshness,
            "summary": f"{trigger}: {clip(str(user_message), 120)}",
            "runtime_identity": self.current_runtime_identity(),
        }
        state["items"][checkpoint_id] = checkpoint
        state["current_id"] = checkpoint_id
        task_state.checkpoint_id = checkpoint_id
        self.session["runtime_identity"] = checkpoint["runtime_identity"]
        self.persist_session()
        return checkpoint

    def infer_next_step(self, task_state):
        if task_state.status == "completed":
            return "No next step recorded."
        if task_state.stop_reason == "step_limit_reached":
            return "Resume from the latest checkpoint and continue the task."
        if task_state.last_tool:
            return f"Decide the next action after {task_state.last_tool}."
        return "Continue the task from the latest checkpoint."

    def update_memory_after_tool(self, name, args, result):
        """把少量高价值工具结果沉淀到 working memory。

        为什么存在：
        并不是每个工具结果都值得长期带进下一轮 prompt。完整结果已经进了
        `history`，这里只挑少量“下一轮大概率还会用到”的事实做提纯，
        例如最近读写过哪些文件、某个文件读出来的短摘要。

        输入 / 输出：
        - 输入：工具名 `name`、参数 `args`、执行结果 `result`
        - 输出：无显式返回值，副作用是更新 `self.memory`

        在 agent 链路里的位置：
        它发生在 `run_tool()` 真正执行完工具之后、下一轮 prompt 组装之前。
        也就是说：工具结果先进入完整历史，再由这个函数择优沉淀成轻量记忆。
        """
        if not self.feature_enabled("memory"):
            return
        path = args.get("path")
        if not path:
            return

        canonical_path = self.memory.canonical_path(path)
        # 不是所有工具结果都进入工作记忆。
        # 读文件会生成摘要；写文件/patch 会让旧摘要失效，因为它们可能过期了。
        if name in {"read_file", "write_file", "patch_file"}:
            self.memory.remember_file(canonical_path)
        if name == "read_file":
            summary = memorylib.summarize_read_result(result)
            self.memory.set_file_summary(canonical_path, summary)
            self.memory.append_note(summary, tags=(canonical_path,), source=canonical_path)
        elif name in {"write_file", "patch_file"}:
            self.memory.invalidate_file_summary(canonical_path)

    def note_tool(self, name, args, result):
        self.update_memory_after_tool(name, args, result)

    def record_process_note_for_tool(self, name, metadata):
        status = str(metadata.get("tool_status", "")).strip()
        if status not in {"partial_success", "error", "rejected"}:
            return
        affected_paths = [str(path).strip() for path in metadata.get("affected_paths", []) if str(path).strip()]
        path_text = ", ".join(affected_paths) or "workspace"
        if status == "partial_success":
            text = f"{name} partial_success on {path_text}; inspect diff before retry"
        elif status == "error":
            text = f"{name} error on {path_text}; check the failure before retry"
        else:
            text = f"{name} rejected; choose a different action before retry"
        tags = ["process", status, *affected_paths]
        self.memory.append_note(text, tags=tuple(tags), source=name, kind="process")
        self.session["memory"] = self.memory.to_dict()

    def reject_durable_reason(self, note_text):
        text = str(note_text or "").strip()
        lowered = text.lower()
        if not text:
            return "empty"
        if REDACTED_VALUE in text or SECRET_SHAPED_TEXT_PATTERN.search(text):
            return "secret_shaped"
        checkpoint_like_prefixes = (
            "current goal",
            "current blocker",
            "next step",
            "current phase",
            "key files",
            "freshness",
            "当前目标",
            "当前卡点",
            "下一步",
            "当前阶段",
            "关键文件",
            "已完成",
            "已排除",
        )
        if any(lowered.startswith(prefix) for prefix in checkpoint_like_prefixes):
            return "transient_task_state"
        if re.search(r"(?i)\b(stdout|stderr|traceback|exit_code)\b", text) or len(text) > 220:
            return "noisy_output"
        return ""

    def extract_durable_promotions(self, user_message, final_answer):
        user_text = str(user_message or "")
        if not (DURABLE_MEMORY_INTENT_PATTERN.search(user_text) or DURABLE_MEMORY_INTENT_ZH_PATTERN.search(user_text)):
            return [], []
        promotions = []
        rejections = []
        for line in str(final_answer or "").splitlines():
            text = line.strip()
            if not text or REDACTED_VALUE in text:
                continue
            for topic, pattern in DURABLE_MEMORY_LINE_PATTERNS:
                match = pattern.match(text)
                if not match:
                    continue
                note_text = match.group(1).strip()
                if note_text:
                    reason = self.reject_durable_reason(note_text)
                    if reason:
                        rejections.append(f"{topic}:{reason}")
                        break
                    promotions.append((topic, note_text))
                break
        return promotions, rejections

    def promote_durable_memory(self, user_message, final_answer):
        promotions, rejections = self.extract_durable_promotions(user_message, final_answer)
        promoted, superseded = self.memory.promote_durable(promotions)
        self.session["memory"] = self.memory.to_dict()
        self.last_durable_promotions = promoted
        self.last_durable_rejections = rejections
        self.last_durable_superseded = superseded
        return promoted, rejections, superseded

    def ask(self, user_message):
        """兼容字符串接口，也消费唯一的事件循环。"""
        result = self.run_to_completion(RunRequest(user_message, task_type="question"))
        if self._run_exception is not None:
            raise self._run_exception
        return result.final_answer

    def run_to_completion(self, request):
        for _event in self.run(request):
            pass
        return self.last_result

    def run(self, request):
        from .loop import run_loop
        if self._running:
            raise RuntimeError("an agent cannot run two requests concurrently")
        if not isinstance(request, RunRequest):
            raise TypeError("run requires RunRequest")
        self._running = True
        self._events.clear()
        self._event_sequence = 0
        self._run_exception = None
        self.last_result = None
        self.current_task_state = None
        self._verification = []
        self._model_final = False
        try:
            yield from run_loop(self, request)
        except GeneratorExit:
            try:
                self.cancel_run()
            except Exception as exc:
                self._run_exception = exc
                self.current_task_state.stop("cancellation_persistence_error", status="failed", final_answer=self.redact_text(str(exc)))
                self.emit_trace(self.current_task_state, "run_failed", {"error": self.redact_text(str(exc))})
                raise
            raise
        except Exception as exc:
            self._run_exception = exc
            task = self.current_task_state
            if task:
                task.stop("runtime_error" if task.status == "running" else task.stop_reason, status="failed", final_answer=self.redact_text(str(exc)))
                # 重要存储失败不会被观测错误处理掩盖；仍向消费者报告失败。
                try:
                    self.run_store.write_task_state(task)
                except OSError as store_error:
                    self.diagnostic("task_state", store_error)
                self.emit_trace(task, "run_failed", {"status": "failed", "stop_reason": task.stop_reason, "error": self.redact_text(str(exc))})
            self.last_result = self.build_result()
            yield from self.drain_events()
        finally:
            if self.last_result is None:
                self.last_result = self.build_result()
            self._running = False

    def drain_events(self):
        while self._events:
            yield self._events.popleft()

    def diagnostic(self, component, exc):
        message = self.redact_text(f"{component}: {exc}")
        self.observation_errors.append(message)
        try:
            print(message, file=sys.stderr)
        except OSError:
            pass  # 诊断输出不可写时，仍保留 observation_errors，不重试已执行操作。

    def publish_report(self, task, report):
        try:
            return self.run_store.write_report(task, report)
        except Exception as exc:
            self.diagnostic("report", exc)

    def build_result(self):
        task = self.current_task_state
        return AgentResult(
            run_id=task.run_id if task else "", status=task.status if task else "failed",
            stop_reason=task.stop_reason if task else "runtime_error",
            final_answer=self.redact_text(task.final_answer) if task else self.redact_text(str(self._run_exception)),
            tool_steps=task.tool_steps if task else 0, attempts=task.attempts if task else 0,
            trace_path=str(self.run_store.trace_path(task)) if task else "",
            report_path=str(self.run_store.report_path(task)) if task else "",
            model_final=self._model_final, verification=freeze(self._verification),
            error=self.redact_text(str(self._run_exception)) if self._run_exception else None,
        )

    def cancel_run(self):
        task = self.current_task_state
        if task is None or task.status != "running":
            return
        pending = {}
        for item in self.session["history"]:
            for block in item.get("message", {}).get("content", []):
                if block["type"] == "tool_call":
                    pending[block["call_id"]] = block
                elif block["type"] == "tool_result":
                    pending.pop(block["call_id"], None)
        for call_id, call in pending.items():
            row = self.gateway.ledger.get(self.session["id"], call_id)
            result = self.gateway.ledger.result(row) if row else self.run_tool(call["name"], call["arguments"], call_id=call_id, cancel_reason="consumer closed the event iterator")
            if result is None:
                raise PersistenceError("executed operation requires reconciliation before cancellation")
            self.record({"role": "tool", "name": call["name"], "args": call["arguments"], "content": result.content,
                         "message": Message("tool", (result,)).to_dict(), "created_at": now()})
        task.stop("cancelled", final_answer="Run cancelled by consumer.")
        self.run_store.write_task_state(task)
        self.emit_trace(task, "run_finished", {"status": task.status, "stop_reason": task.stop_reason})
        self.publish_report(task, self.redact_artifact(self.build_report(task)))
        self.last_result = self.build_result()

    def run_tool(self, name, args, *, call_id=None, argument_error=None, cancel_reason=None):
        try:
            result = self.gateway.execute(ToolCall(call_id or "call_" + uuid.uuid4().hex, name, args, argument_error), cancel_reason=cancel_reason)
        except (PersistenceError, OperationBusyError) as exc:
            if self.current_task_state:
                self.current_task_state.stop("persistence_error" if isinstance(exc, PersistenceError) else "operation_busy", status="failed", final_answer=str(exc))
                self.run_store.write_task_state(self.current_task_state)
                self.publish_report(self.current_task_state, self.redact_artifact(self.build_report(self.current_task_state)))
            raise
        if result.status in {"succeeded", "partial_success"}:
            self.update_memory_after_tool(name, args, result.content)
        self.record_process_note_for_tool(name, self.tool_metadata(result))
        return result

    @staticmethod
    def tool_metadata(result):
        # Trace 是统一结果的投影，不再维护另一份执行状态。
        return {"operation_id": result.operation_id, "tool_status": {"succeeded": "ok", "failed": "error"}.get(result.status, result.status),
                "tool_error_code": result.error_code, "security_event_type": result.security_event_type,
                "risk_level": result.risk_level, "read_only": result.read_only,
                "affected_paths": list(result.affected_paths), "workspace_changed": bool(result.affected_paths),
                "diff_summary": list(result.diff_summary), "exit_code": result.exit_code,
                "truncated": result.truncated, "artifact_refs": list(result.artifact_refs)}

    def repeated_tool_call(self, name, args):
        # agent 很常见的一种坏循环，是在没有新信息的情况下反复发起同一调用。
        # 这里提前挡掉最简单的这种循环。
        tool_events = [item for item in self.session["history"][getattr(self, "_turn_start_index", 0):] if item["role"] == "tool"]
        if len(tool_events) < 2:
            return False
        recent = tool_events[-2:]
        return all(item["name"] == name and item["args"] == args for item in recent)

    @staticmethod
    def new_task_id():
        return "task_" + datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]

    @staticmethod
    def new_run_id():
        return "run_" + datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]

    def build_report(self, task_state):
        # report 是一次运行的最终摘要；
        # 和 trace 的区别在于，trace 关注过程，report 关注结果与关键指标。
        return {
            "runtime_result": self.build_result().to_dict(),
            "run_id": task_state.run_id,
            "task_id": task_state.task_id,
            "status": task_state.status,
            "stop_reason": task_state.stop_reason,
            "final_answer": task_state.final_answer,
            "tool_steps": task_state.tool_steps,
            "attempts": task_state.attempts,
            "tool_counters": self.gateway.ledger.counts(task_state.run_id),
            "model_requests": getattr(self, "current_model_requests", 0),
            "transport_retries": getattr(self, "current_transport_retries", 0),
            "compaction_count": len(self.session["compactions"]),
            "context_budget": {"window":self.token_budget.window,"max_output":self.token_budget.max_output,"source":self.token_budget.source},
            "checkpoint_id": task_state.checkpoint_id,
            "resume_status": task_state.resume_status,
            "task_state": task_state.to_dict(),
            "prompt_metadata": self.last_prompt_metadata,
            "durable_promotions": list(self.last_durable_promotions),
            "durable_rejections": list(self.last_durable_rejections),
            "durable_superseded": list(self.last_durable_superseded),
            "redacted_env": self.detected_secret_env_summary(),
        }

    def tool_example(self, name):
        return toolkit.tool_example(name)

    def validate_tool(self, name, args):
        """把通用工具校验和 runtime 级额外约束串起来。"""
        if name == "delegate" and self.depth >= self.max_depth:
            raise ValueError("delegate depth exceeded")
        toolkit.validate_tool(self, name, args)

    def tool_list_files(self, args):
        return self.run_tool("list_files", args)

    def tool_read_file(self, args):
        return self.run_tool("read_file", args)

    def tool_search(self, args):
        return self.run_tool("search", args)

    def tool_run_shell(self, args):
        return self.run_tool("run_shell", args)

    def tool_write_file(self, args):
        return self.run_tool("write_file", args)

    def tool_patch_file(self, args):
        return self.run_tool("patch_file", args)

    def tool_delegate(self, args):
        return self.run_tool("delegate", args)

    def approve(self, name, args):
        return approve_operation(self.approval_policy, self.read_only, name, args)

    @staticmethod
    def retry_notice(problem=None):
        return f"Runtime notice: {problem or 'invalid model response'}. Use native tool calls or return a non-empty answer."

    def reset(self):
        self.session["history"] = []
        self.session["compactions"] = []
        self.session["checkpoints"] = {"items":{},"current_id":""}
        self.session["memory"].clear()
        self.session["memory"].update(memorylib.default_memory_state())
        self.memory = memorylib.LayeredMemory(self.session["memory"], workspace_root=self.root)
        self.persist_session()

    def path(self, raw_path):
        path = Path(raw_path)
        path = path if path.is_absolute() else self.root / path
        resolved = path.resolve()
        # 所有文件类工具都被锚定在 workspace root 之下。
        # 这样既能防住 "../" 逃逸，也能防住符号链接解析后跳出仓库。
        if os.path.commonpath([str(self.root), str(resolved)]) != str(self.root):
            raise ValueError(f"path escapes workspace: {raw_path}")
        return resolved


MiniAgent = ZZCode
