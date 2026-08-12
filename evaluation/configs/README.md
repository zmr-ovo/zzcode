# Evaluation 配置

本目录保存 provider、模型、资源限制、执行环境和 release gate 配置。

Phase 5 提供 `agent-zzcode.example.json`。复制后必须显式填写真实 provider 和 model；配置拒绝 Fake、Stub、Mock 模型名称，并强制 Agent tool plane 禁网。

配置文件禁止保存 API Key 或其他凭据。密钥只能通过环境变量传入：

- OpenAI-compatible：`OPENAI_API_KEY`
- Anthropic-compatible：`ANTHROPIC_API_KEY`，也兼容 `RIGHT_CODES_API_KEY` 或 `OPENAI_API_KEY`
- Ollama：默认不需要密钥

Provider worker 可以连接配置的模型 endpoint。模型调用发出的 `run_shell` 会进入独立 Docker 容器，该容器使用 `network=none`，不会继承 provider 密钥；写文件和 patch 工具仍受 workspace 路径约束。评分阶段继续使用 Phase 4 的独立只读 workspace 容器。
