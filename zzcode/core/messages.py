"""Ordered native messages. Text is never parsed as a tool command."""

from dataclasses import asdict, dataclass, field
from typing import Literal, Protocol
from uuid import uuid4


class ProviderProtocolError(RuntimeError):
    """A backend returned an invalid or unsupported structured response."""


@dataclass(frozen=True)
class TextBlock:
    text: str
    phase: str | None = None
    type: str = field(default="text", init=False)


@dataclass(frozen=True)
class ToolCall:
    call_id: str
    name: str
    arguments: dict
    argument_error: str | None = None
    type: str = field(default="tool_call", init=False)


@dataclass(frozen=True)
class ToolResult:
    call_id: str
    name: str
    content: str
    status: str = "succeeded"
    type: str = field(default="tool_result", init=False)


@dataclass(frozen=True)
class OpaqueBlock:
    """Signed thinking/reasoning retained for provider replay, never telemetry."""

    provider: str
    data: dict
    type: str = field(default="opaque", init=False)


ContentBlock = TextBlock | ToolCall | ToolResult | OpaqueBlock


def block_from_dict(value):
    value = dict(value)
    kind = value.pop("type")
    classes = {
        "text": TextBlock,
        "tool_call": ToolCall,
        "tool_result": ToolResult,
        "opaque": OpaqueBlock,
    }
    if kind not in classes:
        raise ProviderProtocolError(f"unsupported content block: {kind}")
    return classes[kind](**value)


