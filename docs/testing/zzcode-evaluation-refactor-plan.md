# zzcode 测试与 Evaluation 架构重构实施方案

> 文档状态：实施指导稿  
> 目标：构建一套近期可落地、可复现、可用于面试和简历展示的 SWE-bench-compatible 执行式评测系统，并在新体系稳定后整合现有测试和 benchmark。  
> 第一版边界：使用真实模型；不实现 Evaluator–Optimizer；不根据隐藏测试结果 Repair；不使用 LLM Judge 判定代码正确性。

---

## 1. 背景、目标与边界

### 1.1 为什么需要重构

zzcode 当前已经有产品测试、确定性 benchmark、指标实验和运行产物，但不同内容的职责不够清楚：

- pytest 测试验证 runtime、memory、tools、CLI 等产品实现；
- evaluator 和 metrics 测试验证运行产物和实验逻辑；
- 现有 benchmark 使用 `FakeModelClient` 和预设输出验证固定执行路径；
- context、memory、resume 实验用于观察模块机制。

这些内容都有价值，但不能用同一个“通过率”解释：

```text
产品测试通过
≠ Agent 能解决未知仓库任务

预设模型输出的 benchmark 通过
≠ 真实模型任务解决率为 100%

Context/Memory 消融结果
≠ SWE-bench 式 Pass@1
```

### 1.2 最终三层架构

```text
L1 Product Tests
验证 zzcode 产品代码和安全合同
unit + integration + security + diagnostics
                ↓
L2 Evaluation Harness Tests
验证任务数据、workspace、patch、Docker、测试解析和评分逻辑
unit + integration + golden + real-model smoke
                ↓
L3 Executable Agent Benchmarks
使用真实模型测量仓库级任务解决能力
internal zzcode-bench + external SWE-bench Verified pilot
```

### 1.3 最终交付能力

完成后应当能够：

1. 解释每一类测试在证明什么；
2. 使用真实模型运行自建 Repo Task；
3. 让 Agent 只提交标准 `git diff` patch；
4. 在独立、干净的评分环境中应用 patch；
5. 使用 FAIL_TO_PASS 和 PASS_TO_PASS 确定性评分；
6. 对每个任务执行 Null、Gold、Agent 三类验证；
7. 保存模型配置、轨迹、patch、测试日志和指标；
8. 输出 SWE-bench-compatible `predictions.jsonl`；
9. 使用同一个 zzcode Agent Adapter 接入官方 SWE-bench；
10. 将现有测试和 12 个旧 benchmark 全部迁移到职责明确的位置。

### 1.4 第一版明确不做

- Evaluator–Optimizer 或隐藏测试反馈 Repair；
- LLM Judge 正确性评分；
- 多语言通用评测平台；
- Kubernetes、分布式任务队列或 Web Dashboard；
- 全量 SWE-bench Verified 500 题；
- 把所有指标加权成一个综合分。

“完整”指 Task、Inference、Patch、Execution、Grading、Artifacts 和 Reporting 全链路完整，不是功能越多越好。

---

## 2. 核心概念

### 2.1 Repo Task

Repo Task 是一道完整仓库级工程题，而不是单函数题。它至少包含：

```text
修复前的仓库版本 base_commit
+
给 Agent 看的 problem_statement
+
Agent 看不到的 test.patch / hidden tests
+
FAIL_TO_PASS 和 PASS_TO_PASS
```

例如：

```text
instance_id: ZZCODE-BUG-001
base_commit: abc123
problem_statement:
  外部文件发生变化后，memory 中保存的旧文件摘要仍然被使用。
```

### 2.2 Prediction、patch.diff 与 predictions.jsonl

Agent 修改代码后，系统执行：

```bash
git diff --binary --no-ext-diff
```

得到的代码差异保存为 `patch.diff`。它是单个任务的正式答案。

Prediction 是对 patch 的标准包装：

```json
{
  "instance_id": "ZZCODE-BUG-001",
  "model_name_or_path": "provider/model-name",
  "model_patch": "diff --git a/... b/..."
}
```

`predictions.jsonl` 每行保存一个任务的 Prediction：

```jsonl
{"instance_id":"ZZCODE-BUG-001","model_name_or_path":"provider/model-name","model_patch":"diff --git ..."}
{"instance_id":"ZZCODE-BUG-002","model_name_or_path":"provider/model-name","model_patch":"diff --git ..."}
```

轨迹、token 和 latency 不写入 `model_patch`，而保存在运行产物中。

### 2.3 Gold Patch 与 Test Patch

`gold.patch` 是已知正确的参考修复，用于证明任务可解、grader 能接受正确实现。Agent 不需要生成相同代码，也不能看到 Gold Patch。

`test.patch` 是针对任务新增或修改的评分测试。它只在评分阶段注入，不能出现在 Agent 的推理环境中。

### 2.4 FAIL_TO_PASS 与 PASS_TO_PASS

```text
FAIL_TO_PASS（F2P）
修复前失败、正确修复后通过
回答：新问题是否真正被修好？

PASS_TO_PASS（P2P）
修复前通过、修复后仍应通过
回答：旧功能是否被破坏？
```

只检查 F2P 可能让“删除功能”式修复误通过，因此必须同时检查 P2P。

### 2.5 Null、Gold、Agent Validation

每个自建任务必须经过：

```text
Null Patch
证明 base commit 上问题确实存在

Gold Patch
证明任务可解且测试可以认可正确实现

Agent Patch
正式测量真实模型驱动的 Agent 能力
```

### 2.6 Evaluation Harness 与 Grader

二者不是同一个概念：

```text
Evaluation Harness = 整个考试系统
Grader             = 最后的阅卷规则
```

Harness 包含：

```text
Dataset Loader
Workspace Manager
Agent Adapter
Patch Collector / Applier
Docker Runner
Test Executor
Log Parser
Grader
Artifact Store
Reporter
```

Grader 只消费结构化测试和安全结果，输出 `FULL / PARTIAL / NO`。

### 2.7 Agent Failure 与 Infrastructure Error

```text
Agent Failure
Agent 超时、空 patch、非法 patch、patch 冲突、测试失败

Infrastructure Error
Docker 构建失败、Harness 崩溃、日志解析器自身异常

Dataset Error
任务定义错误、Gold 不能通过、测试不稳定
```

三者必须分开统计。基础设施错误不能悄悄算成模型失败。

---

## 3. 目标目录结构

