# Evaluation 数据集

本目录只允许保存 Agent 可见的公开任务数据。私有评分数据必须放在仓库之外，并通过 `ZZCODE_EVAL_PRIVATE_ROOT` 指定。

Phase 1 不提交尚未认证的正式任务。后续只有同时满足以下条件的 Repo Task 才能加入正式数据集：

- Null Patch 能证明 `base_commit` 上的问题确实存在；
- Gold Patch 能让 F2P 和 P2P 全部通过；
- Agent 无法读取私有测试和 Gold Patch；
- 多次运行得到稳定、一致的评分结果。

完整目录规范和校验命令见 [`evaluation/README.md`](../README.md)。