@dataclass(frozen=True)
class Message:
    role: Literal["user", "assistant", "tool"]
    content: tuple[ContentBlock, ...]

    @property
    def text(self):
        return "".join(
            block.text for block in self.content if isinstance(block, TextBlock)
        )

    @property
    def tool_calls(self):
        return tuple(block for block in self.content if isinstance(block, ToolCall))

    def to_dict(self):
        return {"role": self.role, "content": [asdict(block) for block in self.content]}

    @classmethod
    def from_dict(cls, value):
        try:
            return cls(
                value["role"],
                tuple(block_from_dict(block) for block in value["content"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderProtocolError("malformed saved native message") from exc


@dataclass(frozen=True)
class Usage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_tokens: int | None = None
    total_tokens: int | None = None

    def metadata(self):
        return {
            name: value for name, value in asdict(self).items() if value is not None
        }


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict
    side_effect: str = "none"
    risk_level: str = "low"


@dataclass(frozen=True)
class ModelCapabilities:
    native_tools: bool = True
    prompt_cache: bool = False
    streaming: bool = False


@dataclass(frozen=True)
class ModelRequest:
    system: str
    messages: tuple[Message, ...]
    tools: tuple[ToolSpec, ...] = ()
    max_output_tokens: int = 2048
    tool_choice: Literal["auto", "none"] = "auto"
    cache_key: str | None = None
    cache_retention: str | None = None
    debug_text: str = ""

    def validate(self):
        pending = set()
        seen = set()
        for message in self.messages:
            if message.role not in {"user", "assistant", "tool"}:
                raise ProviderProtocolError("unsupported message role")
            if pending and message.role != "tool":
                raise ProviderProtocolError(
                    "tool results must complete a batch before the next message"
                )
            for block in message.content:
                if isinstance(block, ToolCall):
                    if (
                        message.role != "assistant"
                        or not block.call_id
                        or block.call_id in seen
                    ):
                        raise ProviderProtocolError(
                            "invalid or duplicate request tool call ID"
                        )
                    seen.add(block.call_id)
                    pending.add(block.call_id)
                elif isinstance(block, ToolResult):
                    if message.role != "tool" or block.call_id not in pending:
                        raise ProviderProtocolError(
                            "tool result does not match a pending call"
                        )
                    pending.remove(block.call_id)
                elif isinstance(block, TextBlock):
                    if message.role == "tool" or not isinstance(block.text, str):
                        raise ProviderProtocolError("invalid message text")
                elif isinstance(block, OpaqueBlock):
                    if message.role != "assistant":
                        raise ProviderProtocolError(
                            "opaque state must belong to assistant"
                        )
                else:
                    raise ProviderProtocolError("unsupported request content block")
        if pending:
            raise ProviderProtocolError("request has incomplete tool results")
        if self.tool_choice not in {"auto", "none"}:
            raise ProviderProtocolError("unsupported tool choice")
        return self


@dataclass(frozen=True)
class ModelResponse:
    message: Message
    stop_reason: str = "end_turn"
    usage: Usage = field(default_factory=Usage)
    metadata: dict = field(default_factory=dict)

    @property
    def tool_calls(self):
        return self.message.tool_calls

    @property
    def text(self):
        return self.message.text

    def validate(self):
        for block in self.message.content:
            if not isinstance(block, (TextBlock, ToolCall, OpaqueBlock)):
                raise ProviderProtocolError("unsupported model content block")
            if isinstance(block, TextBlock) and not isinstance(block.text, str):
                raise ProviderProtocolError("model text must be a string")
            if isinstance(block, TextBlock) and block.phase not in {
                None,
                "commentary",
                "final_answer",
            }:
                raise ProviderProtocolError("unsupported assistant text phase")
            if (
                isinstance(block, ToolCall)
                and block.argument_error is not None
                and not isinstance(block.argument_error, str)
            ):
                raise ProviderProtocolError("argument error must be text")
            if isinstance(block, ToolCall) and not isinstance(block.arguments, dict):
                raise ProviderProtocolError("tool arguments must be an object")
            if isinstance(block, OpaqueBlock) and (
                not isinstance(block.data, dict)
                or block.provider not in {"openai", "anthropic"}
            ):
                raise ProviderProtocolError("invalid opaque provider state")
        if self.stop_reason not in {
            "end_turn",
            "tool_call",
            "max_tokens",
            "content_filter",
            "cancelled",
            "unknown",
        }:
            raise ProviderProtocolError("unsupported stop reason")
        if bool(self.tool_calls) != (
            self.stop_reason == "tool_call"
        ) and self.stop_reason in {"tool_call", "end_turn"}:
            raise ProviderProtocolError("stop reason disagrees with tool calls")
        if self.message.role != "assistant":
            raise ProviderProtocolError("model response must have assistant role")
        calls = self.tool_calls
        ids = [call.call_id for call in calls]
        if any(not isinstance(value, str) or not value for value in ids) or len(
            ids
        ) != len(set(ids)):
            raise ProviderProtocolError(
                "tool call IDs must be non-empty and unique within a response"
            )
        if any(not isinstance(call.name, str) or not call.name for call in calls):
            raise ProviderProtocolError("tool call is missing its name")
        if any(isinstance(block, ToolResult) for block in self.message.content):
            raise ProviderProtocolError("model response cannot contain tool results")
        return self

    def to_dict(self):
        return {
            "message": self.message.to_dict(),
            "stop_reason": self.stop_reason,
            "usage": asdict(self.usage),
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, value):
        return cls(
            Message.from_dict(value["message"]),
            value.get("stop_reason", "end_turn"),
            Usage(**value.get("usage", {})),
            value.get("metadata", {}),
        ).validate()


class ModelProvider(Protocol):
    capabilities: ModelCapabilities

    def complete(self, request: ModelRequest) -> ModelResponse: ...


def text_response(text, stop_reason="end_turn"):
    return ModelResponse(Message("assistant", (TextBlock(text),)), stop_reason)


def tool_response(name, arguments, call_id=None):
    error = (
        None if isinstance(arguments, dict) else "tool arguments must be a JSON object"
    )
    return ModelResponse(
        Message(
            "assistant",
            (
                ToolCall(
                    call_id or "call_" + uuid4().hex,
                    name,
                    arguments if error is None else {},
                    error,
                ),
            ),
        ),
        "tool_call",
    )
