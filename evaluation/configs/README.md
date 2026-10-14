# 评测配置

本目录只保留当前配置示例：

- `agent-zzcode.example.json`：Agent 推理配置，包含 Provider、模型、超时、审批与工具镜像。
- `evaluation.example.json`：完整评测建议参数，包括任务 split、评分资源及 Gold 重复次数。

两份示例用途不同，不是同一 JSON schema。运行完整评测时用 `scripts/evaluation/run_internal_eval.py` 的对应命令行参数；脚本目前不直接读取该 JSON。开发测试使用 `--split dev`，正式留出集使用 `--split test`；可重复传入 `--instance-id` 做小规模 smoke。

必须显式填写真实 provider/model。密钥仅从环境变量传入，不保存到配置、Manifest 或公开日志。Agent Shell 容器禁网且不继承 Provider 密钥；Provider worker 可以访问模型 endpoint。评分使用独立只读任务容器。

已有镜像示例使用 `zzcode-eval-py313:phase4` 标签，保留该名称是为了兼容现有镜像；实际运行固定到不可变 digest。构建方法见 `evaluation/environments/zzcode-py313/README.md`。