```text
zzcode/
├── zzcode/                              # 产品代码
│   └── evaluation/                      # Evaluation Python 实现
│       ├── __init__.py
│       ├── schema.py                    # 共享数据模型
│       ├── dataset.py                   # 数据集加载与 public/private 隔离
│       ├── prediction.py                # predictions.jsonl 读写
│       ├── errors.py                    # 错误类型
│       │
│       ├── inference/
│       │   ├── __init__.py
│       │   ├── adapter.py               # 通用 Agent 接口
│       │   ├── zzcode_adapter.py        # 真实模型 zzcode 适配
│       │   └── patch_collector.py       # git diff 收集
│       │
│       ├── execution/
│       │   ├── __init__.py
│       │   ├── runner.py                # 全流程编排
│       │   ├── workspace.py             # inference/grading workspace
│       │   ├── docker_runner.py         # 容器生命周期
│       │   ├── patch_applier.py         # patch 检查与应用
│       │   ├── test_executor.py         # hidden test 注入和 pytest 执行
│       │   └── log_parser.py            # JUnit XML 解析
│       │
│       ├── grading/
│       │   ├── __init__.py
│       │   ├── grader.py                # F2P/P2P 判分
│       │   ├── safety.py                # patch 与运行安全检查
│       │   └── status.py                # 统一状态枚举
│       │
│       ├── reporting/
│       │   ├── __init__.py
│       │   ├── artifacts.py             # 运行产物持久化
│       │   ├── aggregate.py             # Pass@1 等聚合
│       │   └── markdown.py              # Markdown 报告
│       │
│       └── adapters/
│           ├── __init__.py
│           └── swebench.py              # 官方 SWE-bench 兼容层
│
├── tests/                               # L1：产品测试
│   ├── README.md
│   ├── conftest.py
│   ├── factories.py
│   ├── unit/
│   ├── integration/
│   ├── security/
│   └── diagnostics/
│
├── evaluation/                          # 数据、环境、Harness 测试和结果
│   ├── README.md
│   ├── configs/
│   │   ├── benchmark-v1.yaml
│   │   ├── agent-zzcode.yaml
│   │   └── release-gates.yaml
│   ├── datasets/
│   │   └── zzcode-bench-v1/
│   │       ├── manifest.jsonl
│   │       ├── dataset-card.md
│   │       ├── splits/
│   │       │   ├── dev.txt
│   │       │   └── test.txt
│   │       └── instances/               # 只包含 Agent 可见数据
│   ├── environments/
│   │   └── zzcode-py313/
│   │       ├── Dockerfile
│   │       ├── setup.sh
│   │       └── evaluate.sh
│   ├── tests/                           # L2：Harness 自测
│   │   ├── unit/
│   │   ├── integration/
│   │   └── golden/
│   ├── runs/                            # 默认 gitignore
│   └── reports/
│
└── scripts/
    ├── validate_eval_dataset.py
    ├── run_internal_eval.py
    ├── run_swebench_inference.py
    ├── import_swebench_results.py
    └── render_eval_report.py
```

私有评分数据不放在 Agent 要解决的 zzcode 仓库树中。推荐通过 `ZZCODE_EVAL_PRIVATE_ROOT` 指向独立目录：

```text
<private-root>/zzcode-bench-v1/
└── ZZCODE-BUG-001/
    ├── grading.json
    ├── gold.patch
    └── test.patch
```

正式运行时只有 Harness 进程能读取该目录；Inference workspace/container 不挂载它。若为了本地开发暂存于同一父目录，也必须通过 mount allowlist 保证 Agent 工具不可见，且不得发布到公开仓库。

边界原则：

- `zzcode/evaluation/`：可复用的 Python 实现；
- `evaluation/`：任务数据、Docker 环境、测试、运行结果；
- `scripts/`：用户执行的薄入口；
- `tests/`：zzcode 产品自身测试，不存 Agent 能力成绩。

---

## 4. 一项 Repo Task 的完整运行流程

`scripts/run_internal_eval.py` 启动一次评测，`execution/runner.py` 按顺序处理数据集中的每个 Repo Task。下面以 `ZZCODE-BUG-001` 为例说明单个任务的执行过程。

### 4.1 开始前有哪些数据

运行一个任务需要三组输入：

| 输入 | 包含内容 | 谁可以读取 |
|---|---|---|
| Public Task | `instance_id`、`repo`、`base_commit`、`problem_statement`、资源限制 | Harness 和 Agent |
| Private Grading Data | `gold.patch`、`test.patch`、F2P、P2P | 仅 Harness |
| Run Config | provider、model、step/token/timeout、Docker 环境 | Harness；Agent 只获得必要配置 |

其中：

- `base_commit` 指定任务开始时的代码版本；
- `problem_statement` 是 Agent 要解决的问题；
- `test.patch` 包含 Agent 看不到的评分测试；
- `gold.patch` 只用于任务认证，不用于正式 Agent 推理；
- F2P/P2P 指定评分时要检查哪些测试。

### 4.2 第一步：读取并校验任务

入口脚本读取配置，然后 Dataset Loader 加载 public task 和 private grading data。

```text
scripts/run_internal_eval.py
        ↓
zzcode/evaluation/dataset.py
        ↓
TaskInstance + PrivateTestSpec
```

这一阶段检查：

- task schema 是否正确；
- `instance_id` 是否唯一；
- public 和 private 的 `instance_id` 是否一致；
- `base_commit`、F2P、P2P 是否存在；
- 当前任务是否属于本次运行的 split。

传给 Agent 的数据只包含 Public Task。`gold.patch`、`test.patch`、F2P 和 P2P 不进入 Agent 输入。

负责文件：`scripts/run_internal_eval.py`、`schema.py`、`dataset.py`。  
输出：`TaskInstance`、`PrivateTestSpec`。

### 4.3 第二步：创建运行记录和推理工作区

Harness 先创建本次 run 的目录和 `run_manifest.json`，再为任务创建 inference workspace：

```text
repo
  ↓ checkout
base_commit
  ↓
clean inference workspace
```

Inference workspace 是 zzcode 实际工作的目录。它包含修复前源码和已有公开测试，但不包含 private grading data。

`run_manifest.json` 记录 dataset digest、Agent commit、provider/model、预算和环境版本，使本次运行可以复现。

负责文件：`reporting/artifacts.py`、`execution/workspace.py`。  
输出：`run_manifest.json`、inference workspace。

### 4.4 第三步：真实模型驱动 zzcode 修改仓库

ZZCode Agent Adapter 在 inference workspace 中启动现有 zzcode：

```text
problem_statement
        ↓
真实模型驱动 zzcode
        ↓
搜索和读取代码
        ↓
运行已有测试
        ↓
修改 memory.py 等仓库文件
```

Agent 可以使用产品提供的文件、搜索、shell 和 patch 工具，但不能访问 private root。运行结束时保存过程信息，例如工具调用、token、latency 和最终回答。

负责文件：`inference/zzcode_adapter.py`，并调用现有 `zzcode/cli.py`、`zzcode/runtime.py`。  
输出：修改后的 inference workspace、`agent.log`、`trajectory.jsonl`、`AgentRunResult`。

### 4.5 第四步：提取 Agent 的最终代码修改

Agent 结束后，Patch Collector 在 inference workspace 中执行：

```bash
git diff --binary --no-ext-diff
```

生成单任务答案：

```text
patch.diff
```

随后将 patch 写成标准 Prediction，并追加到：

```text
predictions.jsonl
```

这样评分阶段只接收 Agent 明确产生的代码差异，不直接使用 Agent 工作过的整个目录，也不根据最终自然语言回答评分。

负责文件：`inference/patch_collector.py`、`prediction.py`。  
输出：`patch.diff`、一条 Prediction、`predictions.jsonl`。

### 4.6 第五步：创建独立的评分环境并应用 patch

Harness 重新从同一个 `base_commit` 创建 grading workspace/container：

```text
base_commit
    ├── inference workspace：Agent 已经工作过
    └── grading workspace：重新创建，保持干净
```

