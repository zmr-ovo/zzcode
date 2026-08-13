# zzcode Evaluation

本目录用于存放评测数据、Evaluation Harness 自测、执行环境定义、运行产物和评测报告。可复用的 Python 实现位于 `zzcode/evaluation/`。

Phase 1 已实现评测系统的数据边界，包括：

- 带版本号的公开 `TaskInstance`；
- 与公开任务分开加载的私有评分配置；
- 可复现的数据集摘要（dataset digest）；
- 与 SWE-bench 兼容的 `predictions.jsonl` 读写；
- Schema 校验和私有数据泄漏检查。

Phase 2 已实现运行状态、错误模型和 Artifact Store。Phase 3 已实现本地干净 Workspace、严格 patch 应用、隐藏测试注入、JUnit 解析和 F2P/P2P Grader。Phase 4 已实现固定 Docker 评分环境和受限容器执行后端。Phase 5 已接入真实 zzcode Adapter。Phase 6 已加入两项真实历史 Repo Task 和完整纵向切片运行器。

正式 F2P/P2P 使用 Phase 4 Docker 后端；本地 `TestExecutor` 只用于 Harness 自测。

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
- `TestExecutor` 是开发期本地后端，不提供 OS 级隔离。
- `DockerTestExecutor` 是正式评分后端：workspace 只读、artifact 目录可写，容器禁网、根文件系统只读、使用非 root 用户，并限制 CPU、内存、PID 和 `/tmp`。
- Docker runner 只允许 `/workspace` 和 `/artifacts` 两个挂载目标；挂载源还必须位于创建 runner 时提供的 allowlist 中。
- image tag 只用于查找镜像；实际运行结果会记录不可变 `image_digest`。

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

F2P 和 P2P 分别在独立短生命周期容器中调用 pytest，并分别生成：

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

## Phase 4 Docker 评分后端

构建固定评分镜像：

```bash
docker build --pull=false \
  -t zzcode-eval-py313:phase4 \
  evaluation/environments/zzcode-py313
```

运行非 Docker Harness 测试：

```bash
uv run pytest evaluation/tests -m "not docker"
```

运行 Docker Gate（Gold 连续三次、禁网/资源与文件系统策略、timeout 清理）：

```bash
RUN_DOCKER_TESTS=1 uv run pytest evaluation/tests -m docker
```

接入同一套 `LocalGradingHarness`：

```python
from zzcode.evaluation import (
    DockerRunner,
    DockerTestExecutor,
    LocalGradingHarness,
    ResourceLimits,
)

runner = DockerRunner(allowed_mount_roots=(workspace_root, artifact_root))
executor = DockerTestExecutor(
    runner,
    image="zzcode-eval-py313:phase4",
    limits=ResourceLimits(cpus=1.0, memory_mb=1024, pids_limit=128),
)
harness = LocalGradingHarness(workspace_manager, test_executor=executor)
```

`allowed_mount_roots` 只能包含本次运行的 workspace 和 artifact 父目录，不能使用用户主目录或文件系统根目录。private test patch 仍由 Harness 注入宿主机的 grading workspace；随后该 workspace 只读挂载进评分容器，模型推理阶段不能访问它。

## Phase 5 真实 zzcode Agent

Phase 5 的 inference 与 grading 完全分离：

```text
公开 TaskInstance
        ↓
WorkspaceManager.create_inference()
        ↓
真实 Provider worker 调用 ZZCode.ask(problem_statement)
        ↓
read/write/patch 工具限制在 inference workspace
run_shell 进入禁网 Docker tool container
        ↓
PatchCollector 执行 git diff HEAD --binary
        ↓
非空 patch 生成 Prediction
        ↓
独立 grading workspace 和 DockerTestExecutor 只消费 Prediction.model_patch
```

