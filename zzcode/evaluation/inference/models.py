"""Configuration and results for the real-agent inference stage."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from ..schema import AgentRunResult, Prediction, TaskInstance


_PROVIDERS = {"openai", "anthropic", "ollama"}
_MODEL_RE = re.compile(r"^[^\x00\r\n]{1,256}$")


def _safe_endpoint(value: str | None, field_name: str) -> None:
    if value is None:
        return
    if (
        not isinstance(value, str)
        or not value.strip()
        or any(character in value for character in "\x00\r\n")
    ):
        raise ValueError(f"{field_name} must be a safe non-empty URL")
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError(f"{field_name} must be an absolute HTTP(S) URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError(f"{field_name} must not contain credentials")


@dataclass(frozen=True)
class AgentRunConfig:
    """Explicit formal-evaluation config; credentials are never stored here."""

    provider: str
    model: str
    temperature: float = 0.0
    top_p: float = 0.9
    max_steps: int = 30
    max_new_tokens: int = 8192
    timeout_seconds: float = 900.0
    provider_timeout_seconds: float = 300.0
    base_url: str | None = None
    host: str | None = None
    approval_policy: str = "auto"
    tool_network_enabled: bool = False
    tool_image: str = "zzcode-eval-py313:phase4"

    def __post_init__(self) -> None:
        if self.provider not in _PROVIDERS:
            raise ValueError(f"provider must be one of: {', '.join(sorted(_PROVIDERS))}")
        if not isinstance(self.model, str) or not self.model.strip() or not _MODEL_RE.fullmatch(self.model):
            raise ValueError("model must be an explicit non-empty model name")
        if self.model.strip().lower().startswith(("fake", "stub", "mock")):
            raise ValueError("formal evaluation does not allow fake, stub, or mock models")
        if isinstance(self.temperature, bool) or not isinstance(self.temperature, (int, float)):
            raise ValueError("temperature must be a number")
        if not 0.0 <= float(self.temperature) <= 2.0:
            raise ValueError("temperature must be in [0, 2]")
        if isinstance(self.top_p, bool) or not isinstance(self.top_p, (int, float)):
            raise ValueError("top_p must be a number")
        if not 0.0 < float(self.top_p) <= 1.0:
            raise ValueError("top_p must be in (0, 1]")
        for name in ("max_steps", "max_new_tokens"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        for name in ("timeout_seconds", "provider_timeout_seconds"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
                raise ValueError(f"{name} must be positive")
        if self.approval_policy != "auto":
            raise ValueError("formal evaluation requires approval_policy='auto'")
        if self.tool_network_enabled is not False:
            raise ValueError("formal evaluation requires tool_network_enabled=False")
        if (
            not isinstance(self.tool_image, str)
            or not self.tool_image.strip()
            or any(character in self.tool_image for character in "\x00\r\n")
        ):
            raise ValueError("tool_image must be a safe non-empty Docker image reference")
        _safe_endpoint(self.base_url, "base_url")
        _safe_endpoint(self.host, "host")

    def to_worker_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "temperature": float(self.temperature),
            "top_p": float(self.top_p),
            "max_steps": self.max_steps,
            "max_new_tokens": self.max_new_tokens,
            "provider_timeout_seconds": float(self.provider_timeout_seconds),
            "base_url": self.base_url,
            "host": self.host,
            "approval_policy": self.approval_policy,
            "tool_network_enabled": self.tool_network_enabled,
            "tool_image": self.tool_image,
        }

    @classmethod
    def from_worker_dict(cls, row: dict[str, Any], *, timeout_seconds: float = 1.0) -> "AgentRunConfig":
        return cls(timeout_seconds=timeout_seconds, **row)


@dataclass(frozen=True)
class InferenceOutcome:
    task: TaskInstance
    workspace: Path
    agent_result: AgentRunResult
    patch: str | None
    prediction: Prediction | None

    def __post_init__(self) -> None:
        if self.task.instance_id != self.agent_result.instance_id:
            raise ValueError("task and Agent result instance_id do not match")
        if self.prediction is not None:
            if self.prediction.instance_id != self.task.instance_id:
                raise ValueError("task and Prediction instance_id do not match")
            if self.patch != self.prediction.model_patch:
                raise ValueError("Prediction model_patch must equal the collected patch")
        elif self.patch is not None and self.patch.strip():
            raise ValueError("a non-empty patch requires a Prediction")