评分环境不会复制 inference workspace，而是只接收 `model_patch`。

应用前先检查：

- patch 格式是否合法；
- 是否修改 `.git`、`.env`、private 或 hidden test 路径；
- 是否路径逃逸；
- 是否删除已有测试；
- 是否超过允许的文件或 diff 范围。

检查通过后执行：

```bash
git apply --check model.patch
git apply model.patch
```

负责文件：`execution/workspace.py`、`execution/docker_runner.py`、`grading/safety.py`、`execution/patch_applier.py`。  
输出：带有 Agent 修改的 grading workspace、`patch_apply.log`。

### 4.7 第六步：注入隐藏测试并运行 F2P/P2P

Agent patch 应用成功后，Harness 才把 private `test.patch` 注入 grading workspace。

然后 Test Executor 分别运行：

```text
F2P tests
检查任务问题是否被修复

P2P tests
检查已有功能是否发生回归
```

pytest 同时生成原始日志和 JUnit XML。Log Parser 再把 XML 转换成结构化结果：

```json
{
  "fail_to_pass": {"passed": 1, "total": 1},
  "pass_to_pass": {"passed": 18, "total": 18},
  "tests_completed": true
}
```

负责文件：`execution/test_executor.py`、`execution/log_parser.py`。  
输出：`test_output.log`、`f2p.xml`、`p2p.xml`、`TestGroupResult`。

### 4.8 第七步：生成单任务评分

Grader 根据结构化测试结果和安全结果判定：

```text
F2P=100% 且 P2P=100% 且 Safety=PASS
→ FULL

0%<F2P<100% 且 P2P=100% 且 Safety=PASS
→ PARTIAL

其他情况
→ NO
```

例如：

```text
F2P 1/1，P2P 18/18，Safety PASS
→ ZZCODE-BUG-001 = FULL

F2P 1/1，P2P 17/18
→ 新问题修复但引入回归，因此为 NO
```

负责文件：`grading/grader.py`、`grading/status.py`。  
输出：单任务 `EvaluationResult`、`instances/ZZCODE-BUG-001/report.json`。

### 4.9 第八步：保存并汇总整次评测

Artifact Store 保存单任务的输入、过程和结果。所有任务完成后，Reporter 聚合：

- Pass@1；
- F2P Pass Rate；
- P2P Preservation Rate；
- Patch Apply Rate；
- Agent/Test Completion Rate；
- tokens、latency、tool steps；
- Agent Error、Infrastructure Error 和 Safety Violation。

负责文件：`reporting/artifacts.py`、`reporting/aggregate.py`、`reporting/markdown.py`。  
输出：`instance_results.jsonl`、`results.json`、Markdown 报告。

### 4.10 完整流程总览

```text
读取 Task 和运行配置
        ↓
校验 public/private 数据，但只把 public task 传给 Agent
        ↓
从 base_commit 创建 inference workspace
        ↓
真实模型驱动 zzcode 修改仓库
        ↓
git diff 生成 patch.diff
        ↓
写入 predictions.jsonl
        ↓
从同一 base_commit 创建全新 grading container
        ↓
检查并应用 model_patch
        ↓
注入 private test.patch
        ↓
运行 F2P 和 P2P，解析 JUnit XML
        ↓
Grader 输出 FULL / PARTIAL / NO
        ↓
保存单任务结果并聚合整个数据集 Pass@1
```

### 4.11 失败发生在哪一步

| 阶段 | 典型失败 | 记录结果 |
|---|---|---|
| 加载任务 | schema 错误、Gold 不通过、测试不稳定 | `DATASET_ERROR`，阻止正式运行 |
| 启动 Agent | provider/config 不可用 | `INFRA_ERROR` |
| Agent 执行 | 超时 | `AGENT_ERROR / TIMEOUT` |
| 收集 patch | 没有代码修改 | `AGENT_ERROR / EMPTY_PATCH` |
| 应用 patch | 格式错误、上下文冲突 | `NO / PATCH_APPLY_FAILURE` |
| 安全检查 | 修改受保护路径 | `NO / SAFETY_VIOLATION` |
| 执行测试 | Docker/Harness 自身故障 | `INFRA_ERROR` |
| 执行测试 | 测试超时或 collection error | `NO / TEST_ERROR` |
| Grading | F2P/P2P 未满足 | `PARTIAL` 或 `NO` |

---

## 5. 文件职责与最小接口

本节是实现时的代码导航。每项职责只在这里定义一次。

### 5.1 Schema、Dataset 与 Prediction

#### `zzcode/evaluation/schema.py`

只保存数据模型，不执行 Git、Docker 或模型调用：

```python
@dataclass(frozen=True)
class TaskInstance:
    instance_id: str
    repo: str
    base_commit: str
    problem_statement: str
    environment_id: str
    metadata: dict[str, object]


@dataclass(frozen=True)
class PrivateTestSpec:
    instance_id: str
    gold_patch_path: Path
    test_patch_path: Path
    fail_to_pass: tuple[str, ...]
    pass_to_pass: tuple[str, ...]


@dataclass(frozen=True)
class Prediction:
    instance_id: str
    model_name_or_path: str
    model_patch: str


@dataclass(frozen=True)
class EvaluationResult:
    instance_id: str
    resolved_status: str
    patch_applied: bool
    tests_completed: bool
    fail_to_pass_rate: float
    pass_to_pass_rate: float
    failure_type: str | None
```

#### `zzcode/evaluation/dataset.py`

负责加载、校验和隔离数据：

```python
class EvaluationDataset:
    @classmethod
    def load(
        cls,
        public_root: Path,
        private_root: Path,
        split: str,
    ) -> "EvaluationDataset": ...

    def tasks(self) -> list[TaskInstance]: ...
    def private_spec(self, instance_id: str) -> PrivateTestSpec: ...
    def inference_payload(self, instance_id: str) -> dict: ...
    def digest(self) -> str: ...
```

硬性规则：`inference_payload()` 不得包含 Gold、Test Patch、F2P 或 P2P。

实际构造时应分别传入 `public_root` 与 `private_root`，不要让 Loader 依赖“private 恰好是 public 的子目录”：

```python
EvaluationDataset.load(
    public_root=Path("evaluation/datasets/zzcode-bench-v1"),
    private_root=Path(os.environ["ZZCODE_EVAL_PRIVATE_ROOT"]),
    split="test",
)
```

#### `zzcode/evaluation/prediction.py`

负责 JSONL，不负责评分：

```python
def append_prediction(path: Path, prediction: Prediction) -> None: ...
def load_predictions(path: Path) -> dict[str, Prediction]: ...
def validate_predictions(tasks, predictions) -> None: ...
```

必须验证重复 instance、未知 instance、缺字段和空 patch。

### 5.2 Inference

#### `inference/adapter.py`

定义可替换 Agent 接口：

```python
class AgentAdapter(Protocol):
    def run(
        self,
        task: TaskInstance,
        workspace: Path,
        config: AgentRunConfig,
    ) -> AgentRunResult: ...
```

#### `inference/zzcode_adapter.py`

连接现有 `zzcode/cli.py` 和 `zzcode/runtime.py`：

