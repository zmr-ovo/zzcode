from pathlib import Path

import pytest

from zzcode.evaluation import AgentRunConfig
from zzcode.evaluation.inference.providers import (
    build_real_model_client,
    provider_credentials_available,
)
from zzcode.evaluation.inference.zzcode_adapter import ZZCodeAgentAdapter


@pytest.mark.parametrize("model", ["fake", "FakeModelClient", "stub-agent", "mock/model"])
def test_formal_config_rejects_fake_model_names(model):
    with pytest.raises(ValueError, match="does not allow"):
        AgentRunConfig(provider="openai", model=model)


def test_formal_config_requires_explicit_model_and_networkless_tool_plane():
    with pytest.raises(ValueError, match="explicit"):
        AgentRunConfig(provider="openai", model="")
    with pytest.raises(ValueError, match="tool_network_enabled=False"):
        AgentRunConfig(provider="openai", model="gpt-real", tool_network_enabled=True)


def test_provider_endpoint_rejects_embedded_credentials():
    with pytest.raises(ValueError, match="must not contain credentials"):
        AgentRunConfig(
            provider="openai",
            model="real-model",
            base_url="https://user:password@provider.example/v1",
        )


def test_real_provider_builder_never_returns_fake_client(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-only-secret")
    config = AgentRunConfig(
        provider="openai",
        model="real-model",
        base_url="https://provider.example/v1",
    )

    client = build_real_model_client(config)

    assert client.__class__.__name__ == "OpenAICompatibleModelClient"
    assert client.model == "real-model"
    assert client.api_key == "test-only-secret"


def test_missing_openai_credential_is_detected_without_reading_config(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    config = AgentRunConfig(provider="openai", model="real-model")

    assert not provider_credentials_available(config)
    assert "test-only-secret" not in repr(config)
    assert "OPENAI_API_KEY" not in config.to_worker_dict()


def test_config_contains_no_current_environment_secret(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "secret-value-must-not-serialize")
    serialized = AgentRunConfig(provider="openai", model="real-model").to_worker_dict()

    assert "secret-value-must-not-serialize" not in repr(serialized)
    assert all("key" not in name.lower() for name in serialized)


def test_adapter_module_has_no_fake_model_fallback():
    import inspect

    source = inspect.getsource(ZZCodeAgentAdapter)
    assert "FakeModelClient(" not in source


def test_current_evaluation_guides_exist():
    implementation = Path("docs/testing/zzcode-evaluation-refactor-plan.md")
    usage = Path("evaluation/README.md")

    assert implementation.is_file()
    assert usage.is_file()
    assert "Phase 6.5" in implementation.read_text(encoding="utf-8")
    assert "Coding 执行闭环" in usage.read_text(encoding="utf-8")
