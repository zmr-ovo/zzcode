# P1：原生工具协议迁移

分支：`codex/native-tool-protocol`。P0 基线提交：`464428c`。

## 实现

- `core/messages.py` 定义有序内容块、请求/响应、工具声明、Usage 与协议校验。工具快捷属性从消息派生。
- `providers/native.py` 实现 OpenAI Responses、Claude Messages 和 Ollama `/api/chat`；`models.py` 保留公共导出位置。
- 已删除 `parse()`、XML/文本标签解析、旧提示词调用示例及 Legacy Adapter。FakeModel 字符串只是普通文本。
- ContextManager 构建 system 和分角色历史。调用/结果批次整体保留，当前用户请求不丢失，工具参数不裁剪。旧会话的工具文本作为历史观察。
- 工具声明使用 JSON Schema；本地验证 object、required、additionalProperties、string/integer，保留路径、范围、审批、只读和重复调用护栏。
- 多调用按内容块顺序串行执行。每个 call ID 都有对应结果，包括非法参数、审批拒绝、预算取消和中断后 `unknown`。
- `max_tokens`、过滤、取消和未知停止状态不当作成功，不执行其中的工具。预算用尽仅允许一次禁用工具的最终回答请求。
- 思考/推理状态仅用于 Provider 续接；Session 原子保存且权限 `0600`。公开日志使用 metadata 允许字段，不暴露签名思考。OpenAI assistant phase 保留。

## 验证

149 项产品测试、115 项评测框架测试、7 条 Golden 和两次各 12 条机制回归全部通过。44 项原生协议测试包含在产品测试中。Ruff、安装导入与 CLI 检查通过。

完整结果以 `artifacts/p1-baseline/verified/manifest.json` 为准；原生真实后端往返记录见 `artifacts/p1-baseline/native-smoke.json`。

真实 OpenAI-compatible 后端只读往返已通过：读取临时 `probe.txt`，回传匹配的 call ID，再正确返回文件内容。这个测试不使用 Docker，不修改项目业务文件。

Claude 和 Ollama 使用确定性 HTTP 契约测试验证，未宣称真实服务 Smoke 已通过。原先两个真实仓库 Smoke 仍单独记录 Docker 环境状态。

Golden 的预算场景有一项有意变化：旧版丢弃额外工具提案；新版保存关联取消结果，工具执行次数不变。P0 冻结证据保持不变，重放旧行为需使用 P0 提交。当前 Golden 和 12 条机制回归已迁移为原生消息。

## 边界

- OpenAI-compatible 端点必须支持 Responses 和原生工具调用；不自动回退 Chat Completions 或文本协议。
- HTTP 为同步、缓冲响应；可解码兼容服务返回的 SSE，但没有提供 P4 流式事件/UI。
- 原有字符预算仍是软限制；保存完整批次可能超限，新增 `native_request_chars/native_request_over_budget`。P3 再实现 token 预算与容量预检。
- P2 的独立 Gateway、操作账本、严格恢复幂等和结构化 Shell Executor 尚未实施。`unknown` 仅避免自动重放，并不保证外部操作恰好执行一次。
- 不透明 Provider 状态不可跨 Provider 重放。必要状态不兼容时明确拒绝，不静默丢弃。

## 协议来源

- [OpenAI Function Calling](https://developers.openai.com/api/docs/guides/function-calling)
- [OpenAI 官方 SDK 输入消息类型](https://github.com/openai/openai-python/blob/main/src/openai/types/responses/easy_input_message_param.py)
- [Claude Tool Use](https://platform.claude.com/docs/en/agents-and-tools/tool-use/define-tools)
- [Ollama Tool Calling](https://docs.ollama.com/capabilities/tool-calling)
