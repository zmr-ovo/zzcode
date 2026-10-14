# 真实仓库评测

`evaluation/` 保存公开任务、配置与 Docker 环境；实现位于 `zzcode/evaluation/`，测试位于 `tests/evaluation/`。

```text
configs/        Agent 与完整评测配置示例
datasets/       公开任务、split、数据锁及题面
environments/   Dockerfile 与依赖锁定
```

隐藏测试、Gold Patch 和评分选择器通过 `evaluation/private/` 或 `ZZCODE_EVAL_PRIVATE_ROOT` 单独提供，不提交、不进入 Agent 上下文。

## 流程

固定 base commit → Null/Gold 数据门禁 → 真实 Agent → 收集 Patch → 独立工作区评分 → 报告。数据门禁失败不调用模型。F2P 衡量修复，P2P 检查原有行为；独立评分给出 FULL/PARTIAL/NO 或错误分类，Agent 宣布完成不等于 resolved。

## 数据校验

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
