# OpenAI-compatible reasoning-only 响应没有明确报错

`OpenAICompatibleModelClient.complete()` 在兼容 `/responses` 的服务返回 reasoning 内容、但没有返回可执行文本时，会把响应当成空字符串返回。上层 Agent 随后只能看到模糊的“empty response”，无法知道输出预算可能不足。

请修改实现：

- 保留现有文本响应和事件流响应的行为。
- 当响应存在非空 reasoning、但不存在可执行文本时，抛出清晰的 `RuntimeError`。
- 错误信息应说明输出预算不足，并提示提高 `--max-new-tokens`。
- 不要破坏其他 OpenAI-compatible 响应格式。
