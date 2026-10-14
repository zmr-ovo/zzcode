# 真实仓库评测

`evaluation/` 保存公开任务、配置与 Docker 环境；实现位于 `zzcode/evaluation/`，测试位于 `tests/evaluation/`。

```text
configs/        Agent 与完整评测配置示例
datasets/       公开任务、split、数据锁及题面
environments/   Dockerfile 与依赖锁定
```

隐藏测试、Gold Patch 和评分选择器通过 `evaluation/private/` 或 `ZZCODE_EVAL_PRIVATE_ROOT` 单独提供，不提交、不进入 Agent 上下文。

## 测试与任务数据集

| 内容 | 位置 | 验证什么 |
|---|---|---|
| 产品测试 | `tests/agent/`、`context/`、`execution/`、`storage/`、`providers/` | Agent 的协议、工具、安全、压缩和恢复行为 |
| 评测框架测试 | `tests/evaluation/` | 数据加载、Patch 处理、隔离、评分和报告是否正确 |
| 真实任务数据集 | `evaluation/datasets/` | 真实模型能否根据题面修复指定仓库的问题 |
| 隐藏评分测试 | 私有包中的 `test.patch` 和 `grading.json` | Agent 的修改是否满足任务要求，且没有破坏原有行为 |

一个任务 case 不是一个 pytest 用例：它包含题面、仓库版本、执行环境和评分输入，可以对应多个测试用例。`pytest` 通过不能代替真实任务解决率。

当前 `zzcode-bench-v1` 有 8 个任务，按协议、运行时、配置、安全分类；dev/test 各 4 项。完整任务列表见 [任务卡](datasets/zzcode-bench-v1/dataset-card.md)，结构与扩展方法见 [数据集说明](datasets/README.md)。

## 评分术语

- **F2P（FAIL_TO_PASS）**：修复前失败、修复后应通过的测试，检查问题是否解决。
- **P2P（PASS_TO_PASS）**：修复前通过、修复后仍应通过的测试，检查是否引入回归。
- **Null Patch**：不修改源码，用于验证问题在 base commit 中确实存在。
- **Gold Patch**：人工参考修复，用于验证任务和评分环境有效；不是唯一允许的答案。
- **FULL**：评分要求全部满足；PARTIAL/NO 表示未完全解决。Agent 返回“完成”不等于 FULL。

数据门禁先运行 Null，再运行 Gold（默认重复 3 次）；通过后才运行真实 Agent。评分由独立环境中的测试决定，不用模型自评代替。

## 流程

固定 base commit → Null/Gold 数据门禁 → 真实 Agent → 收集 Patch → 独立工作区评分 → 报告。数据门禁失败不调用模型。F2P 衡量修复，P2P 检查原有行为；独立评分给出 FULL/PARTIAL/NO 或错误分类，Agent 宣布完成不等于 resolved。

## 数据校验

下面的命令检查任务结构、公开／私有关联和数据锁；不会执行 Null/Gold，也不会调用模型。运行测试的门禁在完整评测流程中执行。

```bash
uv run python -m zzcode.evaluation.cli validate-dataset \
  --public-root evaluation/datasets/zzcode-bench-v1 \
  --private-root evaluation/private --split dev
```

## 运行

构建并准备可信评分镜像，配置模型凭证和私有评分输入后执行：

```bash
export ZZCODE_EVAL_PRIVATE_ROOT="$PWD/evaluation/private"
uv run python scripts/evaluation/run_internal_eval.py \
  --split dev --provider openai --model provider/model-name \
  --base-url https://provider.example/v1
```

使用 `--split test` 评估留出集；用重复的 `--instance-id` 选择 smoke 任务。真实模型调用会产生费用。完整评测要求 Agent 仓库干净，先提交代码再运行。参数示例见 [配置说明](configs/README.md)。

结果写入 `evaluation/runs/<run_id>/`，工作副本写入 `evaluation/workspaces/<run_id>/`，均被忽略。Manifest 固定模型、预算、Git commit、数据摘要与镜像 digest。每项任务保存 Agent 轨迹、Patch、JUnit 和评分结果。已完成且结果保存完整的工作副本可清理；私有评分输入不是生成物。

## 检查与报告

```bash
uv run pytest tests/evaluation -m "not docker and not real_model"
RUN_DOCKER_TESTS=1 uv run pytest tests/evaluation -m docker
uv run python -m zzcode.evaluation.cli render-report evaluation/runs/<run_id>
```

Docker 工具执行与评分均禁网，容器以非 root 运行并限制 CPU、内存、PID 和 tmpfs。Agent 使用可同步副本；评分工作区只读，独立日志挂载可写。

产品与原生协议测试使用确定性 stub 验证运行规则，不计入真实任务成功率。旧预设答案 benchmark 和消融实验已移除，能力评估统一使用真实仓库任务。