```python
class ZZCodeAgentAdapter:
    def run(self, task, workspace, config):
        agent = build_real_agent(
            workspace=workspace,
            provider=config.provider,
            model=config.model,
            max_steps=config.max_steps,
        )
        answer = agent.ask(task.problem_statement)
        return AgentRunResult.from_agent(agent, answer)
```

正式 benchmark 必须显式配置真实 provider；不得回退到 Fake LLM。

#### `inference/patch_collector.py`

```python
def collect_patch(workspace: Path) -> str:
    return run_checked(
        ["git", "diff", "--binary", "--no-ext-diff"],
        cwd=workspace,
    ).stdout
```

它只提取 patch，不运行隐藏测试。

### 5.3 Execution Harness

#### `execution/runner.py`

主编排器按顺序调用所有模块：

```python
class EvaluationRunner:
    def run(self, dataset, agent_adapter, run_config):
        run = self.artifacts.start_run(...)
        for task in dataset.tasks():
            result = self.run_instance(
                task,
                dataset.private_spec(task.instance_id),
                run,
            )
            self.artifacts.write_instance_result(result)
        return self.reporter.finalize(run)
```

Runner 应将已知阶段失败转成明确状态，但不能把自身编程错误静默转成普通模型失败。

#### `execution/workspace.py`

```python
class WorkspaceManager:
    def create_inference(self, task: TaskInstance) -> Path: ...
    def create_grading(self, task: TaskInstance) -> Path: ...
    def checkout_base(self, workspace: Path, base_commit: str) -> None: ...
    def assert_clean_base(self, workspace: Path, base_commit: str) -> None: ...
```

Inference 和 Grading 必须返回不同目录。

#### `execution/docker_runner.py`

```python
class DockerRunner:
    def create(self, environment_id, limits) -> ContainerHandle: ...
    def exec(self, container, command, timeout) -> CommandResult: ...
    def copy_in(self, container, source, destination) -> None: ...
    def cleanup(self, container) -> None: ...
```

只封装容器、资源限制、网络和清理，不判断 F2P/P2P。

#### `execution/patch_applier.py`

```python
class PatchApplier:
    def check(self, workspace: Path, patch: str) -> PatchApplyResult: ...
    def apply(self, workspace: Path, patch: str) -> PatchApplyResult: ...
```

第一版只使用严格 `git apply --check` 和 `git apply`，不使用高 fuzz 替 Agent 修补 patch。

#### `execution/test_executor.py`

```python
class TestExecutor:
    def inject_test_patch(self, workspace, path) -> None: ...
    def run_fail_to_pass(self, workspace, test_ids, timeout) -> TestRun: ...
    def run_pass_to_pass(self, workspace, test_ids, timeout) -> TestRun: ...
```

负责调用 pytest 并生成 JUnit XML，不决定 FULL/PARTIAL/NO。

#### `execution/log_parser.py`

```python
def parse_junit(path: Path) -> list[TestCaseResult]: ...
def reconcile_expected_tests(expected_ids, observed) -> TestGroupResult: ...
```

必须区分 failed、error、skipped、not-run、collection error 和 timeout。

### 5.4 Grading

#### `grading/safety.py`

```python
def inspect_patch(patch: str, policy: SafetyPolicy) -> SafetyResult: ...
```

第一版检查：

- `.git`、`.env`、private、hidden tests 等受保护路径；
- 路径逃逸和符号链接；
- 删除或弱化已有测试；
- 超出允许范围的文件和 diff LOC；
- 网络与资源限制违规。

#### `grading/status.py`

```python
class ResolvedStatus(StrEnum):
    FULL = "FULL"
    PARTIAL = "PARTIAL"
    NO = "NO"
    AGENT_ERROR = "AGENT_ERROR"
    INFRA_ERROR = "INFRA_ERROR"
```

#### `grading/grader.py`

```python
def grade(task, f2p, p2p, safety) -> EvaluationResult:
    if not safety.passed:
        return unresolved(task, "SAFETY_VIOLATION")
    if f2p.rate == 1.0 and p2p.rate == 1.0:
        return resolved(task, ResolvedStatus.FULL)
    if 0.0 < f2p.rate < 1.0 and p2p.rate == 1.0:
        return resolved(task, ResolvedStatus.PARTIAL)
    return unresolved(task, "TEST_FAILURE")
```

### 5.5 Artifacts 与 Reporting

#### `reporting/artifacts.py`

负责原子保存：

```python
class ArtifactStore:
    def start_run(self, manifest: RunManifest) -> RunPaths: ...
    def write_agent_result(self, result: AgentRunResult) -> None: ...
    def append_prediction(self, prediction: Prediction) -> None: ...
    def write_instance_result(self, result: EvaluationResult) -> None: ...
```

#### `reporting/aggregate.py`

```python
def aggregate(results: list[EvaluationResult]) -> BenchmarkSummary:
    """计算 Pass@1、F2P/P2P、漏斗、效率和错误数量。"""
```

#### `reporting/markdown.py`

```python
def render_report(manifest, summary, results) -> str: ...
```

报告包含配置、数据分布、主指标、分类结果、漏斗、效率、失败类型、安全和限制。

### 5.6 SWE-bench Adapter

#### `adapters/swebench.py`

```python
def from_swebench_instance(row: dict) -> TaskInstance: ...
def to_swebench_prediction(prediction: Prediction) -> dict: ...
def import_swebench_report(path: Path) -> list[EvaluationResult]: ...
```

它只做字段转换。官方 Docker、repo-specific test spec 和评分仍由官方 SWE-bench Harness 执行。

### 5.7 文件边界速查

| 问题 | 文件 |
|---|---|
| 数据对象有哪些字段？ | `schema.py` |
| 如何读取任务并隔离私有数据？ | `dataset.py` |
| 如何运行真实 zzcode？ | `zzcode_adapter.py` |
| 如何得到 `git diff`？ | `patch_collector.py` |
| 如何写 `predictions.jsonl`？ | `prediction.py` |
| 整个顺序由谁控制？ | `runner.py` |
| 如何创建 base workspace？ | `workspace.py` |
| 如何启动/清理 Docker？ | `docker_runner.py` |
| patch 是否越权？ | `safety.py` |
| 如何应用 patch？ | `patch_applier.py` |
| 如何运行 pytest？ | `test_executor.py` |
| 如何解释 JUnit XML？ | `log_parser.py` |
| 什么时候是 FULL？ | `grader.py` |
| 如何保存结果？ | `artifacts.py` |
| Pass@1 怎么算？ | `aggregate.py` |
| 报告怎么排版？ | `markdown.py` |
| 官方 SWE-bench 怎么接？ | `adapters/swebench.py` |

---

## 6. 顶层数据、配置与脚本

### 6.1 配置文件

#### `evaluation/configs/benchmark-v1.yaml`

```yaml
dataset: evaluation/datasets/zzcode-bench-v1
split: test
max_workers: 1
run_root: evaluation/runs
report_root: evaluation/reports
```

#### `evaluation/configs/agent-zzcode.yaml`

```yaml
agent: zzcode
provider: openai
model: provider/model-name
temperature: 0.0
max_steps: 30
max_new_tokens: 8192
timeout_seconds: 900
provider_network: enabled
tool_network: disabled
grading_network: disabled
```

真实模型 API 调用通常需要 Provider 控制面联网，但 Agent 执行的 shell/tool sandbox 和评分容器默认禁网：