该链路没有 FakeLLM fallback。provider 与 model 必须显式设置；Fake、Stub、Mock 名称会在配置阶段被拒绝。OpenAI/Anthropic 密钥只从环境变量读取，不写入配置、worker request、prompt、日志或 Agent tool container。

单任务真实 inference 命令：

```bash
python -m zzcode.evaluation.cli run-inference \
  --public-root evaluation/datasets/zzcode-bench-v1 \
  --private-root "$ZZCODE_EVAL_PRIVATE_ROOT" \
  --split dev \
  --instance-id ZZCODE-BUG-001 \
  --repo-path /absolute/path/to/zzcode \
  --workspace-root /tmp/zzcode-eval-workspaces \
  --artifact-dir evaluation/runs/manual/instances/ZZCODE-BUG-001 \
  --provider openai \
  --model provider/model-name \
  --base-url https://provider.example/v1
```

输出包括：

- `agent_request.json`：公开 Task 和非敏感配置；
- `agent_response.json`：runtime 状态和用量；
- `runtime/`：已脱敏的 zzcode session、trace、report；
- `agent_worker.stdout.log`、`agent_worker.stderr.log`；
- `agent_result.json`：统一 AgentRunResult；
- `patch.diff` 和 SWE-bench-compatible `predictions.jsonl`：仅在 patch 非空时生成。

状态分类：

| 情况 | Agent 状态 | FailureType |
|---|---|---|
| 正常生成非空 patch | `COMPLETED` | 无 |
| 正常结束但没有代码修改 | `FAILED` | `EMPTY_PATCH` |
| Provider 不可用或请求失败 | `FAILED` | `PROVIDER_UNAVAILABLE` |
| Agent 总时限到达 | `INTERRUPTED` | `AGENT_TIMEOUT` |
| Tool/runtime 异常 | `FAILED` | `TOOL_FAILURE` |
| Worker/patch collector 基础设施异常 | `FAILED` | `INFRASTRUCTURE_ERROR` |

Adapter 不读取 `PrivateTestSpec`，也不运行 F2P/P2P。Grader 只读取 patch，因此不会依赖 Agent 的 history、memory、最终回答或内部状态。

## Phase 6 两项纵向切片任务

公开数据位于 `evaluation/datasets/zzcode-bench-v1/`：

- `ZZCODE-BUG-001`：单文件 OpenAI-compatible reasoning-only 响应缺陷；
- `ZZCODE-BUG-002`：跨 `workspace.py` 和 `tools.py` 的 `.env` 隔离缺陷。

对应 Gold Patch、hidden test patch 与 F2P/P2P 清单位于本地 `evaluation/private/zzcode-bench-v1/`。该目录已被 `.gitignore` 排除；对外发布时应通过私有对象存储或 CI Secret 单独分发，不能复制到公开数据目录。

完整执行顺序：

```text
加载并校验公开任务 + 私有评分数据
                 ↓
Null Validation：base commit + hidden tests
必须 F2P 非全过、P2P 全过
                 ↓
Gold Validation × 3：base commit + gold.patch + hidden tests
三次必须全部 FULL，且记录相同 image digest
                 ↓
从 base commit 创建独立 inference workspace
                 ↓
真实 zzcode Agent 只读取公开题面并修改源码
                 ↓
git diff HEAD --binary → patch.diff → predictions.jsonl
                 ↓
从 base commit 再创建独立 grading workspace
                 ↓
Safety → git apply Agent Patch → 注入 hidden tests
                 ↓
Docker 分别执行 F2P/P2P → FULL/PARTIAL/NO
                 ↓
写入任务 report.json、批次 results.json、run_manifest.json
```

Null 或 Gold 门禁失败时，任务记录为 `DATASET_ERROR`，不会调用真实模型。Agent/provider 超时或失败同样会生成 `agent_result.json` 和 `report.json`，不会缺失任务产物。

运行本地数据集稳定性门禁（不调用模型，容器禁网）：

