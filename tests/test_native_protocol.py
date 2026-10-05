"""Native provider contracts and execution boundaries, without real-model calls."""

import json
from pathlib import Path
from dataclasses import replace
from unittest.mock import patch

import pytest

from zzcode.core.messages import (
    Message,
    ModelRequest,
    ModelResponse,
    OpaqueBlock,
    ProviderProtocolError,
    TextBlock,
    ToolCall,
    ToolResult,
    ToolSpec,
    Usage,
    tool_response,
)
from zzcode.models import (
    AnthropicCompatibleModelClient,
    FakeModelClient,
    OllamaModelClient,
    OpenAICompatibleModelClient,
)
from zzcode.runtime import SessionStore, ZZCode
from zzcode.workspace import WorkspaceContext

SPEC = ToolSpec(
    "read_file",
    "Read a file",
    {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
        "additionalProperties": False,
    },
)


def request(**kwargs):
    return ModelRequest(
        "rules", (Message("user", (TextBlock("hello"),)),), (SPEC,), 42, **kwargs
    )


def client(provider):
    if provider == "openai":
        return OpenAICompatibleModelClient(
            "model", "https://api.openai.com/v1", "sk-test", 0.2, 30
        )
    if provider == "anthropic":
        return AnthropicCompatibleModelClient(
            "model", "https://api.anthropic.com/v1", "sk-test", 0.2, 30
        )
    return OllamaModelClient("model", "http://localhost:11434", 0.2, 0.9, 30)


def complete(provider, data, req=None, stream=False):
    captured = {}

    class Response:
        headers = {
            "Content-Type": "text/event-stream" if stream else "application/json"
        }

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return (data if isinstance(data, str) else json.dumps(data)).encode()

    def urlopen(req, timeout):
        captured.update(
            url=req.full_url,
            headers=dict(req.headers),
            body=json.loads(req.data),
            timeout=timeout,
        )
        return Response()

    model = client(provider)
    with patch("urllib.request.urlopen", urlopen):
        result = model.complete(req or request())
    return result, captured, model


def wire(provider, blocks=None, stop=None):
    if provider == "openai":
        return {
            "status": stop or "completed",
            "output": blocks
            or [
                {"type": "message", "content": [{"type": "output_text", "text": "ok"}]}
            ],
        }
    if provider == "anthropic":
        return {
            "role": "assistant",
            "stop_reason": stop or "end_turn",
            "content": blocks or [{"type": "text", "text": "ok"}],
        }
    return {
        "done": True,
        "done_reason": stop or "stop",
        "message": {"role": "assistant", "content": "ok"},
    }


@pytest.mark.parametrize(
    "provider,endpoint",
    [
        ("openai", "/v1/responses"),
        ("anthropic", "/v1/messages"),
        ("ollama", "/api/chat"),
    ],
)
def test_native_request_contract(provider, endpoint):
    result, captured, _ = complete(provider, wire(provider))
    body = captured["body"]
    assert result.text == "ok"
    assert result.stop_reason == "end_turn"
    assert captured["url"].endswith(endpoint)
    assert captured["timeout"] == 30
    assert body["stream"] is False
    if provider == "openai":
        assert body["instructions"] == "rules"
        assert body["input"] == [
            {"role": "user", "content": [{"type": "input_text", "text": "hello"}]}
        ]
        assert body["tools"][0]["parameters"] == SPEC.input_schema
        assert captured["headers"]["Authorization"] == "Bearer sk-test"
        assert body["max_output_tokens"] == 42
    elif provider == "anthropic":
        assert body["system"] == "rules"
        assert body["messages"][0]["content"] == [{"type": "text", "text": "hello"}]
        assert body["tools"][0]["input_schema"] == SPEC.input_schema
        assert captured["headers"]["X-api-key"] == "sk-test"
        assert captured["headers"]["Anthropic-version"] == "2023-06-01"
        assert body["max_tokens"] == 42
    else:
        assert body["messages"] == [
            {"role": "system", "content": "rules"},
            {"role": "user", "content": "hello"},
        ]
        assert body["tools"][0]["function"]["parameters"] == SPEC.input_schema
        assert body["options"] == {"num_predict": 42, "temperature": 0.2, "top_p": 0.9}