```text
Provider control plane
zzcode 进程调用 OpenAI/Anthropic/Ollama endpoint
→ 按 provider 配置允许访问

Agent tool plane
run_shell、仓库进程和工具执行环境
→ 默认禁止外网

Grading plane
应用 patch 和运行测试的容器
→ 默认禁止外网
```

密钥只从环境变量读取，不进入 YAML、Agent 工具环境、prompt、trace 或报告。

#### `evaluation/configs/release-gates.yaml`

```yaml
min_pass_at_1: 0.30
min_patch_apply_rate: 0.90
min_p2p_preservation_rate: 0.95
max_safety_violation_rate: 0.0
max_infrastructure_errors: 0
```

第一版可以只报告 Gate，不自动阻断产品发布。

### 6.2 单任务文件

#### Public manifest

`evaluation/datasets/zzcode-bench-v1/manifest.jsonl`：

```jsonl
{"instance_id":"ZZCODE-BUG-001","repo":"local/zzcode","base_commit":"abc123","problem_statement_path":"instances/ZZCODE-BUG-001/problem_statement.md","environment_id":"zzcode-py313-v1"}
```

#### Public task metadata

`instances/ZZCODE-BUG-001/task.json`：

```json
{
  "instance_id": "ZZCODE-BUG-001",
  "task_type": "bug_fix",
  "scope": "multi_file",
  "subsystems": ["memory", "runtime"],
  "difficulty": "medium",
  "resource_limits": {
    "timeout_seconds": 900,
    "max_tool_steps": 30
  }
}
```

#### Public problem statement

`instances/ZZCODE-BUG-001/problem_statement.md`：

```markdown
# File summary remains stale after external edits

When a previously summarized file is changed outside the agent, a resumed
session may continue to use the old summary. Update zzcode so stale summaries
are not reused while preserving valid summaries for unchanged files.
```

不得写测试名、Gold 实现或隐藏边界答案。

#### Private grading data

`$ZZCODE_EVAL_PRIVATE_ROOT/zzcode-bench-v1/ZZCODE-BUG-001/grading.json`：

```json
{
  "instance_id": "ZZCODE-BUG-001",
  "gold_patch": "gold.patch",
  "test_patch": "test.patch",
  "FAIL_TO_PASS": [
    "hidden_tests/test_file_freshness.py::test_external_edit_invalidates_summary"
  ],
  "PASS_TO_PASS": [
    "tests/test_memory.py",
    "tests/test_context_manager.py"
  ]
}
```

### 6.3 Docker 环境文件

| 文件 | 内容 |
|---|---|
| `Dockerfile` | 固定 OS、Python、Git 和测试工具版本；不包含密钥或私有数据 |
| `setup.sh` | 安装锁定依赖，验证仓库可导入；镜像构建时运行 |
| `evaluate.sh` | 执行测试、生成 JUnit XML、保留退出码；不负责评分 |

第一版只支持一个固定 zzcode Python 环境。

### 6.4 顶层脚本

| 脚本 | 用途 | 主要调用 | 输出 |
|---|---|---|---|
| `validate_eval_dataset.py` | 新增/修改任务后认证 | Dataset、Null/Gold Validator | 数据集认证报告 |
| `run_internal_eval.py` | 运行自建真实模型评测 | Runner、ZZCode Adapter | internal artifacts |
| `run_swebench_inference.py` | 让 zzcode 解官方任务 | SWE-bench、ZZCode Adapter | 官方 predictions |
| `import_swebench_results.py` | 导入官方 Harness 结果 | SWE-bench importer | 统一外部结果 |
| `render_eval_report.py` | 从已有结果重绘报告 | Aggregate、Markdown | Markdown report |

主命令：

```bash
python scripts/run_internal_eval.py \
  --benchmark-config evaluation/configs/benchmark-v1.yaml \
  --agent-config evaluation/configs/agent-zzcode.yaml
```

调用关系：

```text
run_internal_eval.py
→ load configs
→ EvaluationDataset.load(...)
→ ZZCodeAgentAdapter(...)
→ EvaluationRunner.run(...)
→ ArtifactStore + Aggregate + Markdown Report
```

---

## 7. 自建 zzcode-bench 数据集

### 7.1 第一版规模

先完成 2 个 Vertical Slice Task 跑通链路，再扩展到 8 个 Verified Task：

| 类型 | 最终数量 | 说明 |
|---|---:|---|
| Bug Fix | 4 | 真实异常、边界条件、状态错误 |
| Feature/API | 2 | 新增可执行验证的行为 |
| Multi-file Integration | 1 | 跨模块接口与持久化 |
| Refactor/Compatibility | 1 | 行为保持或兼容升级 |

交叉覆盖要求：

- 至少 3 个需要定位未知文件；
- 至少 3 个包含非显然边界条件；
- 至少 2 个跨文件；
- 至少 2 个涉及状态或持久化；
- 至少 1 个涉及 provider/parser；
- 至少 1 个涉及 CLI/config。

简单 README 或字符串替换只作为 smoke，不计入正式 Pass@1。

### 7.2 任务来源优先级

1. 真实历史 Issue + PR/commit；
2. 真实发现但尚未公开的 bug；
3. 基于真实需求构造的 feature；
4. 人工 mutation，仅补足边界覆盖。

### 7.3 任务进入 test split 的门禁

- [ ] base commit 可以获取并精确 checkout；
- [ ] 环境可以离线构建；
- [ ] Null Patch 至少一个 F2P 失败；
- [ ] Null Patch 的 P2P 全部通过；
- [ ] Gold Patch 可以 clean apply；
- [ ] Gold Patch 的 F2P/P2P 全部通过；
- [ ] Gold 连续运行三次结果一致；
- [ ] hidden tests 不绑定 Gold 的具体代码结构；
- [ ] problem statement 不泄漏答案；
- [ ] inference workspace 不包含 private 数据；
- [ ] 人工审核认为任务清晰、可解；
- [ ] 记录来源、license、创建时间和已知限制。

### 7.4 Dataset Card

`dataset-card.md` 记录：

- 数据来源、版本和 digest；
- 任务类型、模块和难度分布；
- dev/test split 原则；
- hidden tests 是否曾公开；
- 数据污染风险；
- 已知限制和版本变更。

---

## 8. Fake LLM 与真实模型政策

### 8.1 正式能力评测必须使用真实模型

以下流程禁止 Fake LLM：

- internal zzcode-bench 正式运行；
- internal real-model smoke；
- official SWE-bench inference；
- 对外报告中的 Pass@1；
- 简历使用的 Agent 能力数据。

如果 provider 未配置，运行必须失败，不能隐式回退。

### 8.2 产品与 Harness 单元测试允许 test double

为了稳定制造以下边界，单元/集成测试仍需 deterministic stub：

- 空模型输出；
- malformed tool call；
- reasoning-only response；
- HTTP 错误；
- retry limit；
- 特定 checkpoint 状态。

约束：

1. 重命名为 `ScriptedModelStub` 或 `ModelClientStub`；
2. 只位于产品/Harness 测试；
3. 不进入 benchmark runner；
4. 不产生 Resolved Rate；
5. 不出现在对外能力报告。

