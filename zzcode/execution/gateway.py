"""工具的唯一执行入口：校验、审批、持久化和执行共用一条流程。"""

import hashlib
import json
from dataclasses import replace

from .. import tools as toolkit
from ..core.messages import ToolResult
from .files import FileConflictError, atomic_write, file_hash, prepare_file
from .ledger import OperationBusyError, OperationLedger, PersistenceError, ensure_inactive
from .output import ArtifactStore, ToolOutput
from .shell import ShellOutcome
from ..context.compaction import validation_signature


def arguments_hash(arguments):
    return hashlib.sha256(json.dumps(arguments, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class ToolGateway:
    def __init__(self, agent, ledger=None):
        self.agent = agent
        self.ledger = ledger or OperationLedger(agent.root / ".zzcode")
        self.artifacts = ArtifactStore(agent)
        self.fault_hook = None
        if ledger is None:
            self.recover()

    def _fault(self, stage, operation_id):
        if self.fault_hook:
            self.fault_hook(stage, operation_id)

    def recover(self):
        with self.ledger.writer():
            for row in self.ledger.unsettled():
                details = json.loads(row["details"])
                ensure_inactive(details)
                if row["state"] in {"unknown", "partial_success"} and row["recovery"] == "manual" and row["result"]:
                    continue
                status = "unknown"
                message = "Execution outcome unknown; inspect workspace and resolve the operation before another write."
                error = "recovery_required"
                if row["state"] == "planned":
                    status, message, error = "cancelled", "Cancelled before execution after interruption.", "interrupted_before_execution"
                elif row["recovery"] == "file" and details.get("path"):
                    try:
                        path = self.agent.path(details["path"])
                        lexical = self.agent.root / details["path"]
                        current = "file_conflict" if lexical.is_symlink() or path.is_dir() else file_hash(path)
                    except (ValueError, OSError):
                        current = "file_conflict"
                    details["reconciliation"] = {"observed_hash": current}
                    self.ledger.update_details(row["operation_id"], details)
                    if current == details["after_hash"]:
                        status, message, error = "succeeded", "Recovered completed file operation by full-file hash.", ""
                    elif current == details["before_hash"]:
                        status, message, error = "failed", "File still matches its pre-state; a new approved attempt is safe.", "interrupted_before_effect"
                    else:
                        message, error = "File conflict after interruption; refusing to overwrite.", "file_conflict"
                elif row["recovery"] == "reread":
                    status, message, error = "cancelled", "Interrupted read; read current workspace again.", "interrupted_read"
                result = ToolResult(row["call_id"], row["name"], message, status, row["operation_id"], error_code=error,
                                    workspace_identity=str(self.agent.root))
                self.ledger.finish(result)

    def _base(self, call, operation_id, spec):
        return ToolResult(call.call_id, call.name, "", operation_id=operation_id,
                          workspace_identity=str(self.agent.root), risk_level=spec.risk_level if spec else "high",
                          read_only=spec.side_effect == "none" if spec else False)

    def execute(self, call, *, cancel_reason=None):
        # JSON 复制冻结参数，审批函数和执行函数不会共享可变对象。
        arguments = json.loads(json.dumps(call.arguments))
        signature = arguments_hash(arguments)
        with self.ledger.writer():
            existing = self.ledger.get(self.agent.session["id"], call.call_id)
            if existing:
                if existing["name"] != call.name or existing["args_hash"] != signature:
                    raise ValueError("call ID is already bound to different arguments")
                result = self.ledger.result(existing)
                if result is None:
                    raise OperationBusyError("operation must be reconciled before replay")
                return result
            spec = next((item for item in toolkit.native_tool_specs(self.agent.tools) if item.name == call.name), None)
            details = {}
            prepared = None
            rejection = None
            error = "invalid_arguments"
            if spec is None:
                rejection, error = f"error: unknown tool '{call.name}'", "unknown_tool"
            elif call.argument_error:
                rejection = "error: " + call.argument_error
            else:
                try:
                    self.agent.validate_tool(call.name, arguments)
                    if call.name in {"write_file", "patch_file"}:
                        prepared = prepare_file(self.agent, call.name, arguments)
                        details = prepared[2]
                except (ValueError, KeyError, TypeError, OSError) as exc:
                    rejection = f"error: invalid arguments for {call.name}: {exc}\nexample: {toolkit.tool_example(call.name)}"
            if spec and spec.side_effect != "none" and self.agent.read_only:
                rejection, error = f"error: approval denied for {call.name}", "approval_denied"
            operation_id = self.ledger.plan(self.agent.session["id"], self.agent.current_task_state.run_id if self.agent.current_task_state else "manual", call.call_id, call.name, signature,
                                            spec.recovery if spec else "manual", details)
            result = self._base(call, operation_id, spec)
            self._fault("after_planned", operation_id)

            def finish(status, content, error_code="", **fields):
                content, truncated, refs = self.artifacts.present(content)
                value = replace(result, status=status, content=content, error_code=error_code,
                                truncated=truncated, artifact_refs=refs, **fields)
                self.ledger.finish(value)
                return value

            if cancel_reason:
                return finish("cancelled", "Tool call cancelled: " + cancel_reason, "cancelled")
            if rejection:
                security = "read_only_block" if error == "approval_denied" and self.agent.read_only else "path_escape" if "path escapes workspace" in rejection else ""
                return finish("failed" if call.argument_error else "rejected", rejection, error, security_event_type=security)
            if self.agent.repeated_tool_call(call.name, arguments):
                return finish("rejected", f"error: repeated identical tool call for {call.name}; choose a different tool or return a final answer", "repeated_identical_call")
            if spec.side_effect != "none" and any(row["operation_id"] != operation_id for row in self.ledger.unsettled()):
                return finish("rejected", "error: unresolved operation; inspect and resolve it before another write", "recovery_required")
            approval_arguments = json.loads(json.dumps(arguments))
            if not result.read_only and not self.agent.approve(call.name, approval_arguments):
                security = "read_only_block" if self.agent.read_only else "approval_denied"
                return finish("rejected", f"error: approval denied for {call.name}", "approval_denied", security_event_type=security)
            if arguments_hash(approval_arguments) != signature:
                return finish("rejected", "error: parameters changed during approval; submit a new proposal", "approval_parameters_changed")
            before = self.agent.capture_workspace_snapshot() if not result.read_only else {}
            self.ledger.start(operation_id)
            self._fault("after_running", operation_id)
            try:
                if prepared:
                    path, data, file_details = prepared
                    atomic_write(path, data, file_details)
                    self._fault("after_replace", operation_id)
                    content = (f"wrote {file_details['path']} ({len(arguments['content'])} chars)" if call.name == "write_file" else f"patched {file_details['path']}")
                    outcome = None
                elif call.name == "read_artifact":
                    content = self.artifacts.read(arguments["artifact_id"], arguments.get("start", 1), arguments.get("end", 80))
                    outcome = None
                elif call.name == "run_shell":
                    def on_start(pid):
                        if isinstance(pid, dict):
                            details.update(pid)
                        else:
                            details["pid"] = pid
                        self.ledger.update_details(operation_id, details)
                    outcome = self.agent.tools[call.name]["run"](arguments, on_start=on_start)
                    if not isinstance(outcome, ShellOutcome):
                        raise TypeError("shell executor must return ShellOutcome")
                    content = outcome.display()
                else:
                    content = self.agent.tools[call.name]["run"](arguments)
                    outcome = None
            except (PersistenceError, OperationBusyError):
                raise
            except Exception as exc:
                after = self.agent.capture_workspace_snapshot() if not result.read_only else before
                paths, diffs = self.agent.diff_workspace_snapshots(before, after)
                status = "failed" if isinstance(exc, FileConflictError) else "partial_success" if paths else "unknown" if spec.side_effect == "shell" else "failed"
                error = "file_conflict" if isinstance(exc, FileConflictError) else "recovery_required" if spec.side_effect == "shell" else "tool_failed"
                return finish(status, f"error: tool {call.name} failed: {exc}", error, affected_paths=tuple(paths), diff_summary=tuple(diffs))
            source_truncated = isinstance(content, ToolOutput) and content.truncated
            if isinstance(content, ToolOutput):
                content = content.content
            after = self.agent.capture_workspace_snapshot() if not result.read_only else before
            paths, diffs = self.agent.diff_workspace_snapshots(before, after)
            status = "succeeded"
            error = ""
            if outcome and (outcome.timed_out or outcome.cancelled):
                status = "partial_success" if paths else "unknown"
                error = "tool_timeout" if outcome.timed_out else "cancelled"
            elif outcome and outcome.exit_code != 0:
                status = "partial_success" if paths else "failed"
                error = "tool_partial_success" if paths else "tool_failed"
            content, truncated, refs = self.artifacts.present(content)
            evidence = {}
            if outcome:
                # 验证结论绑定执行时的命令、环境及完整工作区；摘要不能替代这些事实。
                evidence = {"workspace_digest": arguments_hash(after),
                            "patch_digest": arguments_hash({path:[before.get(path),after.get(path)] for path in paths}),
                            "command_digest": arguments_hash({"command":arguments["command"]}),
                            "validation_signature":validation_signature(self.agent)}
            value = replace(result, execution_evidence=evidence, status=status, content=content, error_code=error, affected_paths=tuple(paths), diff_summary=tuple(diffs),
                            exit_code=outcome.exit_code if outcome else None, timed_out=outcome.timed_out if outcome else False,
                            truncated=truncated or source_truncated or bool(outcome and outcome.truncated), artifact_refs=refs)
            self._fault("before_terminal", operation_id)
            # 执行后提交失败直接中止，保留 running 供恢复核对，绝不返回普通失败。
            self.ledger.finish(value)
            return value
