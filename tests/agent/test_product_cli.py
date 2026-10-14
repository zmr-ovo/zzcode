
import os


import subprocess

import sys


from unittest.mock import patch

import zzcode as mini_pkg

from zzcode import cli as mini_cli

from zzcode import (
    FakeModelClient,
    MiniAgent,
    SessionStore,
    WorkspaceContext,
    build_welcome,
)

def build_workspace(tmp_path):
    (tmp_path / "README.md").write_text("demo\n", encoding="utf-8")
    return WorkspaceContext.build(tmp_path)

def build_agent(tmp_path, outputs, **kwargs):
    workspace = build_workspace(tmp_path)
    store = SessionStore(tmp_path / ".zzcode" / "sessions")
    approval_policy = kwargs.pop("approval_policy", "auto")
    return MiniAgent(
        model_client=FakeModelClient(outputs),
        workspace=workspace,
        session_store=store,
        approval_policy=approval_policy,
        **kwargs,
    )

def test_welcome_screen_keeps_box_shape_for_long_paths(tmp_path):
    deep = tmp_path / "very" / "long" / "path" / "for" / "the" / "mini" / "agent" / "welcome" / "screen"
    deep.mkdir(parents=True)
    agent = build_agent(deep, [])

    welcome = build_welcome(agent, model="qwen3.5:4b", host="http://127.0.0.1:11434")
    lines = welcome.splitlines()

    assert len(lines) >= 5
    assert len({len(line) for line in lines}) == 1
    assert "..." in welcome
    assert "Start your creation!" in welcome
    assert "MINI-CODING-AGENT" not in welcome
    assert "MINI CODING AGENT" not in welcome
    assert "zzcode" in welcome
    assert "local coding agent" in welcome
    assert "// READY" not in welcome
    assert "SLASH" not in welcome
    assert "READY      " not in welcome
    assert "commands: Commands:" not in welcome

def test_build_agent_uses_openai_provider_and_model_override(tmp_path):
    args = type(
        "Args",
        (),
        {
            "cwd": str(tmp_path),
            "provider": "openai",
            "model": "override-model",
            "base_url": None,
            "host": "http://127.0.0.1:11434",
            "ollama_timeout": 300,
            "temperature": 0.2,
            "top_p": 0.9,
            "resume": None,
            "approval": "ask",
            "executor": "local",
            "docker_image": None,
            "secret_env_names": [],
            "max_steps": 6,
            "max_run_seconds": 300,
            "context_window": None,
            "max_output_tokens": None,
            "max_new_tokens": 512,
        },
    )()

    with patch.dict(
        os.environ,
        {
            "OPENAI_API_BASE": "https://www.right.codes/codex/v1",
            "OPENAI_API_KEY": "sk-test",
            "OPENAI_MODEL": "env-model",
        },
        clear=False,
    ):
        with patch(
            "zzcode.cli.OllamaModelClient",
            side_effect=AssertionError("ollama client should not be used"),
        ), patch("zzcode.cli.OpenAICompatibleModelClient") as mock_openai:
            fake_client = mock_openai.return_value
            fake_client.capabilities = mini_pkg.FakeModelClient([]).capabilities
            agent = mini_pkg.build_agent(args)

    mock_openai.assert_called_once()
    assert mock_openai.call_args.kwargs["model"] == "override-model"
    assert mock_openai.call_args.kwargs["base_url"] == "https://www.right.codes/codex/v1"
    assert mock_openai.call_args.kwargs["api_key"] == "sk-test"
    assert agent.model_client is fake_client

def test_build_agent_loads_global_dotenv_from_another_workspace(tmp_path):
    global_env = tmp_path / "global.env"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    global_env.write_text(
        "OPENAI_API_KEY=global-key\nOPENAI_MODEL=global-model\n",
        encoding="utf-8",
    )
    args = mini_pkg.build_arg_parser().parse_args(["--cwd", str(workspace)])

    with patch.dict(os.environ, {}, clear=True), patch(
        "zzcode.cli._global_env_path", return_value=global_env
    ), patch("zzcode.cli.OpenAICompatibleModelClient") as mock_openai:
        mini_cli._load_env_files(workspace)
        mock_openai.return_value.capabilities = mini_pkg.FakeModelClient([]).capabilities
        mini_pkg.build_agent(args)

    assert mock_openai.call_args.kwargs["api_key"] == "global-key"
    assert mock_openai.call_args.kwargs["model"] == "global-model"