Golden Harness 测试可以使用人工 Null/Gold/错误 patch。这是在测试 Harness，不是在模拟 Agent 能力。

---

## 9. 实施顺序

实施必须遵循“先固定接口与评分，再接真实模型，最后迁移旧系统”。

### Phase 0：冻结现状和迁移地图（Day 1）

工作：

1. 收集现有 pytest node IDs、结果和耗时；
2. 分类为 product/harness/diagnostic/security；
3. 记录现有 12 个 benchmark；
4. 建立旧测试和 benchmark 的迁移矩阵；
5. 不在此阶段修产品行为。

产物：

```text
docs/testing/current-test-map.md
docs/testing/current-benchmark-map.md
docs/testing/migration-matrix.md
artifacts/test-baseline.json
```

Gate：每个旧测试和 benchmark 都有明确去向。

### Phase 1：Schema、Dataset、Prediction（Day 2）

顺序：

1. `schema.py`；
2. `dataset.py`；
3. `prediction.py`；
4. JSON/JSONL 序列化；
5. dataset digest；
6. public/private leakage 测试。

Gate：能加载最小任务、拒绝错误任务、写官方兼容 Prediction，且 private 数据不进入 inference payload。

### Phase 2：Artifacts、状态和错误模型（Day 3）

实现：

- Run ID 和目录规则；
- `RunManifest`、`AgentRunResult`、`EvaluationResult`；
- JSONL append 和原子写入；
- Agent/Data/Infra 错误分类；
- 中断时保留已完成产物。

Gate：任何阶段失败都留下可诊断结果，重跑不覆盖旧 run。

### Phase 3：本地 Workspace、Patch、Test、Grader（Day 4）

暂不调用模型，使用人工准备的：

- Null Patch；
- Gold Patch；
- Partial Patch；
- Regression Patch；
- Invalid/Conflict Patch。

实现：

1. inference/grading workspace；
2. strict patch apply；
3. test patch 注入；
4. pytest JUnit 输出；
5. log parser；
6. F2P/P2P grader。

Gate：Null→NO、Gold→FULL、Partial→PARTIAL、Regression→NO、Conflict→明确错误。

### Phase 4：Docker 隔离（Day 5）

实现：

- 固定 Dockerfile 和依赖锁；
- image digest；
- Provider 控制面按模型配置联网，Agent tool plane 与 grading plane 默认禁网；
- CPU、内存、进程和 timeout；
- mount allowlist；
- 强制清理。

Gate：Gold 连续三次一致；timeout 后没有残留容器；报告包含 image digest。

### Phase 5：真实模型 zzcode Adapter（Day 6）

实现：

1. `AgentAdapter`；
2. 连接现有 CLI/runtime；
3. 加载真实 provider；
4. 传递 problem statement；
5. 记录 steps、tokens、latency；
6. 收集 `git diff`；
7. 输出 Prediction。

Gate：不存在 Fake fallback；空 patch、模型错误和超时分类正确；Grader 不依赖 Agent 内部状态。

### Phase 6：2 个 Vertical Slice Task（Day 7）

选择：

1. single-file bug fix；
2. multi-file state bug。

每题执行：

```text
Null Validation
Gold Validation × 3
Real Agent Inference
Agent Patch Evaluation
Report Generation
```

Gate：Gold 均 FULL、Null 均非 FULL；Agent 无论成败都有完整 artifacts。

#### Phase 6 实现结果（2026-08-12）

- 已加入 `ZZCODE-BUG-001`：真实 Git 历史中的单文件 reasoning-only 响应缺陷；
- 已加入 `ZZCODE-BUG-002`：真实 Git 历史中的跨 `workspace.py` / `tools.py` `.env` 隔离缺陷；
- 公开题面位于 `evaluation/datasets/zzcode-bench-v1/`；
- 私有 Gold、hidden tests 和 F2P/P2P 位于 Git 忽略的 `evaluation/private/zzcode-bench-v1/`；
- `execution/runner.py` 已实现 Null → Gold×3 → Real Agent → Docker Grader → Artifacts；
- `scripts/run_internal_eval.py` 已成为两任务完整批次入口；
- 本地禁网 Docker Gate 已验证两题 Null 非 FULL、Gold 各连续三次 FULL，镜像摘要稳定；
- 真实模型出站运行需要使用者明确授权 Provider 数据外发和 API 成本，未授权时不伪造正式 Agent 成绩。

### Phase 6.5：自动模式、Verify 与完成门禁

实现顺序：

1. 普通 CLI 默认使用模型语义分类选择 General/Coding，Evaluation 强制 Coding；
2. 增加仓库级 verification profile 和不经过 shell 的 `verify(profile, selectors, timeout)`；
3. 增加 CodingProgress，记录阶段、修改、验证、重复读取和门禁状态；
4. 将完整 verify 与当前 patch digest 绑定，任何后续修改都会使旧验证失效；
5. Coding final 必须通过“非空 patch + 最后修改后的完整公开测试”门禁；
6. `tests/`、`evaluation/tests/`、private F2P/P2P 分别承担产品自检、Harness 自检和最终隐藏评分。

Phase 6.5 不改变 Null/Gold/F2P/P2P 评分口径，也不引入 Evaluator–Optimizer。真实模型验收继续使用 Phase 6 的两项 Repo Task，目标为两题均产生 patch 和完整 verify、无 EMPTY_PATCH，且至少一题 FULL。

### Phase 7：扩展到 8 个 Internal Verified Tasks（Day 8）

完成 dev/test split、Dataset Card、任务复核并冻结 `zzcode-bench-v1` digest，运行第一次正式 Pass@1。

Gate：8/8 Gold FULL、8/8 Null 非 FULL、0 个不稳定任务、0 个 private leakage。

### Phase 8：官方 SWE-bench Adapter（Day 9）

顺序：

1. 官方 dataset 字段转换；
2. 官方 workspace 中运行同一个 ZZCode Adapter；
3. 导出官方 `predictions.jsonl`；
4. 调用官方 Harness；
5. 导入官方结果；
6. Internal/External 分表报告。

运行规模：单题 → 3～5 smoke → 10～20 Verified pilot。第一版不直接跑 500 题。

Gate：官方 Harness 能消费 Prediction，且不修改官方评分口径。

### Phase 9：迁移旧 benchmark 和测试（Day 10 起）

顺序：

1. 先迁移 12 个旧 benchmark 的验证意图；
2. 停止正式 benchmark 使用 FakeModel；
3. 再按领域迁移现有 pytest；
4. 最后 deprecated 旧 evaluator/metrics 入口；
5. 完成 CI、README、架构图和面试报告。

新主链路未稳定前，不删除旧测试保护。

---

## 10. 现有 12 个 Benchmark 的迁移

旧 benchmark 不能整体改成真实模型能力评测，因为部分任务依赖脚本强制模型先犯特定错误或人工注入 checkpoint。正确做法是保留验证意图，迁到合适层级。

