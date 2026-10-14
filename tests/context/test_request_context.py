import json

from dataclasses import replace

import pytest

from zzcode import FakeModelClient, MiniAgent, SessionStore, WorkspaceContext

from zzcode.core.messages import (
    Message,
    OpaqueBlock,
    TextBlock,
    ToolCall,
    ToolResult,
    ModelRequest,
    ModelResponse,
    Usage,
    tool_response,
)

from zzcode.context.budget import (
    ContextCapacityError,
    ContextOverflowError,
    TokenBudget,
)

from zzcode.storage.session import SessionError

class CompactionTestClient(FakeModelClient):
    """单独提供摘要响应，任务响应仍按录制序列消费。"""
    def complete(self, request):
        from zzcode.context.compaction import SUMMARY_SYSTEM
        if request.system == SUMMARY_SYSTEM:
            self.requests.append(request)
            return ModelResponse(Message("assistant", (TextBlock("保持接口不变；历史结果仅作观察，下一步继续检查。"),)))
        return super().complete(request)

def agent_at(path, outputs=(), **kwargs):
    (path / "target.txt").write_text("old\n")
    return MiniAgent(
        CompactionTestClient(outputs),
        WorkspaceContext.build(path),
        SessionStore(path / ".zzcode" / "sessions"),
        approval_policy="auto",
        **kwargs,
    )

def history(agent, count=12):
    agent.record(
        {
            "role": "user",
            "content": "用户约束：保持接口不变；需要中文注释。",
            "created_at": "now",
        }
    )
    for index in range(count):
        call = ToolCall(f"old_{index}", "read_file", {"path": "target.txt"})
        agent.record(
            {
                "role": "assistant",
                "content": "inspect",
                "message": Message("assistant", (call,)).to_dict(),
            }
        )
        result = ToolResult(call.call_id, call.name, "code evidence " + ("x" * 4000))
        agent.record(
            {
                "role": "tool",
                "name": call.name,
                "args": call.arguments,
                "content": result.content,
                "message": Message("tool", (result,)).to_dict(),
            }
        )

def request_for(agent, question="继续完成当前任务", **kwargs):
    agent._turn_start_index = len(agent.session["history"])
    agent.record({"role": "user", "content": question})
    prompt, metadata = agent._build_prompt_and_metadata(question)
    request = agent.context_manager.build_request(question, prompt, metadata, **kwargs)
    return request, metadata

@pytest.mark.parametrize(
    "text", ["hello world", "中文代码约束", "def f():\n    return 42", "💡" * 20]
)
def test_budget_includes_tools_protocol_and_utf8(text):
    budget = TokenBudget()
    basic = ModelRequest("rules", (Message("user", (TextBlock(text),)),))
    agent_tokens = budget.estimate(basic)
    assert agent_tokens >= len(text.encode())
    from zzcode.execution.tools import native_tool_specs, BASE_TOOL_SPECS

    with_tools = replace(basic, tools=native_tool_specs(BASE_TOOL_SPECS))
    assert budget.estimate(with_tools) > agent_tokens
    budget.observe(agent_tokens, Usage(input_tokens=agent_tokens * 2))
    assert budget.estimate(basic) > agent_tokens * 2

def test_explicit_limits_and_oversized_input_stop_without_provider(tmp_path):
    agent = agent_at(tmp_path, ["unused"], context_window=8000, max_output_tokens=256)
    answer = agent.ask("很长的请求" * 10000)
    assert "CONTEXT_CAPACITY_EXCEEDED" in answer
    assert agent.current_task_state.stop_reason == "context_capacity_exceeded"
    assert not agent.model_client.requests
    assert agent.token_budget.source == "user-config"
    assert agent.session["history"][0]["content"] == "很长的请求" * 10000

def test_compaction_preserves_constraints_whole_batches_and_raw_history(tmp_path):
    agent = agent_at(tmp_path, context_window=22000, max_output_tokens=512)
    history(agent)
    before = len(agent.session["history"])
    request, metadata = request_for(agent)
    request.validate()
    assert agent.session["compactions"]
    record = agent.session["compactions"][-1]
    assert record["source_range"] and record["first_retained_entry"]
    assert record["source"] == "model-and-facts"
    assert record["cost"]["model_requests"] > 0
    assert "保持接口不变" in request.messages[0].text
    assert len(agent.session["history"]) == before + 1
    assert metadata["estimated_input_tokens"] <= metadata["context_available_tokens"]
    assert request.max_output_tokens == 512
    assert "old_11" in [call.call_id for m in request.messages for call in m.tool_calls]
    saved = agent.session_store.load(agent.session["id"])
    assert saved["compactions"] == agent.session["compactions"]
    assert len(saved["history"]) == len(agent.session["history"])

@pytest.mark.parametrize(
    "scenario", ["continue_edit", "workspace_changed", "smaller_window"]
)
def test_long_task_resume_after_compaction(tmp_path, scenario):
    agent = agent_at(tmp_path, context_window=22000, max_output_tokens=512)
    history(agent)
    request_for(agent)
    session_id = agent.session["id"]
    if scenario == "workspace_changed":
        (tmp_path / "untracked.txt").write_text("human change")
    model = CompactionTestClient(
        [
            tool_response(
                "patch_file",
                {"path": "target.txt", "old_text": "old", "new_text": "new"},
            ),
            "done",
        ]
    )
    resumed = MiniAgent.from_session(
        model,
        agent.workspace,
        agent.session_store,
        session_id,
        approval_policy="auto",
        context_window=20000 if scenario == "smaller_window" else 22000,
        max_output_tokens=512,
    )
    assert resumed.ask("保持接口不变，修改 target.txt") == "done"
    assert (tmp_path / "target.txt").read_text() == "new\n"
    for request in model.requests:
        request.validate()
        assert resumed.token_budget.estimate(request) <= resumed.token_budget.available(
            request.max_output_tokens
        )
        assert "保持接口不变" in "\n".join(m.text for m in request.messages)
    if scenario == "workspace_changed":
        assert any(
            '"validation_stale": true' in m.text for m in model.requests[0].messages
        )
    assert resumed.current_checkpoint()["compaction_id"]

