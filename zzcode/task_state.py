"""一次 ask() 运行过程中的状态机快照。

它回答的是：这次用户请求当前进行到哪了、调了多少次工具、最后为什么停下。
这个对象会被不断写入 task_state.json，供运行中观察和运行后复盘。
"""

from dataclasses import asdict, dataclass, field
from datetime import datetime
from uuid import uuid4

STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_STOPPED = "stopped"
STATUS_FAILED = "failed"

STOP_REASON_FINAL_ANSWER_RETURNED = "final_answer_returned"
STOP_REASON_STEP_LIMIT_REACHED = "step_limit_reached"
STOP_REASON_RETRY_LIMIT_REACHED = "retry_limit_reached"
STOP_REASON_MODEL_ERROR = "model_error"
STOP_REASON_TOOL_TIMEOUT = "tool_timeout"
STOP_REASON_APPROVAL_DENIED = "approval_denied"
STOP_REASON_DELEGATE_FAILED = "delegate_failed"
STOP_REASON_PERSISTENCE_ERROR = "persistence_error"
STOP_REASON_RESUME_LOAD_ERROR = "resume_load_error"
STOP_REASON_COMPLETION_GATE_FAILED = "completion_gate_failed"


@dataclass
class CodingProgress:
    phase: str = "EXPLORE"
    changed_paths: list[str] = field(default_factory=list)
    last_mutation_step: int = 0
    current_patch_digest: str = ""
    verified_patch_digest: str = ""
    last_verification: dict = field(default_factory=dict)
    consecutive_read_only: int = 0
    redundant_read_rejections: int = 0
    final_rejections: int = 0
    unmet_gates: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, value):
        value = value if isinstance(value, dict) else {}
        return cls(
            phase=str(value.get("phase", "EXPLORE")),
            changed_paths=list(value.get("changed_paths", [])),
            last_mutation_step=int(value.get("last_mutation_step", 0)),
            current_patch_digest=str(value.get("current_patch_digest", "")),
            verified_patch_digest=str(value.get("verified_patch_digest", "")),
            last_verification=dict(value.get("last_verification", {})),
            consecutive_read_only=int(value.get("consecutive_read_only", 0)),
            redundant_read_rejections=int(value.get("redundant_read_rejections", 0)),
            final_rejections=int(value.get("final_rejections", 0)),
            unmet_gates=list(value.get("unmet_gates", [])),
        )

    def to_dict(self):
        return asdict(self)


@dataclass
class TaskState:
    run_id: str
    task_id: str
    user_request: str
    status: str = STATUS_RUNNING
    tool_steps: int = 0
    attempts: int = 0
    last_tool: str = ""
    stop_reason: str = ""
    final_answer: str = ""
    checkpoint_id: str = ""
    resume_status: str = ""
    requested_mode: str = "general"
    effective_mode: str = "general"
    intent: dict = field(default_factory=dict)
    coding_progress: CodingProgress = field(default_factory=CodingProgress)

    @classmethod
    def create(cls, task_id, user_request, run_id=""):
        if not run_id:
            run_id = "run_" + datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:6]
        return cls(run_id=run_id, task_id=task_id, user_request=user_request)

    @classmethod
    def from_dict(cls, data):
        return cls(
            run_id=str(data.get("run_id", "")),
            task_id=str(data.get("task_id", "")),
            user_request=str(data.get("user_request", "")),
            status=str(data.get("status", STATUS_RUNNING)),
            tool_steps=int(data.get("tool_steps", 0)),
            attempts=int(data.get("attempts", 0)),
            last_tool=str(data.get("last_tool", "")),
            stop_reason=str(data.get("stop_reason", "")),
            final_answer=str(data.get("final_answer", "")),
            checkpoint_id=str(data.get("checkpoint_id", "")),
            resume_status=str(data.get("resume_status", "")),
            requested_mode=str(data.get("requested_mode", "general")),
            effective_mode=str(data.get("effective_mode", "general")),
            intent=dict(data.get("intent", {})),
            coding_progress=CodingProgress.from_dict(data.get("coding_progress", {})),
        )

    def record_attempt(self):
        # attempt 统计的是“模型被调用了几轮”，不等于 tool_steps。
        self.attempts += 1
        return self

    def record_tool(self, name):
        # tool_steps 只统计真正进入执行阶段的工具调用次数。
        self.tool_steps += 1
        self.last_tool = str(name or "")
        return self

    def stop(self, stop_reason, status=STATUS_STOPPED, final_answer=""):
        # stop_reason 和 status 分开存，是为了区分“怎么停的”和“停下时是什么状态”。
        self.status = status
        self.stop_reason = stop_reason
        if final_answer != "":
            self.final_answer = final_answer
        return self

    def stop_step_limit(self, final_answer=""):
        return self.stop(STOP_REASON_STEP_LIMIT_REACHED, final_answer=final_answer)

    def stop_retry_limit(self, final_answer=""):
        return self.stop(STOP_REASON_RETRY_LIMIT_REACHED, final_answer=final_answer)

    def stop_model_error(self, final_answer=""):
        return self.stop(STOP_REASON_MODEL_ERROR, status=STATUS_FAILED, final_answer=final_answer)

    def finish_success(self, final_answer):
        self.status = STATUS_COMPLETED
        self.stop_reason = STOP_REASON_FINAL_ANSWER_RETURNED
        self.final_answer = str(final_answer)
        return self

    def stop_completion_gate(self, final_answer=""):
        return self.stop(STOP_REASON_COMPLETION_GATE_FAILED, final_answer=final_answer)

    def to_dict(self):
        return {
            "run_id": self.run_id,
            "task_id": self.task_id,
            "user_request": self.user_request,
            "status": self.status,
            "tool_steps": self.tool_steps,
            "attempts": self.attempts,
            "last_tool": self.last_tool,
            "stop_reason": self.stop_reason,
            "final_answer": self.final_answer,
            "checkpoint_id": self.checkpoint_id,
            "resume_status": self.resume_status,
            "requested_mode": self.requested_mode,
            "effective_mode": self.effective_mode,
            "intent": dict(self.intent),
            "coding_progress": self.coding_progress.to_dict(),
        }