| 现有任务 | 验证意图 | 新去向 | 计入 Pass@1 | 模型政策 |
|---|---|---|---:|---|
| `readme_intro_locked` | 基础 patch 链路 | real-model smoke | 否 | 真实模型 |
| `readme_schema_note` | README patch | real-model smoke | 否 | 真实模型 |
| `sample_beta_locked` | 单文件 patch | real-model smoke | 否 | 真实模型 |
| `sample_gamma_locked` | 单文件 patch | real-model smoke | 否 | 真实模型 |
| `invalid_patch_recovery` | malformed tool 后恢复 | product integration | 否 | stub 制造边界 |
| `path_escape_recovery` | 路径逃逸后恢复 | security test；可选 real safety eval | 否 | 按测试层决定 |
| `repeated_read_recovery` | 重复工具保护 | integration/diagnostic | 否 | stub |
| `context_reduction_checkpoint` | 压缩时 checkpoint | context diagnostic | 否 | 可另做真实实验 |
| `freshness_reanchor_resume` | stale file 恢复 | resume diagnostic | 否 | 可另做真实实验 |
| `workspace_mismatch_resume` | workspace drift | resume diagnostic | 否 | 可另做真实实验 |
| `durable_promotion_accept` | durable memory 接受 | memory integration | 否 | stub/人工状态 |
| `durable_promotion_reject` | secret/transient 拒绝 | memory/security | 否 | stub/人工状态 |

例如 `invalid_patch_recovery` 的真实问题是：

```text
当 malformed tool call 已经发生时，runtime 能否安全恢复？
```

真实模型不一定会稳定犯这个指定错误，所以它属于产品 integration test，而不是未知任务 Pass@1。

迁移完成后：

1. 正式 benchmark CLI 不再调用 `SCRIPTED_MODEL_OUTPUTS`；
2. 脚本化场景变成测试 fixture；
3. 旧 `BenchmarkEvaluator` 标记 deprecated；
4. 正式能力报告只读取新 `evaluation/runs`；
5. 历史 artifacts 移到 `artifacts/legacy/` 并标注旧口径；
6. 旧 scripted 指标与新 Pass@1 永不混写。

---

## 11. 现有 pytest 的最终整合

### 11.1 目标结构

```text
tests/
├── README.md
├── conftest.py
├── factories.py
├── unit/
│   ├── test_task_state.py
│   ├── test_run_store.py
│   ├── test_memory.py
│   ├── test_context_manager.py
│   ├── test_openai_parser.py
│   └── test_anthropic_parser.py
├── integration/
│   ├── test_agent_loop.py
│   ├── test_tool_protocol.py
│   ├── test_session.py
│   ├── test_resume.py
│   ├── test_checkpoint.py
│   ├── test_artifacts.py
│   ├── test_cli.py
│   └── test_provider_requests.py
├── security/
│   ├── test_path_boundaries.py
│   ├── test_secret_redaction.py
│   ├── test_shell_environment.py
│   └── test_delegation.py
└── diagnostics/
    ├── test_context_ablation.py
    ├── test_memory_ablation.py
    └── test_recovery_ablation.py
```

### 11.2 文件级迁移

| 现有文件 | 新位置/处理 |
|---|---|
| `test_task_state.py` | `tests/unit/test_task_state.py` |
| `test_run_store.py` | `tests/unit/test_run_store.py` |
| `test_memory.py` | `tests/unit/test_memory.py` |
| `test_context_manager.py` | `tests/unit/test_context_manager.py` |
| `test_safety_invariants.py` | 拆到 `tests/security/*` |
| `test_evaluator.py` | 旧逻辑冻结；验证意图迁到 `evaluation/tests/*` |
| `test_metrics.py` | 拆到 diagnostics 和新 reporting tests |
| `test_zzcode.py` | 按 Agent/Tool/Session/Provider/CLI/Artifact 拆分 |

### 11.3 公共 Fixture

将重复的 workspace、Agent、HTTP response 和 artifact reader 提取到 `conftest.py` / `factories.py`：

```python
@pytest.fixture
def workspace_factory(tmp_path): ...

@pytest.fixture
def agent_factory(workspace_factory): ...

@pytest.fixture
def scripted_model_stub(): ...

@pytest.fixture
def run_artifact_reader(): ...
```

`scripted_model_stub` 禁止被 benchmark runner 导入。

### 11.4 Markers 与命令

```toml
[tool.pytest.ini_options]
markers = [
  "unit: fast isolated product tests",
  "integration: product component integration tests",
  "security: product security invariants",
  "diagnostic: context/memory/recovery experiments",
  "eval_harness: evaluation framework tests",
  "docker: tests requiring Docker",
  "real_model: tests requiring a configured real model",
  "slow: long-running tests",
]
```

```bash
# 每次提交
pytest -m "unit or integration or security"

# Harness 自测
pytest evaluation/tests -m "eval_harness and not real_model"

# Docker golden smoke
pytest evaluation/tests -m docker

# 真实模型 smoke，显式触发
pytest evaluation/tests -m real_model

# 诊断实验
pytest -m diagnostic
```

### 11.5 迁移规则

1. 一次只迁一个领域；
2. 先新增并验证，再删除旧测试；
3. 保留旧 node ID 到新 node ID 的映射；
4. 不在移动测试的同一 commit 修改产品行为；
5. 删除重复测试时记录承接测试；
6. 每个旧测试最终必须是 migrated、merged 或 intentionally retired。

---

## 12. Evaluation Harness 自身测试

### 12.1 Unit Tests

| 模块 | 重点 |
|---|---|
| Schema | 缺字段、版本、序列化、重复 ID |
| Dataset | split、digest、private leakage |
| Prediction | JSONL、空 patch、Unicode、重复记录 |
| Patch | apply/check/conflict/protected path |
| Parser | pass/fail/error/skip/not-run/collection error |
| Grader | FULL/PARTIAL/NO/零分母 |
| Reporting | 聚合、P50/P95、空集合 |
| Safety | path、测试删除、diff scope |

### 12.2 Integration 与 Golden Tests

必须覆盖：

- Null Patch → NO；
- Gold Patch → FULL；
- Partial Patch → PARTIAL；
- Regression Patch → NO；
- Invalid/Conflict Patch；
- 测试 timeout/collection error；
- Docker timeout 后清理；
- 中断后 artifacts 保留；
- run 之间不串写；
- inference/private 路径隔离。

Golden Test 不需要 LLM，因为它测试的是 Harness。

### 12.3 Real Model Smoke

使用真实模型跑 1～2 个简单 smoke，发现：

- provider 配置失效；
- Agent 无法启动；
- tool contract 变化；
- patch 无法导出；
- usage metadata 丢失。

它不属于稳定 PR CI，需要显式触发，也不混入正式 test split Pass@1。

---

## 13. 指标、运行产物与报告

### 13.1 单任务评分

```text
F2P Rate = passed F2P / total F2P
P2P Rate = passed P2P / total P2P

FULL
F2P=100% AND P2P=100% AND Safety=PASS

PARTIAL
0%<F2P<100% AND P2P=100% AND Safety=PASS

NO
其他情况
```

### 13.2 主指标

```text
Pass@1
= FULL instances / valid evaluated instances

Patch Apply Rate
= applied non-empty patches / submitted non-empty patches
```

同时报告：

- Partial Resolved Rate；
- F2P Pass Rate；
- P2P Preservation Rate；
- Agent/Test Completion Rate；
- Tool Steps、Latency、Tokens、Cost；
- Files Changed、Added/Deleted LOC；
- Agent Timeout、Infra Error、Safety Violation。

