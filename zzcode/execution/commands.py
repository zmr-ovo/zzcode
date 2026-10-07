"""Local 与 Docker 共用的命令契约；Provider 请求不属于执行平面。"""
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Callable, Mapping, Protocol

from .output import OUTPUT_LIMIT


@dataclass(frozen=True)
class CommandRequest:
    command: str | tuple[str, ...]
    cwd: Path
    env: Mapping[str, str] = field(default_factory=dict)
    timeout: float = 20
    output_limit: int = OUTPUT_LIMIT
    cancel: Callable[[], bool] | None = None

    def __post_init__(self):
        if not self.command or self.timeout <= 0 or self.output_limit < 2:
            raise ValueError('command, positive timeout and output limit >= 2 are required')
        object.__setattr__(self, 'cwd', Path(self.cwd).resolve())
        object.__setattr__(self, 'env', MappingProxyType(dict(self.env)))
        if not isinstance(self.command, str):
            object.__setattr__(self, 'command', tuple(self.command))


@dataclass(frozen=True)
class CommandResult:
    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool = False
    truncated: bool = False
    cancelled: bool = False
    command: str | tuple[str, ...] = ()
    duration_seconds: float = 0
    backend: str = 'local'
    resource_status: str = 'completed'
    container_id: str = ''
    image_digest: str = ''
    cleanup_complete: bool = True

    @property
    def returncode(self):
        return self.exit_code

    def display(self):
        return f"exit_code: {self.exit_code}\nstdout:\n{self.stdout.strip() or '(empty)'}\nstderr:\n{self.stderr.strip() or '(empty)'}"


class Executor(Protocol):
    def execute(self, request: CommandRequest, *, on_start=None) -> CommandResult: ...


class ExecutorError(RuntimeError):
    def __init__(self, message, *, status='environment_error', executed=False):
        super().__init__(message)
        self.status = status
        self.executed = executed


@dataclass(frozen=True)
class ResourceLimits:
    cpus: float = 1.0
    memory_mb: int = 1024
    pids_limit: int = 128
    tmpfs_mb: int = 256

    def __post_init__(self) -> None:
        if isinstance(self.cpus, bool) or not isinstance(self.cpus, (int, float)) or self.cpus <= 0:
            raise ValueError("cpus must be positive")
        for name in ("memory_mb", "pids_limit", "tmpfs_mb"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")


