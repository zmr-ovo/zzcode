# zzcode-bench-v1

`zzcode-bench-v1` 是面向 zzcode Coding Agent 的内部可执行评测集。每项任务固定到真实 Git base commit，Agent 只接收公开题面，最终提交标准 Git patch；独立 Grader 在干净仓库中注入 hidden tests，并分别计算 F2P 与 P2P。

## v1 范围

| ID | 内容 | 类型 | 范围 | Split | 来源 |
|---|---|---|---|---|---|
| `ZZCODE-BUG-001` | reasoning-only 响应错误诊断 | bug fix | single-file | dev | Git history |
| `ZZCODE-BUG-002` | `.env` 工作区隔离 | security fix | multi-file | dev | Git history |
| `ZZCODE-BUG-003` | 工具预算后的 finalization turn | bug fix | single-file | dev | Git history |
| `ZZCODE-BUG-004` | 全局环境文件定位 | bug fix | single-file | dev | Git history |
| `ZZCODE-BUG-005` | CLI 环境文件加载与优先级 | bug fix | multi-file | test | Git history |
| `ZZCODE-BUG-006` | CLI/runtime 默认预算一致性 | configuration fix | multi-file | test | Git history |
| `ZZCODE-BUG-007` | `.env.*` 私密路径隔离 | security fix | multi-file | test | curated regression |
| `ZZCODE-BUG-008` | shell 子进程密钥边界 | security fix | single-file | test | curated regression |

`dev` 用于实现期调试；`test` 用于固定配置后的正式 Pass@1；`all` 只用于数据质量门禁和完整演示，不用于反复调参。

## 数据来源与许可边界

- 001–006 来自 zzcode 自身修复历史，经过拆分后形成可独立应用和评分的任务。
- 007–008 是根据同一代码基线审查得到的安全回归任务。
- 所有任务均以 `b0f9c84b9fcec9901684c70d6c29edad5357761b` 为 base commit。
- 数据集不包含第三方仓库代码或第三方 benchmark 答案。

## 公开/私有隔离

本目录只包含 Agent 可见的题面和公开元数据。Gold patch、hidden test patch、FAIL_TO_PASS 与 PASS_TO_PASS 位于独立私有根目录：

```text
evaluation/private/zzcode-bench-v1/<instance-id>/
├── grading.json
├── gold.patch
└── test.patch
```

私有目录被 Git 忽略，正式运行时通过 `--private-root` 或 `ZZCODE_EVAL_PRIVATE_ROOT` 提供。Agent inference 容器不会挂载该目录。

## 有效性门禁

每项任务进入模型推理前必须满足：

1. Null Validation：不应用修复时，F2P 至少一项失败且 P2P 全部通过。
2. Gold Validation ×3：参考修复连续三次均为 `FULL`。
3. Patch Safety：Gold 与 Agent patch 都通过路径、体积和二进制文件检查。
4. Isolation：公开数据不存在 hidden test、Gold 或评分 selector 泄漏。
5. Artifact Completeness：Agent 成功、失败或超时均保存结构化产物。

## 冻结版本

`dataset-lock.json` 固定各 split 的任务数与完整数据 digest。digest 同时覆盖公开任务定义、Gold patch、test patch 和评分 selector；私有包发生任何变化都会导致校验值变化。

## 指标

- 主指标：Pass@1 / Resolution Rate，即 `FULL` 任务数除以任务总数。
- 诊断指标：F2P rate、P2P rate、patch generated rate、dataset gate pass rate。
- 错误分布：`AGENT_ERROR`、`INFRA_ERROR`、`DATASET_ERROR` 分开统计。

## 限制

- 8 项任务只适合验证评测闭环和小规模纵向比较，不代表通用软件工程能力。
- 多项任务来自同一个历史 commit，任务之间可能存在领域相关性。
- 正式结果必须同时记录 dataset digest、Agent commit、模型参数和 Docker image digest。
- 不能使用 Gold patch、FakeLLM 或 scripted output 生成正式 Agent 成绩。
