"""没有可靠 tokenizer 时用 UTF-8 字节保守估算，明确记录来源。"""

import json
from dataclasses import asdict


class ContextCapacityError(RuntimeError):
    pass


class ContextOverflowError(RuntimeError):
    """仅由后端明确的上下文容量错误触发。"""


class TokenBudget:
    def __init__(
        self, window=32768, max_output=4096, safety=1024, source="conservative-default"
    ):
        if window <= 0 or max_output <= 0 or safety < 0:
            raise ValueError("context limits must be positive")
        self.window, self.max_output, self.safety, self.source = (
            window,
            max_output,
            safety,
            source,
        )
        self.calibration = 1.0

    def estimate(self, request):
        value = {
            "system": request.system,
            "messages": [m.to_dict() for m in request.messages],
            "tools": [asdict(spec) for spec in request.tools],
            "tool_choice": request.tool_choice,
        }
        size = len(json.dumps(value, ensure_ascii=False).encode("utf-8"))
        return int((size + 128 + 32 * len(request.messages)) * self.calibration)

    def observe(self, estimated, usage):
        actual = usage.input_tokens
        if actual is not None and estimated:
            self.calibration = max(self.calibration, actual / estimated * 1.1)

    def available(self, output):
        return self.window - min(output, self.max_output) - self.safety
