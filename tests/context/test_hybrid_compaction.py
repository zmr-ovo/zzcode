"""检查工具输出投影、模型摘要与原始历史的恢复边界。"""
import copy
import json

import pytest

from zzcode import FakeModelClient, MiniAgent, SessionStore, WorkspaceContext
from zzcode.core.messages import Message, ToolCall, ToolResult, ModelResponse
from zzcode.context.compaction import clear_result_bodies, compact, model_summary, summarize
from zzcode.context.budget import ContextCapacityError


def agent_at(tmp_path, outputs=()):
    return MiniAgent(FakeModelClient(outputs), WorkspaceContext.build(tmp_path),
                     SessionStore(tmp_path / ".zzcode/sessions"), approval_policy="auto",
                     context_window=22000, max_output_tokens=512)


def test_projection_keeps_protocol_facts_and_retrievable_output(tmp_path):
    agent = agent_at(tmp_path)
    call = ToolCall("read1", "read_file", {"path": "missing.txt"})
    result = ToolResult(call.call_id, call.name, "historical data\n" * 200,
                        operation_id="op1", execution_evidence={"workspace_digest": "old"})
    group = [Message("assistant", (call,)), Message("tool", (result,))]
    projected, count = clear_result_bodies(agent, group, [])
    value = projected[1].content[0]
    assert count == 1 and value.call_id == call.call_id and value.operation_id == "op1"
    assert value.execution_evidence == result.execution_evidence
    assert "historical data" in agent.gateway.artifacts.read(value.artifact_refs[0])
    assert group[1].content[0].content == result.content
    from zzcode.core.messages import ModelRequest
    ModelRequest("rules", tuple(projected)).validate()


@pytest.mark.parametrize("status", ["unknown", "partial_success", "failed"])
def test_unsettled_or_failed_results_are_not_cleared(tmp_path, status):
    agent = agent_at(tmp_path)
    group = [Message("tool", (ToolResult("c", "run_shell", "x" * 3000, status),))]
    assert clear_result_bodies(agent, group, []) == (group, 0)


def test_model_summary_keeps_facts_and_does_not_modify_history(tmp_path):
    agent = agent_at(tmp_path, ["根因在边界判断；修改尚未验证，下一步运行测试。"])
    items = [{"entry_id": "e1", "role": "user", "content": "保持接口不变"},
             {"entry_id": "e2", "role": "assistant", "content": "发现空输入越界，已修改但未验证"}]
    before = copy.deepcopy(items)
    record = compact(agent, items, "e3")
    model_summary(agent, record, items)
    assert record["summary"]["user_requests_verbatim"] == ["保持接口不变"]
    assert "尚未验证" in record["summary"]["model_continuation"]
    assert record["source"] == "model-and-facts" and record["cost"]["model_requests"] == 1
    assert items == before
    request = agent.model_client.requests[0]
    assert not request.tools and request.tool_choice == "none"
    assert "e2" in request.messages[0].text


def test_model_tool_call_is_rejected_without_execution(tmp_path):
    from zzcode.core.messages import ProviderProtocolError
    agent = agent_at(tmp_path, [ModelResponse(Message("assistant", (ToolCall("bad", "write_file", {"path": "bad"}),)), "tool_call")])
    items = [{"entry_id": "e1", "role": "user", "content": "goal"}]
    record = compact(agent, items, "e2")
    with pytest.raises(ProviderProtocolError, match="complete text"):
        model_summary(agent, record, items)
    assert not (tmp_path / "bad").exists() and record["source"] == "deterministic-facts"


def test_summary_oversized_entry_stops_before_model(tmp_path):
    agent = agent_at(tmp_path)
    items = [{"entry_id": "e1", "role": "user", "content": "x" * 50000}]
    with pytest.raises(ContextCapacityError, match="summarizer capacity"):
        model_summary(agent, compact(agent, items, "e2"), items)
    assert not agent.model_client.requests


def test_distinct_successful_shell_operations_survive_rule_summary():
    items = [{"entry_id": f"e{i}", "role": "tool", "name": "run_shell", "args": {},
              "content": text} for i, text in enumerate(["first command", "second command"])]
    assert len(summarize(items)["observations"]) == 2


def test_hybrid_request_clears_history_without_mutating_session(tmp_path):
    agent = agent_at(tmp_path)
    agent.record({"role": "user", "content": "保持接口不变"})
    for i in range(8):
        call = ToolCall(f"c{i}", "read_file", {"path": "a.py"})
        result = ToolResult(call.call_id, call.name, "file data\n" * 400)
        agent.record({"role": "assistant", "content": "read", "message": Message("assistant", (call,)).to_dict()})
        agent.record({"role": "tool", "name": call.name, "args": call.arguments,
                      "content": result.content, "message": Message("tool", (result,)).to_dict()})
    agent._turn_start_index = len(agent.session["history"])
    agent.record({"role": "user", "content": "continue"})
    before = json.dumps(agent.session["history"])
    prompt, metadata = agent._build_prompt_and_metadata("continue")
    request = agent.context_manager.build_request("continue", prompt, metadata)
    request.validate()
    assert metadata["cleared_tool_results"] > 0
    assert json.dumps(agent.session["history"]) == before
    assert not agent.model_client.requests


def test_full_hybrid_summary_is_persisted_and_reused(tmp_path):
    agent = agent_at(tmp_path, ["发现边界问题；修改未验证，下一步检查测试。"] * 8)
    agent.record({"role": "user", "content": "不要改接口"})
    for i in range(12):
        agent.record({"role": "assistant", "content": "调查结果：" + str(i) + " detail" * 600})
    agent._turn_start_index = len(agent.session["history"])
    agent.record({"role": "user", "content": "continue"})
    raw = copy.deepcopy(agent.session["history"])
    prompt, metadata = agent._build_prompt_and_metadata("continue")
    request = agent.context_manager.build_request("continue", prompt, metadata)
    request.validate()
    record = agent.session["compactions"][-1]
    assert record["source"] == "model-and-facts"
    assert record["cost"]["model_requests"] > 0
    assert "不要改接口" in request.messages[0].text
    assert agent.session["history"] == raw
    saved = agent.session_store.load(agent.session["id"])
    assert saved["compactions"][-1] == record
    before = len(agent.model_client.requests)
    agent.context_manager.build_request("continue", prompt, {})
    assert len(agent.model_client.requests) == before