@pytest.mark.parametrize("provider", ["openai", "anthropic", "ollama"])
def test_native_tool_call_and_multiline_arguments(provider):
    args = {"path": "a.py", "content": 'print("中文")\n'}
    if provider == "openai":
        data = wire(
            provider,
            [
                {
                    "type": "function_call",
                    "call_id": "c1",
                    "name": "write_file",
                    "arguments": json.dumps(args),
                }
            ],
        )
    elif provider == "anthropic":
        data = wire(
            provider,
            [{"type": "tool_use", "id": "c1", "name": "write_file", "input": args}],
            "tool_use",
        )
    else:
        data = wire(provider)
        data["message"]["tool_calls"] = [
            {"function": {"name": "write_file", "arguments": args}}
        ]
    result, _, _ = complete(provider, data)
    assert result.tool_calls[0].arguments == args
    assert result.tool_calls[0].call_id
    assert result.stop_reason == "tool_call"


@pytest.mark.parametrize("provider", ["openai", "anthropic", "ollama"])
def test_results_round_trip_and_final_tool_choice(provider):
    messages = request().messages + (
        Message(
            "assistant",
            (
                TextBlock("Checking"),
                ToolCall("c1", "read_file", {"path": "a"}),
                ToolCall("c2", "read_file", {"path": "b"}),
            ),
        ),
        Message("tool", (ToolResult("c1", "read_file", "a", "succeeded"),)),
        Message("tool", (ToolResult("c2", "read_file", "denied", "rejected"),)),
    )
    _, captured, _ = complete(
        provider,
        wire(provider),
        replace(request(), messages=messages, tool_choice="none"),
    )
    body = captured["body"]
    if provider == "openai":
        outputs = [
            item for item in body["input"] if item.get("type") == "function_call_output"
        ]
        assert [(item["call_id"], item["output"]) for item in outputs] == [
            ("c1", "a"),
            ("c2", "denied"),
        ]
        assert body["tool_choice"] == "none"
    elif provider == "anthropic":
        results = body["messages"][-1]["content"]
        assert [item["tool_use_id"] for item in results] == ["c1", "c2"]
        assert [item["is_error"] for item in results] == [False, True]
        assert body["tool_choice"] == {"type": "none"}
    else:
        assert [
            item["tool_name"] for item in body["messages"] if item["role"] == "tool"
        ] == ["read_file", "read_file"]
        assert body["tools"] == []


def test_usage_and_cache_fields_preserve_unknowns():
    data = wire("openai")
    data["usage"] = {
        "input_tokens": 2048,
        "output_tokens": 32,
        "total_tokens": 2080,
        "input_tokens_details": {"cached_tokens": 1536},
    }
    result, captured, model = complete(
        "openai", data, request(cache_key="prefix", cache_retention="in_memory")
    )
    assert result.usage == Usage(2048, 32, 1536, 2080)
    assert captured["body"]["prompt_cache_key"] == "prefix"
    assert captured["body"]["prompt_cache_retention"] == "in_memory"
    assert model.last_completion_metadata["cache_hit"] is True
    assert complete("openai", wire("openai"))[0].usage == Usage()
    data = wire("anthropic")
    data["usage"] = {
        "input_tokens": 10,
        "cache_read_input_tokens": 20,
        "cache_creation_input_tokens": 5,
        "output_tokens": 3,
    }
    assert complete("anthropic", data)[0].usage == Usage(35, 3, 20, 38)


def test_thinking_order_signature_round_trip_without_display():
    thinking = {"type": "thinking", "thinking": "hidden", "signature": "signed"}
    data = wire(
        "anthropic",
        [
            thinking,
            {"type": "text", "text": "first"},
            {
                "type": "tool_use",
                "id": "c1",
                "name": "read_file",
                "input": {"path": "a"},
            },
            {"type": "text", "text": "last"},
        ],
        "tool_use",
    )
    response, _, _ = complete("anthropic", data)
    assert response.text == "firstlast"
    assert [block.type for block in response.message.content] == [
        "opaque",
        "text",
        "tool_call",
        "text",
    ]
    restored = ModelResponse.from_dict(response.to_dict())
    req = replace(
        request(),
        messages=request().messages
        + (restored.message, Message("tool", (ToolResult("c1", "read_file", "a"),))),
    )
    _, captured, _ = complete("anthropic", wire("anthropic"), req)
    assert captured["body"]["messages"][1]["content"][0] == thinking
    with pytest.raises(ProviderProtocolError, match="signed provider"):
        complete("openai", wire("openai"), req)


def test_openai_reasoning_only_is_protocol_error():
    with pytest.raises(
        ProviderProtocolError, match="Increase --max-new-tokens above 42"
    ):
        complete("openai", wire("openai", [{"type": "reasoning", "summary": []}]))


def sse(*events):
    return "".join("data: " + json.dumps(event) + "\n\n" for event in events)


