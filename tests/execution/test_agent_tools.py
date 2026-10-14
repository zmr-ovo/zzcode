from zzcode.core.messages import tool_response

import os

import json


from unittest.mock import patch

import zzcode as mini_pkg

from zzcode import cli as mini_cli

from zzcode import (
    FakeModelClient,
    MiniAgent,
    SessionStore,
    WorkspaceContext,
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

def test_patch_file_replaces_exact_match(tmp_path):
    file_path = tmp_path / "sample.txt"
    file_path.write_text("hello world\n", encoding="utf-8")
    agent = build_agent(tmp_path, [])

    result = agent.run_tool(
        "patch_file",
        {
            "path": "sample.txt",
            "old_text": "world",
            "new_text": "agent",
        },
    )

    assert result.content == "patched sample.txt"
    assert file_path.read_text(encoding="utf-8") == "hello agent\n"

def test_invalid_risky_tool_does_not_prompt_for_approval(tmp_path):
    agent = build_agent(tmp_path, [], approval_policy="ask")

    with patch("builtins.input") as mock_input:
        result = agent.run_tool("write_file", {})

    assert result.content.startswith("error: invalid arguments for write_file: missing path")
    assert "example:" in result.content
    mock_input.assert_not_called()

def test_list_files_hides_internal_agent_state(tmp_path):
    agent = build_agent(tmp_path, [])
    (tmp_path / ".zzcode").mkdir(exist_ok=True)
    (tmp_path / ".git").mkdir(exist_ok=True)
    (tmp_path / "hello.txt").write_text("hi\n", encoding="utf-8")

    result = agent.run_tool("list_files", {})

    assert ".zzcode" not in result.content
    assert ".git" not in result.content
    assert "[F] hello.txt" in result.content

def test_repeated_identical_tool_call_is_rejected(tmp_path):
    agent = build_agent(tmp_path, [])
    agent.record({"role": "tool", "name": "list_files", "args": {}, "content": "(empty)", "created_at": "1"})
    agent.record({"role": "tool", "name": "list_files", "args": {}, "content": "(empty)", "created_at": "2"})

    result = agent.run_tool("list_files", {})

    assert result.content == "error: repeated identical tool call for list_files; choose a different tool or return a final answer"

def test_shell_env_overrides_global_and_workspace_dotenv(tmp_path):
    global_env = tmp_path / "global.env"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    global_env.write_text("OPENAI_API_KEY=global-key\n", encoding="utf-8")
    (workspace / ".env").write_text("OPENAI_API_KEY=workspace-key\n", encoding="utf-8")
    args = mini_pkg.build_arg_parser().parse_args(["--cwd", str(workspace)])

    with patch.dict(os.environ, {"OPENAI_API_KEY": "shell-key"}, clear=True), patch(
        "zzcode.cli._global_env_path", return_value=global_env
    ), patch("zzcode.cli.OpenAICompatibleModelClient") as mock_openai:
        mini_cli._load_env_files(workspace)
        mock_openai.return_value.capabilities = mini_pkg.FakeModelClient([]).capabilities
        mini_pkg.build_agent(args)

    assert mock_openai.call_args.kwargs["api_key"] == "shell-key"

def test_dotenv_is_hidden_and_cannot_be_read_by_agent(tmp_path):
    (tmp_path / ".env").write_text("OPENAI_API_KEY=secret\n", encoding="utf-8")
    agent = build_agent(tmp_path, [])

    assert ".env" not in (agent.run_tool("list_files", {"path": "."})).content
    assert "access to private environment files is not allowed" in (agent.run_tool(
        "read_file", {"path": ".env"}
    )).content

def test_trace_and_report_redact_secret_env_values(tmp_path):
    secret = "sk-test-secret-123"
    with patch.dict(os.environ, {"OPENAI_API_KEY": secret}, clear=True):
        agent = build_agent(
            tmp_path,
            [
                tool_response('run_shell', {'command': "printf '%s' 'sk-test-secret-123'", 'timeout': 20}),
                'Masked.',
            ],
        )

        assert agent.ask("Mask the secret") == "Masked."

    runs_root = tmp_path / ".zzcode" / "runs"
    run_dirs = [path for path in runs_root.iterdir() if path.is_dir()]
    assert len(run_dirs) == 1

    run_dir = run_dirs[0]
    trace_text = (run_dir / "trace.jsonl").read_text(encoding="utf-8")
    report_text = (run_dir / "report.json").read_text(encoding="utf-8")
    trace_events = [json.loads(line) for line in trace_text.splitlines()]

    assert secret not in trace_text
    assert secret not in report_text

    prompt_events = [event for event in trace_events if event["event"] == "prompt_built"]
    assert prompt_events
    assert prompt_events[0]["prompt_metadata"]["secret_env_count"] >= 1
    assert "OPENAI_API_KEY" in prompt_events[0]["prompt_metadata"]["secret_env_names"]

    tool_events = [event for event in trace_events if event["event"] == "tool_executed"]
    assert tool_events
    assert "<redacted>" in tool_events[0]["args"]["command"]
    assert "<redacted>" in tool_events[0]["result"]

def test_run_shell_nonzero_with_workspace_change_is_recorded_as_partial_success(tmp_path):
    agent = build_agent(tmp_path, [])

    result = agent.run_tool(
        "run_shell",
        {
            "command": "printf 'changed\\n' > README.md && exit 1",
            "timeout": 20,
        },
    )

    assert "exit_code: 1" in result.content
    assert result.status == "partial_success"
    assert list(result.affected_paths) == ["README.md"]
    assert bool(result.affected_paths) is True

def test_write_file_trace_records_minimum_tool_contract_fields(tmp_path):
    agent = build_agent(
        tmp_path,
        [
            tool_response('write_file', {'path': 'notes.txt', 'content': 'hello\n'}),
            'Done.',
        ],
    )

    assert agent.ask("Create notes.txt") == "Done."

    trace_events = [
        json.loads(line)
        for line in agent.run_store.trace_path(agent.current_task_state).read_text(encoding="utf-8").splitlines()
    ]
    tool_event = [event for event in trace_events if event["event"] == "tool_executed"][-1]

    assert tool_event["name"] == "write_file"
    assert tool_event["risk_level"] == "high"
    assert tool_event["read_only"] is False
    assert tool_event["tool_status"] == "ok"
    assert tool_event["affected_paths"] == ["notes.txt"]
    assert tool_event["workspace_changed"] is True
    assert tool_event["diff_summary"] == ["created:notes.txt"]

def test_partial_success_creates_process_note_for_exploration_history(tmp_path):
    agent = build_agent(tmp_path, [])

    agent.run_tool(
        "run_shell",
        {
            "command": "printf 'changed\\n' > README.md && exit 1",
            "timeout": 20,
        },
    )

    process_notes = [
        note
        for note in agent.memory.to_dict()["episodic_notes"]
        if note.get("kind") == "process"
    ]

    assert process_notes
    assert process_notes[-1]["text"] == "run_shell partial_success on README.md; inspect diff before retry"
    assert "partial_success" in process_notes[-1]["tags"]
    assert "README.md" in process_notes[-1]["tags"]
