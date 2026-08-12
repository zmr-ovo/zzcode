# zzcode Evaluation

本目录用于存放评测数据、Evaluation Harness 自测、执行环境定义、运行产物和评测报告。可复用的 Python 实现位于 `zzcode/evaluation/`。

Phase 1 已实现评测系统的数据边界，包括：

- 带版本号的公开 `TaskInstance`；
- 与公开任务分开加载的私有评分配置；
- 可复现的数据集摘要（dataset digest）；
- 与 SWE-bench 兼容的 `predictions.jsonl` 读写；
- Schema 校验和私有数据泄漏检查。

Phase 2 已实现运行状态、错误模型和 Artifact Store。Phase 3 已实现本地干净 Workspace、严格 patch 应用、隐藏测试注入、JUnit 解析和 F2P/P2P Grader。

当前 Phase 3 只接收人工提供的 Null、Gold、Partial、Regression 和 Conflict patch，用于证明 Harness 本身正确。它仍不调用真实模型，也不提供 Docker 隔离；真实 zzcode Adapter 和容器执行将在后续 Phase 实现。

## Phase 3 本地执行流程

`LocalGradingHarness` 对一项任务执行以下流程：

```text
TaskInstance + PrivateTestSpec + 人工 model_patch
                    ↓
WorkspaceManager 从 allowlist 仓库重新 clone
                    ↓
checkout --detach 到精确 base_commit
                    ↓
确认 HEAD 正确且工作区干净
                    ↓
SafetyPolicy 检查 model_patch
                    ↓
git apply --check --binary --whitespace=error-all
                    ↓
git apply --binary --whitespace=error-all
                    ↓
注入私有 test.patch
                    ↓
分别运行 F2P 和 P2P，生成 JUnit XML
                    ↓
解析 failed / error / skipped / not-run / collection error
                    ↓
Grader 输出 FULL / PARTIAL / NO 或明确错误
```

Null Patch 是任务认证模式：不应用模型 patch，直接在 `base_commit` 上注入隐藏测试并执行 F2P/P2P。Null 结果用于证明问题真实存在，不生成正式 `EvaluationResult`，也不进入 Pass@1。

## Phase 3 Workspace 规则

- 仓库必须在 `WorkspaceManager` 的 repo allowlist 中；
- Inference 和 Grading 使用不同目录；
- 每个 Workspace 只创建一次，已有目录不能覆盖；
- Workspace 必须处于精确 `base_commit`；
- clone 使用 `--no-hardlinks`，避免修改源仓库对象；
- 模型 patch 应用前工作区必须干净；
- Phase 3 是本地进程隔离，不等同于 Docker 安全沙箱。

## Phase 3 Patch 安全与应用规则

模型 patch 会先经过静态检查：

- 禁止修改 `.git`、`.env`、`.zzcode`、`evaluation`、`private`、`hidden_tests` 和 `tests`；
- 禁止绝对路径、`..` 路径穿越和反斜线路径；
- 禁止 rename/copy、符号链接和默认的 binary patch；
- 限制修改文件数量和增删行数量；
- 空 patch 记录为 `AGENT_ERROR / EMPTY_PATCH`；
- Conflict 或格式错误记录为 `NO / PATCH_APPLY_FAILURE`。

Patch 只使用严格的 `git apply --check` 和 `git apply`，不会使用 fuzz、三方合并或自动修补来替 Agent 修改答案。

私有 `test.patch` 不执行模型 patch 的“禁止修改 tests”策略，因为它本身就是 Harness 信任的评分输入；但它必须能 clean apply，否则任务记为 `DATASET_ERROR`。

## F2P/P2P 执行和评分

F2P 和 P2P 分别调用 pytest，并分别生成：

```text
f2p.xml
f2p.log
f2p.result.json
p2p.xml
p2p.log
p2p.result.json
```

Grader 只读取结构化 `TestGroupResult`：

| 条件 | 结果 |
|---|---|
| Safety 通过，F2P=100%，P2P=100% | `FULL` |
| Safety 通过，0%<F2P<100%，P2P=100% | `PARTIAL` |
| P2P<100%，即使 F2P 全过 | `NO` |
| 测试缺失、collection error、JUnit 缺失或不完整 | `NO / TEST_ERROR` |
| pytest 无法启动等基础设施故障 | `INFRA_ERROR` |
| Safety 违规 | `NO / SAFETY_VIOLATION` |

F2P/P2P selector 必须是 `.py` 文件或 pytest node id，不能以 `-` 开头、包含换行、反斜线或路径穿越，避免把数据集内容注入为 pytest 命令行选项。

## Golden Harness 验收