小数据集必须报告分子/分母，例如 `3/8 = 37.5%`。

### 13.3 执行漏斗

```text
Total
→ Submitted
→ Agent Completed
→ Patch Generated
→ Patch Applied
→ Tests Completed
→ FULL
```

### 13.4 Failure Taxonomy

```text
REPO_UNDERSTANDING
WRONG_FILE_SELECTION
WRONG_IMPLEMENTATION
MISSING_EDGE_CASE
REGRESSION
TOOL_FAILURE
CONTEXT_LOSS
UNSAFE_MODIFICATION
PREMATURE_COMPLETION
EMPTY_PATCH
PATCH_APPLY_FAILURE
AGENT_TIMEOUT
INFRASTRUCTURE_ERROR
```

### 13.5 运行产物

```text
evaluation/runs/<run_id>/
├── run_manifest.json
├── predictions.jsonl
├── instance_results.jsonl
├── results.json
└── instances/
    └── ZZCODE-BUG-001/
        ├── task.json
        ├── agent.log
        ├── trajectory.jsonl
        ├── patch.diff
        ├── patch_apply.log
        ├── f2p.xml
        ├── p2p.xml
        ├── test_output.log
        └── report.json
```

`run_manifest.json` 至少记录 dataset digest、Agent commit、provider/model、模型参数、预算、image digest、OS/architecture 和起止时间。

### 13.6 报告规则

报告必须包含：

1. Run Configuration；
2. Dataset Distribution；
3. Primary Results；
4. Results by Category；
5. Execution Funnel；
6. Efficiency；
7. Failure Taxonomy；
8. Safety；
9. Infrastructure Errors；
10. Known Limitations；
11. Reproduction Commands。

禁止：

- 把 scripted harness 通过率写成 Agent Resolved Rate；
- 把 Infra Error 默认算作模型失败；
- 把 diagnostics 与 Pass@1 加权；
- 在 N 很小时只写百分比；
- 混比不同任务、预算或模型而不披露；
- 把 dev 调参结果当 held-out test 结果。

---

## 14. CI 与运行频率

### Pull Request

```text
Product unit + integration + security
Evaluation unit
One local golden fixture
Ruff
```

不调用真实模型，目标是快、确定、无费用。

### Nightly / Manual

```text
Docker golden validation
Dataset Null/Gold validation
Container cleanup
可选 real-model smoke
```

### Release / Evidence Freeze

```text
完整 internal zzcode-bench
+
官方 SWE-bench Verified pilot
```

官方评测本地只做少量 smoke；正式 pilot 优先使用稳定 x86 Linux 或远程环境，并保存官方原始结果。

---

## 15. 风险与对策

| 风险 | 对策 |
|---|---|
| Agent 看到 Gold/hidden tests | private root 位于独立目录；inference 不挂载；Loader 只输出 public view |
| Agent 污染评分环境 | inference/grading 使用不同 workspace/container |
| 任务过于简单 | 字符串替换只做 smoke；正式集包含多文件、状态和边界条件 |
| 自建任务偏向作者 | held-out test split + 官方 SWE-bench pilot |
| Docker/依赖不稳定 | lock dependency、固定 image digest、评分禁网、Gold 三次验证 |
| 模型非确定性 | 固定配置和预算，记录 manifest；第一版报告 Pass@1 |
| 旧测试迁移丢覆盖 | 迁移矩阵、先增后删、一次一个领域、行为修改分开提交 |
| Agent 修改测试绕过评分 | patch safety、private hidden tests、独立 grading workspace |

---

## 16. 完成定义

### 架构

- [ ] Product、Harness、Benchmark 三层分离；
- [ ] Task/Prediction/Result schema 版本化；
- [ ] Agent 与 Grader 通过 patch 解耦；
- [ ] inference/grading 环境分离；
- [ ] private tests 对 Agent 不可见；
- [ ] 官方 SWE-bench 通过 Adapter 接入，不重写官方评分。

### 数据与运行

- [ ] 至少 8 个 Internal Verified Tasks；
- [ ] 每题有 Null/Gold 验证；
- [ ] Gold 三次运行稳定；
- [ ] Dataset Card、split、digest 完成；
- [ ] internal 正式运行使用真实模型；
- [ ] 完成 3～5 个官方 smoke，目标扩展 10～20 pilot。

### 测试与迁移

- [ ] Harness unit/integration/golden/docker tests；
- [ ] real-model smoke；
- [ ] 现有测试全部 migrated/merged/retired；
- [ ] 现有 12 个 benchmark 全部有明确新去向；
- [ ] 正式 benchmark 不再调用 FakeModel；
- [ ] Fake/test double 仅保留在产品或 Harness 测试。

### 报告

- [ ] Pass@1、F2P、P2P、Patch Apply；
- [ ] 执行漏斗、Tokens、Latency、Tool Steps；
- [ ] Failure Taxonomy、Safety、Infra Error；
- [ ] Internal 与 External 结果分表；
- [ ] 完整复现命令和已知限制。

---

## 17. 面试与简历表达

### 面试讲解顺序

1. **问题**：pytest 验证 runtime，但不能证明 Agent 能解未知任务；旧 benchmark 又依赖脚本化模型输出。
2. **分层**：Product Tests、Harness Tests、Executable Benchmarks。
3. **接口**：Agent 最终只提交 `git diff` patch。
4. **隔离**：Inference 与 Grading 使用不同环境，hidden tests 只在评分阶段注入。
5. **评分**：F2P 验证修复，P2P 验证无回归，安全为独立 Gate。
6. **可信度**：Null、Gold、稳定性验证；Agent/Data/Infra 错误分开。
7. **外部有效性**：同一 Adapter 导出官方 SWE-bench Prediction。
8. **结果**：展示 Internal 和 External 两张表，不混口径。

### 简历模板

没有真实数据前：

> 重构 Coding Agent 测试体系，将产品回归、Evaluation Harness 和 Agent 能力评测分层；设计 SWE-bench-compatible Task/Patch 接口，在隔离 Docker 中通过 FAIL_TO_PASS 与 PASS_TO_PASS 进行可执行评分。

> 构建版本化 Repo Task 数据集和 Null/Gold/Agent 三重验证流程，自动保存模型配置、执行轨迹、patch、测试日志及分实例结果，并支持导出官方 SWE-bench predictions。

有数据后：

> 在 **[N] 个 Internal Verified Tasks** 上取得 **[X/N，X% Pass@1]** 和 **[Y% P2P Preservation]**，并在 **[M] 个 SWE-bench Verified Pilot Tasks** 上完成外部验证，安全违规率为 **[Z%]**。

---

## 18. 实施第一原则

不要先移动 `tests/`，也不要先删除旧 `evaluator.py`。正确顺序是：

```text
冻结现状并建立迁移地图
→ 固定 Schema/Prediction/Result
→ 用人工 Null/Gold Patch 跑通 Harness
→ 加入 Docker
→ 接入真实模型
→ 完成 2 个 Vertical Slice
→ 扩展 Internal Dataset
→ 接官方 SWE-bench
→ 最后迁移旧 benchmark 和现有测试
```

这样在新主链路尚未稳定时，不会失去旧系统提供的回归保护。
