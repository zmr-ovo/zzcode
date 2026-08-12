"""Replaceable interface for coding-agent inference implementations."""

from pathlib import Path
from typing import Protocol

from ..schema import TaskInstance
from .models import AgentRunConfig, InferenceOutcome


class AgentAdapter(Protocol):
    def run(
        self,
        task: TaskInstance,
        workspace: Path,
        config: AgentRunConfig,
        artifact_dir: Path,
    ) -> InferenceOutcome: ...
