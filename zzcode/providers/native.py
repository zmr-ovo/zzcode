"""Official wire formats; no text-command parsing or legacy fallback."""

import json
import time
import urllib.error
import urllib.request
from copy import deepcopy
from functools import wraps
from http.client import RemoteDisconnected
from uuid import uuid4

from ..core.messages import (
    Message,
    ModelCapabilities,
    ModelRequest,
    ModelResponse,
    OpaqueBlock,
    ProviderProtocolError,
    TextBlock,
    ToolCall,
    ToolResult,
    Usage,
    text_response,
)


def _provider_contract(method):
    @wraps(method)
    def invoke(self, request):
        if not isinstance(request, ModelRequest):
            raise TypeError("complete requires a ModelRequest")
        try:
            request.validate()
            return method(self, request)
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise ProviderProtocolError(
                "backend returned malformed native response"
            ) from exc

    return invoke


class FakeModelClient:
    """Structured test stub. Strings are literal text, never executable commands."""

    capabilities = ModelCapabilities()

    def __init__(self, outputs):
        self.outputs, self.prompts, self.requests = list(outputs), [], []
        self.supports_prompt_cache = False
        self.last_completion_metadata = {}

    def complete(self, request: ModelRequest):
        if not isinstance(request, ModelRequest):
            raise TypeError("complete requires a ModelRequest")
        self.requests.append(deepcopy(request))
        self.prompts.append(request.debug_text)
        if not self.outputs:
            raise RuntimeError("fake model ran out of outputs")
        output = self.outputs.pop(0)
        if isinstance(output, Exception):
            raise output
        if isinstance(output, str):
            return text_response(output)
        if isinstance(output, dict):
            return ModelResponse.from_dict(deepcopy(output))
        if not isinstance(output, ModelResponse):
            raise ProviderProtocolError("stub response must be a ModelResponse")
        return deepcopy(output).validate()


def _base_url(base):
    base = str(base).rstrip("/")
    return base if base.endswith("/v1") else base + "/v1"


def _http(url, payload, headers, timeout, provider):
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode(), headers=headers, method="POST"
    )
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read().decode(), getattr(response, "headers", {}).get(
                    "Content-Type", ""
                )
        except urllib.error.HTTPError as exc:
            if exc.code >= 500 and attempt < 2:
                time.sleep(0.5 * (attempt + 1))
                continue
            raise RuntimeError(
                f"{provider} request failed with HTTP {exc.code}"
            ) from exc
        except (
            urllib.error.URLError,
            RemoteDisconnected,
            TimeoutError,
            ConnectionResetError,
        ) as exc:
            if attempt < 2:
                time.sleep(0.5 * (attempt + 1))
                continue
            raise RuntimeError(f"Could not reach {provider} backend") from exc


def _object(body):
    try:
        value = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ProviderProtocolError("backend returned non-JSON content") from exc
    if not isinstance(value, dict):
        raise ProviderProtocolError("backend response must be an object")
    if value.get("error"):
        raise RuntimeError("backend returned a provider error")
    return value


def _events(body):
    data = []
    for line in [*body.splitlines(), ""]:
        if line.startswith("data:"):
            data.append(line[5:].lstrip())
        elif not line and data:
            joined, data = "\n".join(data), []
            yield {"type": "done"} if joined == "[DONE]" else _object(joined)


def _openai_sse(body):
    items, deltas, finished = {}, {}, False
    pending = set()
    for event in _events(body):
        kind = event.get("type")
        if kind in ("error", "response.failed"):
            raise RuntimeError("OpenAI stream failed")
        if kind in ("response.completed", "response.incomplete"):
            if not isinstance(event.get("response"), dict):
                raise ProviderProtocolError("stream terminal event is missing response")
            return event["response"]
        if kind == "response.output_item.added":
            pending.add(event.get("output_index", 0))
        elif kind == "response.function_call_arguments.delta":
            pending.add(event.get("output_index", 0))
        elif kind == "response.output_item.done":
            index = event.get("output_index", len(items))
            items[index] = event["item"]
            pending.discard(index)
        elif kind == "response.output_text.delta":
            deltas.setdefault(event.get("output_index", 0), []).append(
                event.get("delta", "")
            )
        elif kind == "response.output_text.done":
            items[event.get("output_index", 0)] = {
                "type": "message",
                "content": [{"type": "output_text", "text": event["text"]}],
            }
        elif kind == "done":
            finished = True
    if pending:
        raise ProviderProtocolError("OpenAI stream contains unfinished output items")
    if not finished:
        raise ProviderProtocolError("OpenAI stream ended without a terminal event")
    for index, parts in deltas.items():
        if index not in items:
            items[index] = {
                "type": "message",
                "content": [{"type": "output_text", "text": "".join(parts)}],
            }
    return {"status": "completed", "output": [items[index] for index in sorted(items)]}