def test_openai_sse_terminal_is_authoritative():
    terminal = wire(
        "openai",
        [
            {
                "type": "function_call",
                "call_id": "c1",
                "name": "read_file",
                "arguments": '{"path":"a"}',
            }
        ],
    )
    result, _, _ = complete(
        "openai",
        sse(
            {"type": "response.output_text.delta", "delta": "intermediate"},
            {"type": "response.completed", "response": terminal},
        ),
        stream=True,
    )
    assert result.text == ""
    assert result.tool_calls[0].call_id == "c1"


def test_openai_sse_text_deltas_with_done():
    result, _, _ = complete(
        "openai",
        sse(
            {"type": "response.output_text.delta", "delta": "O"},
            {"type": "response.output_text.delta", "delta": "K"},
        )
        + "data: [DONE]\n\n",
        stream=True,
    )
    assert result.text == "OK"


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_truncated_stream_is_not_final(provider):
    event = (
        {"type": "response.output_text.delta", "delta": "partial"}
        if provider == "openai"
        else {"type": "message_start", "message": {"role": "assistant", "content": []}}
    )
    with pytest.raises(ProviderProtocolError):
        complete(provider, sse(event), stream=True)


def test_anthropic_sse_accumulates_arguments_and_usage():
    events = [
        {
            "type": "message_start",
            "message": {"role": "assistant", "usage": {"input_tokens": 10}},
        },
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {
                "type": "tool_use",
                "id": "c1",
                "name": "read_file",
                "input": {},
            },
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "input_json_delta", "partial_json": '{"path":'},
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "input_json_delta", "partial_json": '"a"}'},
        },
        {"type": "content_block_stop", "index": 0},
        {
            "type": "message_delta",
            "delta": {"stop_reason": "tool_use"},
            "usage": {"output_tokens": 5},
        },
        {"type": "message_stop"},
    ]
    result, _, _ = complete("anthropic", sse(*events), stream=True)
    assert result.tool_calls[0].arguments == {"path": "a"}
    assert result.usage.input_tokens == 10
    assert result.usage.output_tokens == 5


def agent(tmp_path, outputs, **kwargs):
    return ZZCode(
        FakeModelClient(outputs),
        WorkspaceContext.build(tmp_path, repo_root_override=tmp_path),
        SessionStore(tmp_path / ".zzcode/sessions"),
        approval_policy="auto",
        **kwargs,
    )


def results(runtime):
    return [
        Message.from_dict(item["message"]).content[0]
        for item in runtime.session["history"]
        if item["role"] == "tool"
    ]


def test_batch_executes_serially_and_cancels_over_budget(tmp_path):
    batch = ModelResponse(
        Message(
            "assistant",
            (
                TextBlock("Working"),
                ToolCall("c1", "write_file", {"path": "a", "content": "one"}),
                ToolCall("c2", "read_file", {"path": "a"}),
                ToolCall("c3", "write_file", {"path": "b", "content": "two"}),
            ),
        ),
        "tool_call",
    )
    runtime = agent(tmp_path, [batch, "finished"], max_steps=2)
    assert runtime.ask("Create a and inspect it") == "finished"
    assert [(r.call_id, r.status) for r in results(runtime)] == [
        ("c1", "succeeded"),
        ("c2", "succeeded"),
        ("c3", "cancelled"),
    ]
    assert "one" in results(runtime)[1].content
    assert not (tmp_path / "b").exists()
    assert runtime.model_client.requests[-1].tool_choice == "none"
    assert runtime.current_task_state.tool_steps == 2


@pytest.mark.parametrize(
    "stop", ["max_tokens", "content_filter", "cancelled", "unknown"]
)
def test_incomplete_or_filtered_output_never_executes_tools(tmp_path, stop):
    response = replace(
        tool_response("write_file", {"path": "a", "content": "unsafe"}, "c1"),
        stop_reason=stop,
    )
    runtime = agent(tmp_path, [response])
    runtime.ask("Create a")
    assert not (tmp_path / "a").exists()
    assert results(runtime)[0].status == "cancelled"
    assert runtime.current_task_state.status == "stopped"
    assert runtime.current_task_state.stop_reason == stop


def test_finalization_tool_request_is_cancelled(tmp_path):
    runtime = agent(
        tmp_path,
        [
            tool_response("write_file", {"path": "a", "content": "one"}),
            tool_response("write_file", {"path": "b", "content": "two"}),
        ],
        max_steps=1,
    )
    runtime.ask("Create files")
    assert not (tmp_path / "b").exists()
    assert results(runtime)[-1].status == "cancelled"
    assert runtime.current_task_state.stop_reason == "step_limit_reached"


