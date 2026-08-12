# zzcode-bench-v1

这是 Phase 6 使用的最小纵向切片数据集。任务来自 zzcode 的真实 Git 历史，目标是验证从公开 Repo Task、真实 Agent 推理、`git diff` Patch，到 Docker 隔离评分和报告生成的完整链路。

## 当前范围

- `ZZCODE-BUG-001`：单文件模型响应处理缺陷。
- `ZZCODE-BUG-002`：跨文件私密路径隔离缺陷。
- `dev` split：包含以上两个试运行任务，不作为正式 Pass@1 榜单。

## 数据隔离

本目录只包含 Agent 可以看到的题目描述和元数据。Gold Patch、hidden tests、FAIL_TO_PASS 与 PASS_TO_PASS 位于独立的本地私有根目录，默认路径为 `evaluation/private/zzcode-bench-v1/`，该目录已被 Git 忽略。

## 有效性门禁

每项任务在真实 Agent 推理前必须满足：

1. Null Validation：不应用修复时不能为 `FULL`。
2. Gold Validation：应用 Gold Patch 连续运行 3 次，全部为 `FULL`。
3. Agent Evaluation：无论成功、失败或超时，均生成完整、可追踪的任务产物。

Phase 7 扩充数据后再冻结正式 `dev/test` 划分与版本。
