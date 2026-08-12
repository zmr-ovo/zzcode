"""Real coding-agent inference and patch collection."""

from .adapter import AgentAdapter
from .models import AgentRunConfig, InferenceOutcome
from .patch_collector import DEFAULT_MAX_PATCH_BYTES, collect_patch
from .providers import build_real_model_client
from .runner import InferenceRunner
from .tool_sandbox import DockerToolSandbox
from .zzcode_adapter import ZZCodeAgentAdapter

__all__ = [
    "AgentAdapter",
    "AgentRunConfig",
    "DEFAULT_MAX_PATCH_BYTES",
    "DockerToolSandbox",
    "InferenceOutcome",
    "InferenceRunner",
    "ZZCodeAgentAdapter",
    "build_real_model_client",
    "collect_patch",
]