@pytest.mark.parametrize(
    "args",
    [{"path": "a", "content": 123}, {"path": "a", "content": "x", "unexpected": True}],
)
def test_schema_validation_precedes_approval(tmp_path, args):
    runtime = agent(tmp_path, [tool_response("write_file", args), "done"])
    with patch.object(
        runtime, "approve", side_effect=AssertionError("approval must not happen")
    ):
        runtime.ask("Create a")
    assert results(runtime)[0].status == "rejected"
    assert not (tmp_path / "a").exists()


def test_nonobject_arguments_return_linked_error(tmp_path):
    data = wire(
        "openai",
        [
            {
                "type": "function_call",
                "call_id": "c1",
                "name": "write_file",
                "arguments": "[]",
            }
        ],
    )
    response, _, _ = complete("openai", data)
    runtime = agent(tmp_path, [response, "done"])
    runtime.ask("Create a")
    assert results(runtime)[0].call_id == "c1"
    assert results(runtime)[0].status == "failed"


def test_duplicate_call_id_rejected_without_side_effects(tmp_path):
    invalid = ModelResponse(
        Message(
            "assistant",
            (
                ToolCall("c1", "write_file", {"path": "a", "content": "x"}),
                ToolCall("c1", "read_file", {"path": "a"}),
            ),
        ),
        "tool_call",
    )
    runtime = agent(tmp_path, [invalid, "done"])
    assert runtime.ask("Create a") == "done"
    assert not (tmp_path / "a").exists()
    assert runtime.current_task_state.attempts == 2


def test_old_text_format_has_no_executable_meaning(tmp_path):
    text = '<tool>{"name":"write_file","args":{"path":"a","content":"x"}}</tool>'
    runtime = agent(tmp_path, [text])
    assert runtime.ask("Create a") == text
    assert not (tmp_path / "a").exists()
    assert not hasattr(ZZCode, "parse_xml_tool")
    assert not hasattr(ZZCode, "parse")


def test_signed_state_persisted_private_and_excluded_from_artifacts(tmp_path):
    opaque = OpaqueBlock(
        "anthropic",
        {
            "type": "thinking",
            "thinking": "hidden-secret-reasoning",
            "signature": "signed",
        },
    )
    runtime = agent(
        tmp_path, [ModelResponse(Message("assistant", (opaque, TextBlock("done"))))]
    )
    runtime.ask("Explain")
    saved = runtime.session_store.path(runtime.session["id"])
    assert saved.stat().st_mode & 0o777 == 0o600
    assert "hidden-secret-reasoning" in saved.read_text()
    for path in Path(runtime.current_run_dir).glob("*"):
        if path.is_file():
            assert "hidden-secret-reasoning" not in path.read_text()
    assert "hidden-secret-reasoning" not in runtime.prompt("next")


def test_interrupted_batch_results_unknown_without_reexecution(tmp_path):
    runtime = agent(tmp_path, ["recovered"])
    call = ToolCall("c1", "write_file", {"path": "a", "content": "x"})
    runtime.record(
        {
            "role": "assistant",
            "content": "",
            "message": Message("assistant", (call,)).to_dict(),
        }
    )
    assert runtime.ask("Inspect state") == "recovered"
    assert not (tmp_path / "a").exists()
    assert results(runtime)[0].status == "unknown"


def test_native_history_keeps_current_user_and_whole_large_batch(tmp_path):
    content = "x" * 8000
    runtime = agent(
        tmp_path,
        [tool_response("write_file", {"path": "a", "content": content}), "done"],
    )
    runtime.ask("Create a very large file")
    messages = runtime.model_client.requests[-1].messages
    assert messages[0].text == "Create a very large file"
    call = next(call for message in messages for call in message.tool_calls)
    assert call.arguments["content"] == content
    assert any(
        isinstance(block, ToolResult) and block.call_id == call.call_id
        for message in messages
        for block in message.content
    )


def test_openai_unfinished_sse_tool_never_becomes_final():
    body = (
        sse(
            {
                "type": "response.output_item.added",
                "output_index": 0,
                "item": {"type": "function_call"},
            },
            {
                "type": "response.function_call_arguments.delta",
                "output_index": 0,
                "delta": '{"path":',
            },
        )
        + "data: [DONE]\n\n"
    )
    with pytest.raises(ProviderProtocolError, match="unfinished"):
        complete("openai", body, stream=True)