def _anthropic_sse(body):
    message, blocks = None, {}
    pending = set()
    for event in _events(body):
        kind = event.get("type")
        if kind == "error":
            raise RuntimeError("Anthropic stream failed")
        if kind == "message_start":
            message = deepcopy(event["message"])
        elif kind == "content_block_start":
            blocks[event["index"]] = deepcopy(event["content_block"])
            pending.add(event["index"])
        elif kind == "content_block_delta":
            block, delta = blocks[event["index"]], event["delta"]
            for key in ("text", "thinking", "signature"):
                if key in delta:
                    block[key] = block.get(key, "") + delta[key]
            if "partial_json" in delta:
                block["_partial_json"] = (
                    block.get("_partial_json", "") + delta["partial_json"]
                )
        elif kind == "content_block_stop":
            pending.remove(event["index"])
            block = blocks[event["index"]]
            if "_partial_json" in block:
                block["input"] = block.pop("_partial_json")
        elif kind == "message_delta" and message is not None:
            message.update(event.get("delta", {}))
            message.setdefault("usage", {}).update(event.get("usage", {}))
        elif kind == "message_stop" and message is not None:
            if pending:
                raise ProviderProtocolError(
                    "Anthropic stream contains unfinished blocks"
                )
            message["content"] = [blocks[index] for index in sorted(blocks)]
            return message
    raise ProviderProtocolError("Anthropic stream ended without message_stop")