def test_build_agent_uses_anthropic_provider_and_openai_key_fallback(tmp_path):
    args = type(
        "Args",
        (),
        {
            "cwd": str(tmp_path),
            "provider": "anthropic",
            "model": "claude-sonnet-4-5-20250929",
            "base_url": None,
            "host": "http://127.0.0.1:11434",
            "ollama_timeout": 300,
            "openai_timeout": 300,
            "temperature": 0.2,
            "top_p": 0.9,
            "resume": None,
            "approval": "ask",
            "executor": "local",
            "docker_image": None,
            "secret_env_names": [],
            "max_steps": 6,
            "max_run_seconds": 300,
            "context_window": None,
            "max_output_tokens": None,
            "max_new_tokens": 512,
        },
    )()

    with patch.dict(
        os.environ,
        {
            "OPENAI_API_KEY": "sk-openai-fallback",
        },
        clear=True,
    ):
        with patch(
            "zzcode.cli.OllamaModelClient",
            side_effect=AssertionError("ollama client should not be used"),
        ), patch(
            "zzcode.cli.OpenAICompatibleModelClient",
            side_effect=AssertionError("openai client should not be used"),
        ), patch("zzcode.cli.AnthropicCompatibleModelClient") as mock_anthropic:
            fake_client = mock_anthropic.return_value
            fake_client.capabilities = mini_pkg.FakeModelClient([]).capabilities
            agent = mini_pkg.build_agent(args)

    mock_anthropic.assert_called_once()
    assert mock_anthropic.call_args.kwargs["model"] == "claude-sonnet-4-5-20250929"
    assert mock_anthropic.call_args.kwargs["base_url"] == "https://www.right.codes/claude/v1"
    assert mock_anthropic.call_args.kwargs["api_key"] == "sk-openai-fallback"
    assert agent.model_client is fake_client

def test_build_agent_uses_anthropic_default_model_when_env_is_missing(tmp_path):
    args = mini_pkg.build_arg_parser().parse_args(["--cwd", str(tmp_path), "--provider", "anthropic"])

    with patch.dict(
        os.environ,
        {},
        clear=False,
    ):
        os.environ.pop("ANTHROPIC_MODEL", None)
        with patch("zzcode.cli.AnthropicCompatibleModelClient") as mock_anthropic:
            mock_anthropic.return_value.capabilities = mini_pkg.FakeModelClient([]).capabilities
            mini_pkg.build_agent(args)

    assert mock_anthropic.call_args.kwargs["model"] == "claude-sonnet-4-6"

def test_build_agent_uses_openai_provider_by_default(tmp_path):
    args = mini_pkg.build_arg_parser().parse_args(["--cwd", str(tmp_path)])

    with patch.dict(
        os.environ,
        {
            "OPENAI_API_BASE": "https://www.right.codes/codex/v1",
            "OPENAI_API_KEY": "sk-test",
        },
        clear=False,
    ):
        with patch(
            "zzcode.cli.OllamaModelClient",
            side_effect=AssertionError("ollama client should not be used"),
        ), patch("zzcode.cli.OpenAICompatibleModelClient") as mock_openai:
            fake_client = mock_openai.return_value
            fake_client.capabilities = mini_pkg.FakeModelClient([]).capabilities
            agent = mini_pkg.build_agent(args)

    mock_openai.assert_called_once()
    assert mock_openai.call_args.kwargs["model"] == "gpt-5.4"
    assert mock_openai.call_args.kwargs["base_url"] == "https://www.right.codes/codex/v1"
    assert mock_openai.call_args.kwargs["api_key"] == "sk-test"
    assert agent.model_client is fake_client

def test_module_execution_help_works():
    result = subprocess.run(
        [sys.executable, "-m", "zzcode", "--help"],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "usage:" in result.stdout.lower()