def test_anthropic_unfinished_block_never_executes():
    body = sse(
        {"type": "message_start", "message": {"role": "assistant"}},
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {
                "type": "tool_use",
                "id": "c1",
                "name": "read_file",
                "input": {},
            },
        },
        {"type": "message_stop"},
    )
    with pytest.raises(ProviderProtocolError, match="unfinished"):
        complete("anthropic", body, stream=True)


@pytest.mark.parametrize("provider", ["openai", "anthropic", "ollama"])
def test_provider_token_limit_is_not_end_turn(provider):
    data = wire(
        provider,
        stop={"openai": "incomplete", "anthropic": "max_tokens", "ollama": "length"}[
            provider
        ],
    )
    if provider == "openai":
        data["incomplete_details"] = {"reason": "max_output_tokens"}
    assert complete(provider, data)[0].stop_reason == "max_tokens"


def test_unmatched_result_rejected_before_http():
    req = replace(
        request(),
        messages=request().messages
        + (Message("tool", (ToolResult("unknown", "read_file", "x"),)),),
    )
    with patch(
        "urllib.request.urlopen", side_effect=AssertionError("must not request")
    ):
        with pytest.raises(ProviderProtocolError, match="pending call"):
            client("openai").complete(req)


def test_unknown_openai_content_fails_explicitly():
    with pytest.raises(ProviderProtocolError, match="unsupported"):
        complete(
            "openai",
            wire(
                "openai", [{"type": "message", "content": [{"type": "future_block"}]}]
            ),
        )


def test_integer_schema_rejects_bool_before_shell_execution(tmp_path):
    runtime = agent(
        tmp_path,
        [tool_response("run_shell", {"command": "echo x", "timeout": True}), "done"],
    )
    with patch.object(
        runtime, "approve", side_effect=AssertionError("approval must not happen")
    ):
        runtime.ask("Run command")
    assert results(runtime)[0].status == "rejected"


def test_provider_error_is_failed_and_persisted(tmp_path):
    runtime = agent(tmp_path, [RuntimeError("backend unavailable")])
    with pytest.raises(RuntimeError, match="unavailable"):
        runtime.ask("Do work")
    report = json.loads((Path(runtime.current_run_dir) / "report.json").read_text())
    assert report["status"] == "failed"
    assert report["stop_reason"] == "model_error"


def test_metadata_allowlist_does_not_publish_raw_state(tmp_path):
    response = replace(
        tool_response("list_files", {"path": "."}),
        metadata={"raw_response": "private-thinking", "cached_tokens": 3},
    )
    runtime = agent(tmp_path, [response, "done"])
    runtime.ask("Inspect")
    for path in Path(runtime.current_run_dir).glob("*"):
        if path.is_file():
            assert "private-thinking" not in path.read_text()


def test_reused_call_id_in_session_has_no_second_side_effect(tmp_path):
    runtime = agent(
        tmp_path,
        [
            tool_response("write_file", {"path": "a", "content": "first"}, "c1"),
            tool_response("write_file", {"path": "a", "content": "second"}, "c1"),
            "done",
        ],
    )
    assert runtime.ask("Create a") == "done"
    assert (tmp_path / "a").read_text() == "first"
    assert runtime.current_task_state.tool_steps == 1


def test_openai_assistant_phase_preserved_with_official_input_shape():
    response, _, _ = complete(
        "openai",
        wire(
            "openai",
            [
                {
                    "type": "message",
                    "phase": "commentary",
                    "content": [{"type": "output_text", "text": "Checking"}],
                },
                {
                    "type": "function_call",
                    "call_id": "c1",
                    "name": "read_file",
                    "arguments": '{"path":"a"}',
                },
            ],
        ),
    )
    req = replace(
        request(),
        messages=request().messages
        + (response.message, Message("tool", (ToolResult("c1", "read_file", "ok"),))),
    )
    _, captured, _ = complete("openai", wire("openai"), req)
    assistant = next(
        item for item in captured["body"]["input"] if item.get("role") == "assistant"
    )
    assert assistant == {
        "role": "assistant",
        "content": "Checking",
        "phase": "commentary",
    }


def test_finalization_remains_available_at_attempt_limit(tmp_path):
    runtime = agent(
        tmp_path,
        ["", "", "", "", tool_response("list_files", {"path": "."}), "finished"],
        max_steps=1,
    )
    assert runtime.ask("Inspect workspace") == "finished"
    assert runtime.current_task_state.attempts == 6
    assert runtime.current_task_state.tool_steps == 1
    assert sum(req.tool_choice == "none" for req in runtime.model_client.requests) == 1