def _arguments(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return {}, "tool arguments contain invalid JSON"
    if not isinstance(value, dict):
        return {}, "tool arguments must be a JSON object"
    return value, None


def _number(value):
    return (
        value
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0
        else None
    )


def _openai_input(messages):
    items = []
    for message in messages:
        text = []
        phase = None

        def flush():
            if text:
                content = (
                    "".join(text)
                    if message.role == "assistant"
                    else [{"type": "input_text", "text": "".join(text)}]
                )
                item = {"role": message.role, "content": content}
                if message.role == "assistant" and phase is not None:
                    item["phase"] = phase
                items.append(item)
                text.clear()

        for block in message.content:
            if isinstance(block, TextBlock):
                if block.phase != phase:
                    flush()
                    phase = block.phase
                text.append(block.text)
                continue
            flush()
            if isinstance(block, ToolCall):
                items.append(
                    {
                        "type": "function_call",
                        "call_id": block.call_id,
                        "name": block.name,
                        "arguments": json.dumps(block.arguments),
                    }
                )
            elif isinstance(block, ToolResult):
                items.append(
                    {
                        "type": "function_call_output",
                        "call_id": block.call_id,
                        "output": block.content,
                    }
                )
            elif isinstance(block, OpaqueBlock):
                if block.provider != "openai":
                    raise ProviderProtocolError(
                        "signed provider state cannot be replayed to OpenAI"
                    )
                items.append(deepcopy(block.data))
        flush()
    return items


class OpenAICompatibleModelClient:
    """Responses API; compatible endpoints must support native tools."""

    def __init__(self, model, base_url, api_key, temperature, timeout):
        self.model, self.api_key, self.temperature, self.timeout = (
            model,
            api_key,
            temperature,
            timeout,
        )
        self.base_url = _base_url(base_url)
        self.supports_prompt_cache = any(
            host in self.base_url for host in ("openai.com", "right.codes")
        )
        self.capabilities = ModelCapabilities(prompt_cache=self.supports_prompt_cache)
        self.last_completion_metadata = {}

    @_provider_contract
    def complete(self, request: ModelRequest):
        self.last_completion_metadata = {}
        payload = {
            "model": self.model,
            "instructions": request.system,
            "input": _openai_input(request.messages),
            "max_output_tokens": request.max_output_tokens,
            "stream": False,
            "include": ["reasoning.encrypted_content"],
            "tool_choice": request.tool_choice,
            "tools": [
                {
                    "type": "function",
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.input_schema,
                    "strict": False,
                }
                for tool in request.tools
            ],
        }
        if self.temperature is not None:
            payload["temperature"] = self.temperature
        if self.supports_prompt_cache and request.cache_key:
            payload["prompt_cache_key"] = request.cache_key
        if self.supports_prompt_cache and request.cache_retention:
            payload["prompt_cache_retention"] = request.cache_retention
        body, content_type = _http(
            self.base_url + "/responses",
            payload,
            {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            self.timeout,
            "OpenAI-compatible",
        )
        data = (
            _openai_sse(body)
            if content_type.startswith("text/event-stream")
            or body.lstrip().startswith("data:")
            else _object(body)
        )
        blocks, refused = [], False
        for item in data.get("output", []):
            kind = item.get("type")
            if kind == "function_call":
                arguments, error = _arguments(item.get("arguments"))
                blocks.append(
                    ToolCall(
                        item.get("call_id", ""), item.get("name", ""), arguments, error
                    )
                )
            elif kind == "message":
                for content in item.get("content", []):
                    if content.get("type") == "output_text":
                        blocks.append(
                            TextBlock(content.get("text", ""), item.get("phase"))
                        )
                    elif content.get("type") == "refusal":
                        refused = True
                        blocks.append(
                            TextBlock(content.get("refusal", ""), item.get("phase"))
                        )
                    else:
                        raise ProviderProtocolError(
                            "unsupported OpenAI message content block"
                        )
            elif kind == "reasoning":
                blocks.append(OpaqueBlock("openai", deepcopy(item)))
            else:
                raise ProviderProtocolError(f"unsupported OpenAI output item: {kind}")
        if not blocks and isinstance(data.get("output_text"), str):
            blocks.append(TextBlock(data["output_text"]))
        if "choices" in data:
            raise ProviderProtocolError(
                "Chat Completions response returned on Responses endpoint"
            )
        status = data.get("status", "completed")
        if status == "incomplete":
            reason = (data.get("incomplete_details") or {}).get("reason")
            stop = (
                "max_tokens"
                if reason == "max_output_tokens"
                else "content_filter"
                if reason == "content_filter"
                else "unknown"
            )
        elif status == "completed":
            stop = (
                "tool_call"
                if any(isinstance(block, ToolCall) for block in blocks)
                else "end_turn"
            )
        elif status == "cancelled":
            stop = "cancelled"
        else:
            raise RuntimeError(f"OpenAI response did not complete: {status}")
        if refused:
            stop = "content_filter"
        usage = data.get("usage") or {}
        details = usage.get("input_tokens_details") or {}
        unified = Usage(
            _number(usage.get("input_tokens")),
            _number(usage.get("output_tokens")),
            _number(details.get("cached_tokens")),
            _number(usage.get("total_tokens")),
        )
        response = ModelResponse(
            Message("assistant", tuple(blocks)), stop, unified
        ).validate()
        if stop == "end_turn" and not response.text.strip() and blocks:
            raise ProviderProtocolError(
                f"OpenAI returned reasoning without final text or tool calls. Increase --max-new-tokens above {request.max_output_tokens}."
            )
        self.last_completion_metadata = {
            "prompt_cache_supported": self.supports_prompt_cache,
            "prompt_cache_key": request.cache_key,
            "prompt_cache_retention": request.cache_retention,
            **unified.metadata(),
            "cache_hit": bool(unified.cached_tokens),
        }
        return response


def _anthropic_messages(messages):
    result = []
    for message in messages:
        role = "user" if message.role == "tool" else message.role
        content = []
        for block in message.content:
            if isinstance(block, TextBlock):
                content.append({"type": "text", "text": block.text})
            elif isinstance(block, ToolCall):
                content.append(
                    {
                        "type": "tool_use",
                        "id": block.call_id,
                        "name": block.name,
                        "input": block.arguments,
                    }
                )
            elif isinstance(block, ToolResult):
                content.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.call_id,
                        "content": block.content,
                        "is_error": block.status != "succeeded",
                    }
                )
            elif isinstance(block, OpaqueBlock):
                if block.provider != "anthropic":
                    raise ProviderProtocolError(
                        "signed provider state cannot be replayed to Anthropic"
                    )
                content.append(deepcopy(block.data))
        if not content:
            continue
        if result and result[-1]["role"] == role:
            result[-1]["content"].extend(content)
        else:
            result.append({"role": role, "content": content})
    return result


