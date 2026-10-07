"""完成判断是运行策略，不能由观测订阅者决定。"""
from dataclasses import dataclass
import time
from zzcode.context.compaction import validation_signature
from zzcode.execution.gateway import arguments_hash


@dataclass(frozen=True)
class CompletionDecision:
    accepted: bool
    reason: str = ''
    retryable: bool = False


class CompletionPolicy:
    def __init__(self, request, agent):
        self.request = request
        self.initial = agent.capture_workspace_snapshot()
        self.rejections = 0

    def reject(self, reason, *, retryable=True):
        self.rejections += 1
        return CompletionDecision(False, reason, retryable)

    def assess(self, final, agent):
        changed = self.initial != agent.capture_workspace_snapshot()
        kind = self.request.task_type
        if kind == 'auto':
            kind = 'code_change' if changed else 'question'
        if kind == 'question':
            return CompletionDecision(True)
        if kind == 'investigation':
            return self.reject('investigation_changed_workspace') if changed else CompletionDecision(bool(final.strip()), 'investigation_requires_explanation')
        if not self.request.verification_commands:
            return self.reject('verification_missing: configure verification commands for code changes', retryable=False)
        if agent.current_task_state.tool_steps + len(self.request.verification_commands) > agent.max_steps:
            return self.reject('verification_budget_exhausted', retryable=False)
        # 验证也走同一个 Gateway；不能绕过审批、工具预算或操作账本。
        for command in self.request.verification_commands:
            remaining = int(agent._run_deadline - time.monotonic())
            if remaining < 1:
                return self.reject('verification_time_limit', retryable=False)
            agent.current_task_state.record_tool('run_shell')
            agent.run_store.write_task_state(agent.current_task_state)
            result = agent.run_tool('run_shell', {'command': command, 'timeout': min(20, remaining)})
            evidence = dict(result.execution_evidence)
            agent._verification.append({'command': agent.redact_text(command), 'operation_id': result.operation_id,
                                        'status': result.status, 'exit_code': result.exit_code, 'evidence': evidence})
            agent.emit_trace(agent.current_task_state, 'tool_executed', {'call_id': result.call_id, 'name': 'run_shell', **agent.tool_metadata(result)})
            if result.status != 'succeeded' or result.exit_code != 0:
                return self.reject('verification_failed')
        current = arguments_hash(agent.capture_workspace_snapshot())
        signature = validation_signature(agent)
        # 后面的命令若又修改了工作区，前面的验证证据立即失效。
        if any(item['evidence'].get('workspace_digest') != current or item['evidence'].get('validation_signature') != signature for item in agent._verification[-len(self.request.verification_commands):]):
            return self.reject('verification_stale')
        return CompletionDecision(True)