```bash
RUN_DOCKER_TESTS=1 uv run pytest -q \
  evaluation/tests/integration/test_phase6_dataset_docker.py
```

运行完整真实评测（会将公开题面和必要仓库上下文发送给配置的 Provider，并产生 API 成本）：

```bash
export ZZCODE_EVAL_PRIVATE_ROOT="$PWD/evaluation/private"

uv run python scripts/run_internal_eval.py \
  --provider openai \
  --model provider/model-name \
  --base-url https://provider.example/v1
```

默认输出目录为 `evaluation/runs/<run_id>/`，工作区位于 `evaluation/workspaces/<run_id>/`，二者都被 Git 忽略。主要产物：

```text
run_manifest.json                 固定配置、数据摘要、Git commit、镜像摘要和状态
predictions.jsonl                 仅包含成功生成 Patch 的任务
instance_results.jsonl            每项任务的结构化评分结果
results.json                      批次汇总与逐任务状态
instances/<id>/validation/        Null、Gold×3 的日志、JUnit 和 gate.json
instances/<id>/agent/             Agent request/response、脱敏日志和 runtime 轨迹
instances/<id>/patch.diff         Agent 的标准 Git Patch（如有）
instances/<id>/grading/           Agent Patch 的 F2P/P2P 评分产物
instances/<id>/report.json        单任务最终结果
```

`evaluation/configs/phase6-vertical-slice.example.json` 记录建议的面试演示参数。正式成绩应固定 provider、model、temperature、资源限制、数据摘要、Agent commit 和镜像摘要后再比较。

为避免 `run_manifest.json` 记录的 commit 与实际 Agent 代码不一致，完整运行命令要求 zzcode 仓库没有已修改或未跟踪文件；请先提交 Phase 6 代码，再启动正式评测。

Phase 5 Gate 已使用真实 provider 在临时 Git Repo Task 上执行 smoke：Agent 完成工具调用后生成非空 `Prediction`，同时落盘 steps、latency 和 token usage。该 smoke 只认证 Adapter 链路，不属于正式数据集成绩；正式 Pass@1 从 Phase 6 的 verified tasks 开始。

## Phase 6.5：Coding 执行闭环

Phase 6.5 在 Agent 推理阶段增加自动任务模式、公开测试验证和完成门禁：

```text
普通请求 → 模型语义分类 → General / Coding
Repo Task → Evaluation 强制 Coding
Coding → 修改代码 → verify 公开测试 → 完成门禁 → Prediction → 隐藏 Grader
```

- 普通 CLI 默认为 `--task-mode auto`，由当前真实模型根据完整语义分类，不使用关键词或正则；
- `--task-mode general` 和 `--task-mode coding` 只用于显式覆盖；
- Evaluation Worker 固定使用 Coding，不调用分类模型，保证正式成绩可比较；
- 仓库级验证入口定义在根目录 `zzcode.verify.json`，评测环境使用 `environment.json` 中的 `agent_verification`；
- zzcode 的公开验证范围固定为 `tests/`，不会收集 `evaluation/tests/` 或 `evaluation/private/`；
- Phase 6 历史 base commit 中一个引用未入库旧文档的基线失败由环境 profile 排除；当前版本的 Evaluation 文档检查已迁入 `evaluation/tests/`；
- `run_shell` 不能满足完成门禁，只有无 selector 的完整 `verify(profile="test")` 可以；
- 完整验证必须在最后一次修改之后成功，且验证时和提交时的 patch digest 必须一致；
- 门禁失败但 patch 非空时仍生成 Prediction 并交给隐藏 Grader，但 Agent 状态记录为失败。

三类测试入口必须分开运行：

```bash
# Agent 修改过程中的公开产品测试
python -m pytest -q tests

# Evaluation Harness 自身测试
python -m pytest -q evaluation/tests

# 正式 Repo Task：Null、Gold、真实 Agent 和隐藏 F2P/P2P
python scripts/run_internal_eval.py --help
```

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