class AnthropicCompatibleModelClient:
    def __init__(self, model, base_url, api_key, temperature, timeout):
        self.model, self.api_key, self.temperature, self.timeout = (
            model,
            api_key,
            temperature,
            timeout,
        )
        self.base_url = _base_url(base_url)
        self.supports_prompt_cache = False
        self.capabilities = ModelCapabilities()
        self.last_completion_metadata = {}

    @_provider_contract
    def complete(self, request: ModelRequest):
        self.last_completion_metadata = {}
        payload = {
            "model": self.model,
            "system": request.system,
            "messages": _anthropic_messages(request.messages),
            "max_tokens": request.max_output_tokens,
            "stream": False,
            "tool_choice": {"type": request.tool_choice},
            "tools": [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "input_schema": tool.input_schema,
                }
                for tool in request.tools
            ],
        }
        if self.temperature is not None:
            payload["temperature"] = self.temperature
        body, content_type = _http(
            self.base_url + "/messages",
            payload,
            {
                "Content-Type": "application/json",
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
            },
            self.timeout,
            "Anthropic-compatible",
        )
        data = (
            _anthropic_sse(body)
            if content_type.startswith("text/event-stream")
            else _object(body)
        )
        blocks = []
        for item in data.get("content", []):
            kind = item.get("type")
            if kind == "text":
                blocks.append(TextBlock(item.get("text", "")))
            elif kind == "tool_use":
                arguments, error = _arguments(item.get("input"))
                blocks.append(
                    ToolCall(item.get("id", ""), item.get("name", ""), arguments, error)
                )
            elif kind in ("thinking", "redacted_thinking"):
                blocks.append(OpaqueBlock("anthropic", deepcopy(item)))
            else:
                raise ProviderProtocolError(
                    f"unsupported Anthropic content block: {kind}"
                )
        if data.get("role", "assistant") != "assistant":
            raise ProviderProtocolError("Anthropic response must have assistant role")
        native_stop = data.get(
            "stop_reason",
            "tool_use"
            if any(isinstance(block, ToolCall) for block in blocks)
            else "end_turn",
        )
        stop = {
            "end_turn": "end_turn",
            "stop_sequence": "end_turn",
            "tool_use": "tool_call",
            "max_tokens": "max_tokens",
            "refusal": "content_filter",
        }.get(native_stop, "unknown")
        usage = data.get("usage") or {}
        read, write = (
            _number(usage.get("cache_read_input_tokens")),
            _number(usage.get("cache_creation_input_tokens")),
        )
        uncached, output = (
            _number(usage.get("input_tokens")),
            _number(usage.get("output_tokens")),
        )
        total_input = (
            None if uncached is None else uncached + (read or 0) + (write or 0)
        )
        unified = Usage(
            total_input,
            output,
            read,
            None if total_input is None or output is None else total_input + output,
        )
        response = ModelResponse(
            Message("assistant", tuple(blocks)), stop, unified
        ).validate()
        self.last_completion_metadata = unified.metadata()
        return response


class OllamaModelClient:
    def __init__(self, model, host, temperature, top_p, timeout):
        self.model, self.host, self.temperature, self.top_p, self.timeout = (
            model,
            host.rstrip("/"),
            temperature,
            top_p,
            timeout,
        )
        self.supports_prompt_cache = False
        self.capabilities = ModelCapabilities()
        self.last_completion_metadata = {}

    @_provider_contract
    def complete(self, request: ModelRequest):
        self.last_completion_metadata = {}
        messages = [{"role": "system", "content": request.system}]
        for message in request.messages:
            if any(isinstance(block, OpaqueBlock) for block in message.content):
                raise ProviderProtocolError(
                    "signed provider state cannot be replayed to Ollama"
                )
            if message.role == "tool":
                messages.extend(
                    {"role": "tool", "tool_name": block.name, "content": block.content}
                    for block in message.content
                    if isinstance(block, ToolResult)
                )
            else:
                item = {"role": message.role, "content": message.text}
                if message.tool_calls:
                    item["tool_calls"] = [
                        {"function": {"name": call.name, "arguments": call.arguments}}
                        for call in message.tool_calls
                    ]
                messages.append(item)
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "think": False,
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.input_schema,
                    },
                }
                for tool in request.tools
            ]
            if request.tool_choice != "none"
            else [],
            "options": {
                "num_predict": request.max_output_tokens,
                "temperature": self.temperature,
                "top_p": self.top_p,
            },
        }
        body, _ = _http(
            self.host + "/api/chat",
            payload,
            {"Content-Type": "application/json"},
            self.timeout,
            "Ollama",
        )
        data = _object(body)
        item = data.get("message")
        if not isinstance(item, dict):
            raise ProviderProtocolError("Ollama Chat response is missing message")
        if item.get("role", "assistant") != "assistant":
            raise ProviderProtocolError("Ollama response must have assistant role")
        blocks = [TextBlock(item["content"])] if item.get("content") else []
        for call in item.get("tool_calls", []):
            function = call.get("function") or {}
            arguments, error = _arguments(function.get("arguments"))
            blocks.append(
                ToolCall(
                    call.get("id") or "ollama_" + uuid4().hex,
                    function.get("name", ""),
                    arguments,
                    error,
                )
            )
        stop = (
            "max_tokens"
            if data.get("done_reason") == "length"
            else "tool_call"
            if any(isinstance(block, ToolCall) for block in blocks)
            else "end_turn"
        )
        if data.get("done") is False or data.get("done_reason", "stop") not in {
            "stop",
            "length",
        }:
            stop = "unknown"
        usage = Usage(
            _number(data.get("prompt_eval_count")), _number(data.get("eval_count"))
        )
        response = ModelResponse(
            Message("assistant", tuple(blocks)), stop, usage
        ).validate()
        self.last_completion_metadata = usage.metadata()
        return response
