from zzcode.core.messages import tool_response


import json

from pathlib import Path


import zzcode as mini_pkg


from zzcode import (
    FakeModelClient,
    MiniAgent,
    OllamaModelClient,
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

def test_agent_runs_tool_then_final(tmp_path):
    (tmp_path / "hello.txt").write_text("alpha\nbeta\n", encoding="utf-8")
    agent = build_agent(
        tmp_path,
        [
            tool_response('read_file', {'path': 'hello.txt', 'start': 1, 'end': 2}),
            'Read the file successfully.',
        ],
    )

    answer = agent.ask("Inspect hello.txt")

    assert answer == "Read the file successfully."
    assert any(item["role"] == "tool" and item["name"] == "read_file" for item in agent.session["history"])
    assert "hello.txt" in agent.session["memory"]["files"]

def test_agent_retries_after_empty_model_output(tmp_path):
    agent = build_agent(
        tmp_path,
        [
            "",
            'Recovered after retry.',
        ],
    )

    answer = agent.ask("Do the task")

    assert answer == "Recovered after retry."
    notices = [item["content"] for item in agent.session["history"] if item["role"] == "user"]
    assert any("empty response" in item for item in notices)

def test_agent_retries_after_malformed_tool_payload(tmp_path):
    (tmp_path / "hello.txt").write_text("alpha\n", encoding="utf-8")
    agent = build_agent(
        tmp_path,
        [
            tool_response('read_file', 'invalid arguments'),
            tool_response('read_file', {'path': 'hello.txt', 'start': 1, 'end': 1}),
            'Recovered after malformed tool output.',
        ],
    )

    answer = agent.ask("Inspect hello.txt")

    assert answer == "Recovered after malformed tool output."
    assert any(item["role"] == "tool" and item["name"] == "read_file" for item in agent.session["history"])
    results = [item for item in agent.session["history"] if item["role"] == "tool"]
    assert "JSON object" in results[0]["content"]
    assert results[0]["message"]["content"][0]["status"] == "failed"

def test_agent_accepts_native_multiline_write_file_tool(tmp_path):
    agent = build_agent(
        tmp_path,
        [
            tool_response('write_file', {'path': 'hello.py', 'content': 'print("hi")\n'}),
            'Done.',
        ],
    )

    answer = agent.ask("Create hello.py")

    assert answer == "Done."
    assert (tmp_path / "hello.py").read_text(encoding="utf-8") == 'print("hi")\n'

def test_retries_do_not_consume_the_whole_budget(tmp_path):
    agent = build_agent(
        tmp_path,
        [
            "",
            "",
            'Recovered after several retries.',
        ],
        max_steps=1,
    )

    answer = agent.ask("Do the task")

    assert answer == "Recovered after several retries."

def test_agent_gets_finalization_turn_after_tool_budget_is_exhausted(tmp_path):
    (tmp_path / "hello.txt").write_text("hello\n", encoding="utf-8")
    agent = build_agent(
        tmp_path,
        [
            tool_response('read_file', {'path': 'hello.txt', 'start': 1, 'end': 1}),
            'The file contains hello.',
        ],
        max_steps=1,
    )

    answer = agent.ask("What is in hello.txt?")

    assert answer == "The file contains hello."
    assert agent.current_task_state.tool_steps == 1
    assert agent.current_task_state.attempts == 2

def test_delegate_uses_child_agent(tmp_path):
    agent = build_agent(
        tmp_path,
        [
            tool_response('delegate', {'task': 'inspect README', 'max_steps': 2}),
            'Child result.',
            'Parent incorporated the child result.',
        ],
    )

    answer = agent.ask("Use delegation")

    assert answer == "Parent incorporated the child result."
    tool_events = [item for item in agent.session["history"] if item["role"] == "tool"]
    assert tool_events[0]["name"] == "delegate"
    assert "delegate_result" in tool_events[0]["content"]

def test_build_arg_parser_defaults_provider_to_openai(tmp_path):
    args = mini_pkg.build_arg_parser().parse_args(["--cwd", str(tmp_path)])

    assert args.provider == "openai"
    assert args.max_new_tokens == 2048

def test_build_arg_parser_accepts_anthropic_provider(tmp_path):
    args = mini_pkg.build_arg_parser().parse_args(["--cwd", str(tmp_path), "--provider", "anthropic"])

    assert args.provider == "anthropic"

def test_successful_run_persists_run_artifacts_and_stop_reason(tmp_path):
    (tmp_path / "hello.txt").write_text("alpha\nbeta\n", encoding="utf-8")
    agent = build_agent(
        tmp_path,
        [
            tool_response('read_file', {'path': 'hello.txt', 'start': 1, 'end': 2}),
            'Finished.',
        ],
    )

    assert agent.ask("Do the thing") == "Finished."

    runs_root = tmp_path / ".zzcode" / "runs"
    run_dirs = [path for path in runs_root.iterdir() if path.is_dir()]
    assert len(run_dirs) == 1

    run_dir = run_dirs[0]
    task_state = json.loads((run_dir / "task_state.json").read_text(encoding="utf-8"))
    report = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
    trace_lines = (run_dir / "trace.jsonl").read_text(encoding="utf-8").splitlines()

    assert task_state["task_id"] != task_state["run_id"]
    assert run_dir.name == task_state["run_id"]
    assert (run_dir / "task_state.json").exists()
    assert (run_dir / "trace.jsonl").exists()
    assert (run_dir / "report.json").exists()
    assert task_state["stop_reason"] == "final_answer_returned"
    assert task_state["final_answer"] == "Finished."
    assert report["stop_reason"] == "final_answer_returned"
    assert report["task_state"]["stop_reason"] == "final_answer_returned"
    assert report["run_id"] == task_state["run_id"]
    trace_events = [json.loads(line)["event"] for line in trace_lines]
    assert trace_events[0] == "run_started"
    assert trace_events[-1] == "run_finished"
    assert trace_events.count("prompt_built") == 2
    assert "tool_executed" in trace_events

def test_public_api_exports_resolve_through_package_path():
    assert callable(mini_pkg.main)
    assert callable(mini_pkg.build_agent)
    assert callable(mini_pkg.build_arg_parser)
    assert callable(build_welcome)
    assert FakeModelClient is not None
    assert MiniAgent is not None
    assert OllamaModelClient is not None
    assert SessionStore is not None
    assert WorkspaceContext is not None
    assert Path(mini_pkg.__file__).as_posix().endswith("/zzcode/__init__.py")
