"""SDK 与 CLI 共用的请求、不可变事件和结果。"""
from dataclasses import dataclass, field
from types import MappingProxyType
from collections.abc import Mapping


def freeze(value):
    if isinstance(value, Mapping):
        return MappingProxyType({key: freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(freeze(item) for item in value)
    return value


def thaw(value):
    if isinstance(value, Mapping):
        return {key: thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [thaw(item) for item in value]
    return value


@dataclass(frozen=True)
class RunRequest:
    prompt: str
    task_type: str = 'auto'
    verification_commands: tuple[str, ...] = ()
    max_final_rejections: int = 2

    def __post_init__(self):
        if self.task_type not in {'auto', 'question', 'investigation', 'code_change'}:
            raise ValueError('invalid task type')
        if not self.prompt.strip() or self.max_final_rejections < 1:
            raise ValueError('prompt and positive rejection limit are required')
        object.__setattr__(self, 'verification_commands', tuple(self.verification_commands))


@dataclass(frozen=True)
class AgentEvent:
    run_id: str
    sequence: int
    type: str
    payload: Mapping
    correlation_id: str
    version: int = 1

    @property
    def event_id(self):
        return f'{self.run_id}:{self.sequence}'

    def __post_init__(self):
        object.__setattr__(self, 'payload', freeze(self.payload))

    def to_dict(self):
        return {'version': self.version, 'event_id': self.event_id, 'run_id': self.run_id,
                'sequence': self.sequence, 'type': self.type, 'correlation_id': self.correlation_id,
                'payload': thaw(self.payload)}


@dataclass(frozen=True)
class AgentResult:
    run_id: str
    status: str
    stop_reason: str
    final_answer: str
    tool_steps: int
    attempts: int
    trace_path: str
    report_path: str
    model_final: bool = False
    verification: tuple = field(default_factory=tuple)
    error: str | None = None
    # 独立评分器负责 resolved，运行时不能替它做结论。
    resolved: bool | None = None

    def __post_init__(self):
        object.__setattr__(self, 'verification', freeze(self.verification))

    def to_dict(self):
        return {key: thaw(getattr(self, key)) for key in self.__dataclass_fields__}