`evaluation/tests/golden/test_local_harness.py` 会临时创建独立 Git 仓库和私有 test patch，验证：

```text
Null Patch       → NO，F2P=0%，P2P=100%
Gold Patch       → FULL
Partial Patch    → PARTIAL
Regression Patch → NO
Conflict Patch   → PATCH_APPLY_FAILURE
Unsafe Patch     → SAFETY_VIOLATION
Empty Patch      → AGENT_ERROR / EMPTY_PATCH
```

这些 patch 只用于测试 Harness，不是模型能力成绩。

## Phase 2 运行产物目录

一次评测对应一个不可重复的 Run ID，默认写入：

```text
evaluation/runs/<run-id>/
├── run_manifest.json
├── predictions.jsonl
├── instance_results.jsonl
├── results.json
├── run_failure.json
└── instances/
    └── <instance-id>/
        ├── agent_result.json
        ├── patch.diff
        └── report.json
```

各文件的职责：

| 文件 | 内容 |
|---|---|
| `run_manifest.json` | 数据集 digest、split、Agent commit、provider/model、模型参数、资源限制、环境和 Run 生命周期 |
| `predictions.jsonl` | SWE-bench-compatible Prediction，每个任务最多一行 |
| `instance_results.jsonl` | 已完成任务的结构化结果，每个任务最多一行 |
| `results.json` | 后续 Reporting 阶段生成的整次运行汇总 |
| `run_failure.json` | Harness 在数据集、基础设施或中断阶段发生的运行级失败 |
| `agent_result.json` | 单任务 Agent 阶段状态、耗时、token、工具步数和失败信息 |
| `patch.diff` | 单任务产生的模型 patch；Phase 3 前只定义存储接口，不负责生成 |
| `report.json` | 单任务 `EvaluationResult` |

Artifact Store 遵循以下规则：

1. Run ID 由 UTC 时间和随机后缀组成；
2. 创建目录时使用排他模式，已存在的 Run ID 会直接报错；
3. JSON 通过同目录临时文件和原子替换写入；
4. JSONL 写入时加排他锁，一次只追加一条完整记录；
5. Prediction 和单任务结果按 `instance_id` 去重；
6. 已进入 `COMPLETED`、`FAILED` 或 `INTERRUPTED` 的 Run 不允许再次修改；
7. 单任务终态结果不允许覆盖；
8. 中断只追加 `run_failure.json` 并更新 Manifest，已经落盘的任务结果仍然保留。

## 状态和错误模型

Run 生命周期：

```text
CREATED → RUNNING → COMPLETED
                  ├→ FAILED
                  └→ INTERRUPTED
```

任务评分状态与失败原因分开保存：

| 字段 | 作用 |
|---|---|
| `resolved_status` | `NOT_GRADED / FULL / PARTIAL / NO / AGENT_ERROR / INFRA_ERROR / DATASET_ERROR` |
| `failure.category` | 错误责任域：`AGENT_ERROR / INFRA_ERROR / DATASET_ERROR` |
| `failure.failure_type` | 具体错误，例如 `AGENT_TIMEOUT`、`EMPTY_PATCH`、`PATCH_APPLY_FAILURE` |
| `failure.stage` | 失败阶段，例如 `DATASET`、`AGENT`、`PATCH_APPLY`、`TEST_EXECUTION` |
| `failure.retryable` | 外部状态恢复后是否值得重试 |
| `failure.details` | 可序列化的诊断数据，不存密钥或私有测试正文 |

三类错误不能混合统计：

- `DATASET_ERROR`：任务定义、数据泄漏、Gold/Test 数据问题；
- `INFRA_ERROR`：provider 不可用、容器或 Harness 基础设施故障；
- `AGENT_ERROR`：Agent 超时、工具失败、空 patch、非法 patch、安全违规或测试失败。

未知的程序异常不会自动转换成普通 Agent 失败。调用方必须只捕获已知错误，并为其生成明确的 `FailureRecord`。

## 公开数据目录规范

公开数据是 Agent 可以读取的任务信息，目录结构如下：

```text
evaluation/datasets/<dataset-name>/
├── manifest.jsonl
├── dataset-card.md
├── splits/
│   ├── dev.txt
│   └── test.txt
└── instances/<instance-id>/
    ├── problem_statement.md
    └── task.json
```

各文件的职责：