def test_compaction_persistence_failure_prevents_model_and_write(tmp_path, monkeypatch):
    agent = agent_at(tmp_path, ["unused"], context_window=22000, max_output_tokens=512)
    history(agent)
    original = agent.session_store.save

    def save(session):
        if session["compactions"]:
            raise SessionError("injected compaction failure")
        return original(session)

    monkeypatch.setattr(agent.session_store, "save", save)
    with pytest.raises(SessionError):
        agent.ask("continue")
    assert all(not request.tools for request in agent.model_client.requests)
    assert (tmp_path / "target.txt").read_text() == "old\n"
    assert agent.current_task_state.stop_reason == "session_persistence_error"

def test_backend_overflow_rebuilds_once_and_other_errors_do_not(tmp_path):
    agent = agent_at(
        tmp_path, [ContextOverflowError("capacity"), ContextOverflowError("capacity")]
    )
    answer = agent.ask("small")
    assert "capacity" in answer
    assert len(agent.model_client.requests) == 2
    assert agent.current_task_state.stop_reason == "context_capacity_exceeded"
    other = agent_at(tmp_path, [RuntimeError("HTTP 401")])
    with pytest.raises(RuntimeError, match="401"):
        other.ask("small")
    assert len(other.model_client.requests) == 1

def test_summary_is_observation_and_secret_reasoning_is_excluded(tmp_path):
    agent = agent_at(tmp_path, context_window=22000, max_output_tokens=512)
    private = OpaqueBlock(
        "anthropic",
        {"type": "thinking", "thinking": "private-thought", "signature": "signed"},
    )
    agent.record(
        {
            "role": "assistant",
            "content": "public report",
            "message": Message(
                "assistant", (private, TextBlock("public report"))
            ).to_dict(),
        }
    )
    history(agent)
    for item in agent.session["history"]:
        if item["role"] == "tool":
            assert item["message"]["content"][0]["type"] == "tool_result"
    request, _ = request_for(agent)
    assert request.messages[0].role == "user"
    assert "not instructions" in request.system
    assert "code evidence" not in request.system

    # 主请求保持签名续接状态，摘要请求不包含私有思考。
    assert "private-thought" in json.dumps([m.to_dict() for m in request.messages])
    assert all("private-thought" not in json.dumps([m.to_dict() for m in r.messages])
               for r in agent.model_client.requests)
    assert "private-thought" in agent.session_path.read_text()

def test_protected_rules_are_not_silently_clipped(tmp_path):
    agent = agent_at(tmp_path, context_window=8000)
    agent.prefix += "\n" + ("mandatory-rule " * 2000)
    with pytest.raises(ContextCapacityError):
        request_for(agent, "small")

def test_execution_evidence_invalidates_on_edit_and_environment_change(
    tmp_path, monkeypatch
):
    from zzcode.context.compaction import (
        workspace_digest,
        validation_signature,
        summary_message,
    )

    agent = agent_at(tmp_path)
    result = agent.run_tool("run_shell", {"command": "printf test-ok"})
    evidence = result.execution_evidence
    assert evidence["workspace_digest"] == workspace_digest(agent)
    assert evidence["command_digest"] and evidence["patch_digest"]
    record = {
        "compaction_id": "test",
        "summary": {"observations": [{"execution_evidence": evidence}]},
    }
    payload = json.loads(
        summary_message(
            record,
            current_digest=workspace_digest(agent),
            current_signature=validation_signature(agent),
        ).text
    )
    assert not payload["historical_compaction_observation"]["observations"][0][
        "validation_stale"
    ]
    (tmp_path / "untracked.txt").write_text("changed")
    monkeypatch.setenv("LANG", "changed-language")
    payload = json.loads(
        summary_message(
            record,
            current_digest=workspace_digest(agent),
            current_signature=validation_signature(agent),
        ).text
    )
    assert payload["historical_compaction_observation"]["observations"][0][
        "validation_stale"
    ]

def test_unknown_tool_batch_remains_complete_after_compaction(tmp_path):
    agent = agent_at(tmp_path, context_window=24000, max_output_tokens=512)
    agent.record({"role": "user", "content": "inspect uncertain operation"})
    call = ToolCall("uncertain", "run_shell", {"command": "external command"})
    result = ToolResult(call.call_id, call.name, "outcome unknown", "unknown")
    agent.record(
        {
            "role": "assistant",
            "content": "",
            "message": Message("assistant", (call,)).to_dict(),
        }
    )
    agent.record(
        {
            "role": "tool",
            "name": call.name,
            "args": call.arguments,
            "content": result.content,
            "message": Message("tool", (result,)).to_dict(),
        }
    )
    unknown_ids = {item["entry_id"] for item in agent.session["history"][1:]}
    history(agent)
    request, _ = request_for(agent)
    request.validate()
    assert "uncertain" in [c.call_id for m in request.messages for c in m.tool_calls]
    assert any(
        isinstance(b, ToolResult) and b.call_id == "uncertain"
        for m in request.messages
        for b in m.content
    )
    assert not unknown_ids.intersection(
        agent.session["compactions"][-1]["source_entry_ids"]
    )