| 文件 | 内容 |
|---|---|
| `manifest.jsonl` | 每行定义一个 Repo Task，包括 `instance_id`、仓库、`base_commit`、问题描述路径和执行环境 ID |
| `dataset-card.md` | 记录数据集来源、任务范围、版本、限制和审核方法 |
| `splits/dev.txt` | 开发阶段使用的任务 ID，每行一个 |
| `splits/test.txt` | 正式测试使用的任务 ID，每行一个 |
| `problem_statement.md` | 提供给 Agent 的问题描述，不包含正确实现和隐藏测试信息 |
| `task.json` | 公开任务元数据，例如任务类型、难度、子系统和资源限制 |

公开目录不得包含以下内容：

- `gold.patch`；
- `test.patch`；
- 隐藏测试名称或测试代码；
- `FAIL_TO_PASS`、`PASS_TO_PASS`、`F2P`、`P2P`；
- Grader 配置或私有评分路径；
- 能直接暴露正确实现的提示。

Dataset Loader 会递归检查公开 metadata。如果发现上述私有字段或常见别名，会拒绝加载数据集。

## 私有评分目录规范

私有评分数据只能由 Evaluation Harness 读取，Agent 和 inference workspace 都不能访问。

通过环境变量 `ZZCODE_EVAL_PRIVATE_ROOT` 指定私有数据根目录。该目录必须与公开数据目录分离，不能是公开目录的父目录或子目录，也不应提交到公开仓库。

推荐结构：

```text
$ZZCODE_EVAL_PRIVATE_ROOT/
└── <dataset-name>/
    └── <instance-id>/
        ├── grading.json
        ├── gold.patch
        └── test.patch
```

也可以让 `ZZCODE_EVAL_PRIVATE_ROOT` 直接指向 `<dataset-name>/`。Dataset Loader 同时支持这两种形式。

各文件的职责：

| 文件 | 内容 |
|---|---|
| `grading.json` | `instance_id`、Gold/Test patch 文件名、`FAIL_TO_PASS` 和 `PASS_TO_PASS` 列表 |
| `gold.patch` | 已知正确的参考修复，仅用于证明任务可解和校验 Grader |
| `test.patch` | 评分阶段注入的隐藏测试，不能进入 Agent 工作区 |

`grading.json` 示例：

```json
{
  "schema_version": 1,
  "instance_id": "ZZCODE-BUG-001",
  "gold_patch": "gold.patch",
  "test_patch": "test.patch",
  "FAIL_TO_PASS": [
    "hidden_tests/test_memory.py::test_stale_summary"
  ],
  "PASS_TO_PASS": [
    "tests/test_memory.py"
  ]
}
```

## 校验公开与私有数据集

运行以下命令校验一个 split：

```bash
python -m zzcode.evaluation.cli validate-dataset \
  --public-root evaluation/datasets/zzcode-bench-v1 \
  --private-root "$ZZCODE_EVAL_PRIVATE_ROOT" \
  --split dev
```

也可以省略 `--private-root`，让程序直接读取 `ZZCODE_EVAL_PRIVATE_ROOT`：

```bash
export ZZCODE_EVAL_PRIVATE_ROOT=/absolute/path/to/private-evaluation-data

python -m zzcode.evaluation.cli validate-dataset \
  --public-root evaluation/datasets/zzcode-bench-v1 \
  --split dev
```

校验成功后会输出：

```json
{
  "dataset_digest": "sha256:...",
  "split": "dev",
  "status": "valid",
  "task_count": 3
}
```

其中 `dataset_digest` 会覆盖当前 split 中的：

- 公开任务定义；
- 公开问题描述和 metadata；
- `FAIL_TO_PASS` 与 `PASS_TO_PASS`；
- `gold.patch` 和 `test.patch` 的内容摘要。

只要这些内容发生变化，digest 就会变化。Digest 只包含私有 patch 的 SHA-256，不会输出 patch 正文。

## 校验 predictions.jsonl

使用以下命令检查模型输出是否和指定数据集一致：

```bash
python -m zzcode.evaluation.cli validate-predictions \
  --public-root evaluation/datasets/zzcode-bench-v1 \
  --private-root "$ZZCODE_EVAL_PRIVATE_ROOT" \
  --split dev \
  --predictions evaluation/runs/<run-id>/predictions.jsonl
```

每行 Prediction 必须严格包含三个字段：

```json
{
  "instance_id": "ZZCODE-BUG-001",
  "model_name_or_path": "provider/model-name",
  "model_patch": "diff --git a/... b/..."
}
```

校验会拒绝：

- 空 patch；
- 缺少必需字段；
- 出现额外字段；
- 重复的 `instance_id`；
- 不属于当前 split 的任务；
- 当前 split 中缺失 Prediction 的任务。

## 本地运行产物

`evaluation/runs/` 和 `evaluation/reports/` 用于保存运行产物和报告，默认被 Git 忽略。正式任务数据、私有评分数据和运行产物不能混放。
