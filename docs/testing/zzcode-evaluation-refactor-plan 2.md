# zzcode 测试与 Evaluation 架构重构实施方案

> 文档状态：实施指导稿  
> 目标读者：第一次系统建设 Coding Agent Evaluation 的开发者  
> 核心目标：在保留现有测试价值的前提下，构建一套近期可落地、可复现、可用于面试与简历展示的 SWE-bench-compatible 执行式评测系统。  
> 明确边界：第一版不实现 Evaluator–Optimizer，不根据隐藏测试结果进行 Repair，不使用 LLM Judge 作为正确性判定。

---

## 1. 为什么要进行这次重构

zzcode 当前已经有产品测试、确定性 benchmark、指标实验和运行产物，但这些内容混合承担了不同职责：

- 一部分测试验证 runtime、memory、tools、CLI 等产品代码是否正确；
- 一部分测试验证 evaluator、metrics 和 artifact 是否能工作；
- 一部分 benchmark 使用 `FakeModelClient` 和预设模型输出，验证固定执行路径；
- 一部分实验测量 context、memory、resume 等模块效果。

这些内容都有价值，但目前容易产生三个误解：

1. **产品测试通过不等于 Agent 能解决未知仓库任务。**
2. **预设 Fake LLM 输出的 benchmark 通过不等于真实模型任务解决率为 100%。**
3. **消融指标不能与 SWE-bench 式 Pass@1 混在同一个分数中。**

本次重构的目标不是删除旧测试，而是重新建立清晰边界：

```text
产品正确性测试
    验证 zzcode 代码有没有坏

Evaluation Harness 测试
    验证任务、容器、patch、测试解析和评分有没有坏

Agent 能力评测
    验证真实模型驱动的 zzcode 能否独立解决未知任务
```

最终形成三层质量体系：

```text
L1 Product Tests
unit + integration + security
                ↓
L2 Evaluation Harness Tests
schema + runner + patch + grader + golden validation
                ↓
L3 Executable Agent Benchmarks
internal zzcode-bench + external SWE-bench Verified pilot
```

---

## 2. 本方案最终要交付什么

完成后，仓库应具备以下能力：

1. 能清楚说明现有每一类测试在验证什么；
2. 能使用真实模型运行自建 Repo Task；
3. Agent 最终只提交标准 `git diff` patch；
4. 能在与推理环境分离的干净环境中应用 patch；
5. 能通过 FAIL_TO_PASS 和 PASS_TO_PASS 进行确定性评分；
6. 能验证 Null Patch、Gold Patch 和 Agent Patch；
7. 能保存每次运行的配置、轨迹、patch、测试日志和指标；
8. 能输出 SWE-bench-compatible `predictions.jsonl`；
9. 能把同一个 zzcode Agent Adapter 接到官方 SWE-bench；
10. 能将现有测试和旧 benchmark 全部迁移到职责明确的位置；
11. 能形成架构图、结果表和面试讲解材料。

第一版最终演示链路：

```text
Task Instance
  ├── problem statement
  ├── base commit
  └── resource limits
          ↓
Real Model + zzcode Agent
          ↓
model_patch
          ↓
Fresh Grading Environment
  ├── apply model patch
  ├── inject private test patch
  └── run executable tests
          ↓
F2P / P2P / Safety
          ↓
FULL / PARTIAL / NO
          ↓
JSONL Results + Markdown Report
```

---

## 3. 关键术语解释

### 3.1 Product Test

产品测试直接测试 zzcode 的代码实现，例如：

- `TaskState` 是否正确更新状态；
- 路径逃逸是否被阻止；
- OpenAI/Anthropic 请求 payload 是否正确；
- Session 是否能保存和恢复；
- Context Manager 是否保留当前请求。

这类测试允许使用 mock、stub 或 deterministic test double，因为目标是隔离模块、稳定复现边界条件。

它们不产生 Agent Pass@1。

### 3.2 Evaluation Task / Instance

一个 Evaluation Instance 是一个完整仓库级任务，至少包含：

- 一个修复前的仓库版本 `base_commit`；
- 一段给 Agent 看的 `problem_statement`；
- 一组 Agent 看不到的评分数据；
- 能证明问题被修复的 FAIL_TO_PASS 测试；
- 能证明旧功能没有回归的 PASS_TO_PASS 测试。

### 3.3 Prediction

Prediction 是 Agent 对任务的最终提交。为兼容 SWE-bench，核心格式固定为：

```json
{
  "instance_id": "ZZCODE-001",
  "model_name_or_path": "provider/model-name",
  "model_patch": "diff --git a/... b/..."
}
```

Evaluation Harness 不需要知道 Agent 的内部推理过程，只消费最终 patch。

### 3.4 Gold Patch

Gold Patch 是已知正确的参考修复，用于验证任务和 grader 自身是否正确。

Gold Patch 的作用不是要求 Agent 生成完全相同的代码，而是证明：

```text
这个任务确实可以解决
+
这组测试能够认可一个正确实现
```

### 3.5 Test Patch

Test Patch 是针对任务新增或修改的测试，通常包含隐藏测试。它只能在评分阶段由 Harness 注入，不能出现在 Agent 可见工作区。

### 3.6 FAIL_TO_PASS（F2P）

在修复前失败、应用正确修复后通过的测试。

它回答：

> 任务描述中的问题是否真正被解决？

### 3.7 PASS_TO_PASS（P2P）

在修复前通过、应用修复后仍应通过的测试。

它回答：

> Agent 是否在修复新问题的同时破坏了旧功能？

### 3.8 Null / Gold / Agent Validation

每个自建任务必须验证三种 patch：

```text
Null Patch
证明 base commit 确实存在待修复问题

Gold Patch
证明任务可解且评分器能认可正确答案

Agent Patch
正式测量 Agent 能力
```

### 3.9 Infrastructure Error 与 Agent Failure

两者必须区分：

```text
Infrastructure Error
Docker 构建失败、Harness 崩溃、日志解析器自身异常

Agent Failure
Agent 超时、空 patch、非法 patch、patch 无法应用、测试失败
```

基础设施故障不能悄悄计入模型失败，否则分数不可信。

### 3.10 一个 Repo Task 从开始到评分的完整流程

这一节使用 `ZZCODE-BUG-001` 说明一项任务如何从数据集进入真实模型，再经过隔离执行，最终得到 `FULL / PARTIAL / NO`。

先看一张完整但仍然简化的总流程图：

```text
┌─────────────────────────────────────────────────────────────────────┐
│ 1. Task Preparation：任务准备                                      │
│                                                                     │
│ Public Task                         Private Grading Data             │
│ ├── instance_id                     ├── gold.patch                   │
│ ├── problem_statement               ├── test.patch                   │
│ ├── repo                             ├── FAIL_TO_PASS                 │
│ └── base_commit                      └── PASS_TO_PASS                 │
└───────────────────────────────┬─────────────────────────────────────┘
                                │ Public Task 进入推理阶段
                                │ Private 数据不进入 Agent 环境
                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│ 2. Inference：真实模型驱动 zzcode                                   │
│                                                                     │
│ 从 base_commit 创建 inference workspace                             │
│              ↓                                                      │
│ zzcode 获得 problem statement                                       │
│              ↓                                                      │
│ 真实模型调用 read/search/shell/write/patch 等工具                   │
│              ↓                                                      │
│ zzcode 修改 memory.py、runtime.py 等文件                            │
│              ↓                                                      │
│ Patch Collector 执行 git diff                                       │
│              ↓                                                      │
│ patch.diff + trajectory.jsonl + agent.log                           │
│              ↓                                                      │
│ 写入 predictions.jsonl                                              │
└───────────────────────────────┬─────────────────────────────────────┘
                                │ 只把标准 Prediction 交给评分阶段
                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│ 3. Execution Harness：隔离执行                                      │
│                                                                     │
│ Prediction Loader 读取对应 instance 的 model_patch                  │
│              ↓                                                      │
│ 创建全新的 grading workspace / container                           │
│              ↓                                                      │
│ checkout 同一个 base_commit                                         │
│              ↓                                                      │
│ Patch Safety 检查受保护路径与非法修改                               │
│              ↓                                                      │
│ git apply --check model.patch                                       │
│              ↓                                                      │
│ git apply model.patch                                               │
│              ↓                                                      │
│ Harness 注入 private test.patch / hidden tests                      │
│              ↓                                                      │
│ Test Executor 运行 F2P 与 P2P                                      │
│              ↓                                                      │
│ Log Parser 将 JUnit XML 转换为结构化 TestCaseResult                 │
└───────────────────────────────┬─────────────────────────────────────┘
                                │ 结构化测试结果进入 Grader
                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│ 4. Grading & Reporting：判分与汇总                                  │
│                                                                     │
│ 计算 F2P Rate + P2P Rate + Safety Result                            │
│              ↓                                                      │
│ FULL / PARTIAL / NO / AGENT_ERROR / INFRA_ERROR                     │
│              ↓                                                      │
│ 写入 instance report.json                                           │
│              ↓                                                      │
│ 写入 instance_results.jsonl                                         │
│              ↓                                                      │
│ Reporter 聚合所有任务，生成 Pass@1 和 Markdown Report              │
└─────────────────────────────────────────────────────────────────────┘
```

需要特别注意：

```text
不是 Grader 直接读取 predictions.jsonl 并执行所有步骤。

Prediction Loader 读取答案；
Execution Harness 准备环境、应用 patch 和运行测试；
Log Parser 解析结果；
Grader 最后只负责依据结构化结果判分。
```

#### Step 1：Dataset Loader 读取 Repo Task

公开任务文件：

```json
{
  "instance_id": "ZZCODE-BUG-001",
  "repo": "local/zzcode",
  "base_commit": "abc123",
  "problem_statement": "外部文件发生变化后，memory 中的旧文件摘要仍然被使用。"
}
```

私有评分文件：

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

Dataset Loader 会生成两个不同的视图：

```text
Inference View
只包含 Agent 可以看到的 repo、base_commit 和 problem_statement

Grading View
包含 test.patch、F2P、P2P 和 gold patch 引用
```

`gold.patch` 只用于数据集认证，不用于正式 Agent 评分，也不能传给 Agent。

#### Step 2：Workspace Manager 创建推理工作区

Workspace Manager 根据 `repo + base_commit` 创建一份任务起始代码：

```text
/tmp/zzcode-eval/<run_id>/inference/ZZCODE-BUG-001/
```

概念上相当于：

```bash
git clone <repo> <inference-workspace>
git checkout --detach abc123
git reset --hard abc123
git clean -fdx
```

此时：

- bug 仍然存在；
- Agent 的起点与其他 run 相同；
- workspace 中没有 `gold.patch`；
- workspace 中没有 `test.patch` 或 hidden tests；
- workspace 中没有上一次运行留下的修改。

#### Step 3：真实模型驱动 zzcode 解决任务

`zzcode_adapter.py` 使用真实 provider 和模型启动 Agent：

```text
problem_statement
        ↓
zzcode 构造 prompt
        ↓
真实模型返回工具调用
        ↓
zzcode 执行 read_file / search / run_shell / patch_file
        ↓
模型继续观察结果和修改代码
        ↓
模型返回最终答案或达到预算
```

例如 Agent 可能执行：

```text
search("file_summaries")
        ↓
read_file("zzcode/memory.py")
        ↓
read_file("zzcode/runtime.py")
        ↓
run_shell("pytest tests/test_memory.py")
        ↓
patch_file("zzcode/memory.py", ...)
```

推理阶段保存：

```text
agent.log
trajectory.jsonl
session.json
token_usage.json
```

这些文件用于诊断过程，但不会作为正确性答案交给 Grader。

#### Step 4：Patch Collector 收集 Agent 的正式答案

Agent 结束后，Patch Collector 在 inference workspace 中执行：

```bash
git diff --binary --no-ext-diff
```

得到：

```text
patch.diff
```

示例：

```diff
diff --git a/zzcode/memory.py b/zzcode/memory.py
--- a/zzcode/memory.py
+++ b/zzcode/memory.py
@@ -100,4 +100,6 @@
 def get_summary(path):
-    return cache[path]
+    if is_stale(path):
+        cache.pop(path, None)
+    return cache.get(path)
```

为什么使用 `git diff` 而不是 Agent 的最终文本：

```text
Agent 说“已经修复”不代表文件真的被修改；
Agent 没在最终回答中粘贴代码，也可能已经正确修改文件；
git diff 能精确、可执行地描述最终文件变化。
```

#### Step 5：Prediction Writer 写入 predictions.jsonl

Patch Collector 把 patch 包装为标准 Prediction：

```jsonl
{"instance_id":"ZZCODE-BUG-001","model_name_or_path":"provider/model-name","model_patch":"diff --git a/zzcode/memory.py b/zzcode/memory.py\n..."}
```

如果本次运行有 8 个任务，`predictions.jsonl` 就有 8 行。每一行表示一个任务的最终答案。

```text
predictions.jsonl
├── line 1：ZZCODE-BUG-001 的 patch
├── line 2：ZZCODE-BUG-002 的 patch
├── line 3：ZZCODE-FEATURE-001 的 patch
└── ...
```

运行轨迹、token 和 latency 不放进 `model_patch`，而是保存在 run artifacts 中。

#### Step 6：Evaluation Harness 读取对应 Prediction

Harness 根据 `instance_id` 将 Task 和 Prediction 配对：

```text
Task.instance_id == Prediction.instance_id
```

如果出现以下情况，应在执行前报错：

- Prediction 中存在数据集没有的 instance；
- 一个 instance 出现两条 Prediction；
- model patch 字段缺失；
- Prediction 使用了不支持的 schema。

空 patch 不属于 Harness 崩溃，而应记录为：

```text
Agent Failure: EMPTY_PATCH
```

#### Step 7：创建全新的评分工作区

Harness 不直接使用 Agent 工作过的 inference workspace，而是重新创建：

```text
/tmp/zzcode-eval/<run_id>/grading/ZZCODE-BUG-001/
```

再次恢复到：

```text
repo = local/zzcode
HEAD = abc123
working tree = clean
```

这样评分只接受 patch 中明确记录的修改，不接受 Agent 对环境产生的其他副作用。

#### Step 8：检查并应用 model patch

Patch Applier 首先检查：

```text
patch 格式是否合法
是否修改 .git/.env/private/hidden_tests
是否发生路径逃逸
是否超过允许的文件或 diff 范围
```

然后执行：

```bash
git apply --check model.patch
git apply model.patch
```

结果可能是：

```text
APPLIED                 成功应用
EMPTY_PATCH             没有修改
INVALID_PATCH           patch 格式错误
PATCH_CONFLICT          与 base commit 不匹配
PROTECTED_PATH_MODIFIED 修改了受保护路径
```

只有 `APPLIED` 才继续进入测试阶段。

#### Step 9：Harness 注入 hidden tests

Agent patch 应用后，Harness 从 private task data 读取 `test.patch`：

```bash
git apply test.patch
```

评分工作区此时类似：

```text
grading-workspace/
├── zzcode/
├── tests/
└── hidden_tests/
    └── test_file_freshness.py
```

这些 hidden tests 在推理阶段从未出现，因此 Agent 无法直接针对具体断言硬编码。

#### Step 10：Test Executor 运行 F2P 和 P2P

先运行任务相关测试：

```bash
pytest -q \
  hidden_tests/test_file_freshness.py::test_external_edit_invalidates_summary \
  --junitxml=/tmp/f2p.xml
```

再运行回归测试：

```bash
pytest -q \
  tests/test_memory.py \
  tests/test_context_manager.py \
  --junitxml=/tmp/p2p.xml
```

含义：

```text
F2P
检查“新问题是否修好”

P2P
检查“旧功能是否仍然正常”
```

假设结果为：

```text
F2P：1/1 passed
P2P：18/18 passed
```

#### Step 11：Log Parser 生成结构化测试结果

Log Parser 不把终端中的 `....F..` 直接交给 Grader，而是解析 JUnit XML：

```json
{
  "fail_to_pass": {
    "passed": 1,
    "failed": 0,
    "total": 1
  },
  "pass_to_pass": {
    "passed": 18,
    "failed": 0,
    "total": 18
  },
  "tests_completed": true
}
```

如果 pytest 无法收集、进程崩溃或超时，也要形成明确状态，而不是假装为普通断言失败。

#### Step 12：Grader 生成单任务评分

Grader 接收结构化结果，计算：

```text
F2P Rate = 1 / 1 = 100%
P2P Rate = 18 / 18 = 100%
Safety = PASS
```

因此：

```text
ZZCODE-BUG-001 → FULL / Resolved
```

如果结果不同：

```text
F2P 1/1，P2P 17/18
→ 新问题修好但引入回归
→ NO

F2P 1/2，P2P 18/18
→ 只修复部分目标行为
→ PARTIAL

F2P 0/2，P2P 18/18
→ 没有修复问题
→ NO

Safety violation
→ 即使测试通过也不能 FULL
```

单任务报告示例：

```json
{
  "instance_id": "ZZCODE-BUG-001",
  "patch_applied": true,
  "tests_completed": true,
  "fail_to_pass_rate": 1.0,
  "pass_to_pass_rate": 1.0,
  "safety_violations": [],
  "resolved_status": "FULL"
}
```

#### Step 13：Reporter 聚合整个 run

如果数据集有 8 个任务，Reporter 读取 8 条单任务结果：

```text
FULL：3
PARTIAL：2
NO：2
AGENT_ERROR：1
INFRA_ERROR：0
```

生成：

```text
Pass@1                = 3/8 = 37.5%
Partial Rate          = 2/8 = 25.0%
Patch Apply Rate      = 7/8 = 87.5%
Agent Completion Rate = 7/8 = 87.5%
Infrastructure Errors = 0
```

最终保存：

```text
evaluation/runs/<run_id>/
├── run_manifest.json
├── predictions.jsonl
├── results.json
├── instance_results.jsonl
└── instances/
    └── ZZCODE-BUG-001/
        ├── agent.log
        ├── trajectory.jsonl
        ├── patch.diff
        ├── patch_apply.log
        ├── f2p.xml
        ├── p2p.xml
        ├── test_output.log
        └── report.json
```

#### 失败分支总览

完整系统不只有“hidden test 通过/失败”两条路径：

```text
加载 Task
  ├── task/schema 错误
  │     └── DATASET_ERROR：阻止正式运行
  │
  └── Task 合法
        ↓
运行真实 Agent
  ├── provider/config 错误
  │     └── INFRA_ERROR
  ├── Agent 超时
  │     └── AGENT_ERROR / TIMEOUT
  ├── 没有文件变化
  │     └── AGENT_ERROR / EMPTY_PATCH
  └── 产生 patch
        ↓
应用 patch
  ├── 非法或冲突
  │     └── NO / PATCH_APPLY_FAILURE
  ├── 修改受保护路径
  │     └── NO / SAFETY_VIOLATION
  └── 应用成功
        ↓
运行测试
  ├── Harness/Docker 自身故障
  │     └── INFRA_ERROR
  ├── 测试超时或 collection error
  │     └── NO 或 TEST_ERROR，并保留详细原因
  └── 得到结构化结果
        ↓
Grader
  ├── F2P=100%，P2P=100%，Safety=PASS → FULL
  ├── 0%<F2P<100%，P2P=100%              → PARTIAL
  └── 其他                                → NO
```

将最开始的简化流程修正为更准确的版本，就是：

```text
Repo Task：ZZCODE-BUG-001
        ↓
Dataset Loader 生成不含私有答案的 Inference View
        ↓
Workspace Manager 从 base_commit 创建推理工作区
        ↓
真实模型驱动 zzcode 搜索、阅读、测试和修改 memory.py
        ↓
Patch Collector 执行 git diff，生成 patch.diff
        ↓
Prediction Writer 写入 predictions.jsonl
        ↓
Evaluation Harness 将 Task 与 Prediction 配对
        ↓
创建全新的 grading workspace，并 checkout 同一 base_commit
        ↓
Patch Safety 检查后 git apply model_patch
        ↓
Harness 注入 private test.patch / hidden tests
        ↓
Test Executor 运行 F2P 与 P2P
        ↓
Log Parser 生成结构化测试结果
        ↓
Grader 计算 F2P/P2P/Safety，得到 FULL / PARTIAL / NO
        ↓
Reporter 保存单任务报告并聚合整个数据集的 Pass@1
```

### 3.11 上述流程对应哪些目录和文件

上一节描述的是运行逻辑，本节把每一个流程节点映射到目标目录中的具体文件。阅读代码时，可以从入口脚本开始，沿箭头逐层进入，而不需要先理解所有模块。

#### 3.11.1 一张图看懂文件调用链

```text
命令行入口
scripts/run_internal_eval.py
        │
        │ 读取 benchmark、agent、release gate 配置
        ├─────────────── evaluation/configs/*.yaml
        │
        ▼
流程编排器
zzcode/evaluation/execution/runner.py
        │
        ├── 1. 加载任务
        │      zzcode/evaluation/dataset.py
        │             ├── evaluation/datasets/zzcode-bench-v1/manifest.jsonl
        │             ├── evaluation/datasets/zzcode-bench-v1/instances/<id>/task.json
        │             ├── evaluation/datasets/zzcode-bench-v1/instances/<id>/problem_statement.md
        │             └── evaluation/datasets/zzcode-bench-v1/private/<id>/grading.json
        │
        ├── 2. 创建运行记录
        │      zzcode/evaluation/reporting/artifacts.py
        │             └── evaluation/runs/<run_id>/run_manifest.json
        │
        ├── 3. 准备 inference workspace
        │      zzcode/evaluation/execution/workspace.py
        │             └── checkout task.base_commit
        │
        ├── 4. 运行真实 zzcode Agent
        │      zzcode/evaluation/inference/zzcode_adapter.py
        │             ├── 调用现有 zzcode/cli.py 的装配逻辑
        │             ├── 调用现有 zzcode/runtime.py 的 Agent loop
        │             └── 保存 agent.log / trajectory.jsonl
        │
        ├── 5. 收集最终代码答案
        │      zzcode/evaluation/inference/patch_collector.py
        │             └── git diff → instances/<id>/patch.diff
        │
        ├── 6. 写出标准 Prediction
        │      zzcode/evaluation/prediction.py
        │             └── evaluation/runs/<run_id>/predictions.jsonl
        │
        ├── 7. 准备独立 grading workspace / container
        │      zzcode/evaluation/execution/workspace.py
        │      zzcode/evaluation/execution/docker_runner.py
        │             └── evaluation/environments/zzcode-py313/*
        │
        ├── 8. 检查并应用 Agent patch
        │      zzcode/evaluation/grading/safety.py
        │      zzcode/evaluation/execution/patch_applier.py
        │             └── git apply model_patch
        │
        ├── 9. 注入并执行隐藏测试
        │      zzcode/evaluation/execution/test_executor.py
        │             ├── private/<id>/test.patch
        │             ├── FAIL_TO_PASS
        │             └── PASS_TO_PASS
        │
        ├── 10. 解析测试输出
        │       zzcode/evaluation/execution/log_parser.py
        │             └── JUnit XML → TestCaseResult[]
        │
        ├── 11. 生成单任务评分
        │       zzcode/evaluation/grading/grader.py
        │       zzcode/evaluation/grading/status.py
        │             └── instances/<id>/report.json
        │
        └── 12. 聚合整个数据集
               zzcode/evaluation/reporting/aggregate.py
               zzcode/evaluation/reporting/markdown.py
                      ├── instance_results.jsonl
                      ├── results.json
                      └── evaluation/reports/<run_id>.md
```

这里新增了 `execution/runner.py`。它是 Evaluation Harness 的主编排器：本身不实现 Git、Docker、测试或评分细节，只负责按顺序调用其他模块，并保证任何阶段失败时仍能写出运行产物。

#### 3.11.2 四个阶段与文件的对应关系

| 阶段 | 主要输入 | 负责文件 | 文件做什么 | 主要输出 |
|---|---|---|---|---|
| Task Preparation | manifest、task、private grading | `schema.py`、`dataset.py` | 校验并加载任务，分离 public/private 视图 | `TaskInstance`、`PrivateTestSpec` |
| Run Initialization | benchmark/agent 配置 | `run_internal_eval.py`、`artifacts.py` | 创建 run ID，冻结模型与环境配置 | `run_manifest.json` |
| Inference Workspace | repo、base commit | `workspace.py` | 创建 Agent 可写的干净起始仓库 | inference workspace |
| Agent Inference | problem statement、Agent config | `zzcode_adapter.py` | 用真实模型驱动现有 zzcode 修改仓库 | `AgentRunResult`、trajectory |
| Patch Collection | 修改后的仓库 | `patch_collector.py` | 执行 `git diff` 并校验是否有修改 | `patch.diff` |
| Prediction Export | instance ID、patch | `prediction.py` | 写出 SWE-bench-compatible JSONL | `predictions.jsonl` |
| Grading Setup | repo、base commit、image config | `workspace.py`、`docker_runner.py` | 创建全新的评分仓库和隔离容器 | grading workspace/container |
| Safety & Apply | model patch、scope policy | `safety.py`、`patch_applier.py` | 检查路径并应用 patch | patch apply result/log |
| Test Injection | private test patch | `test_executor.py` | 在评分环境加入 Agent 未见过的测试 | 带 hidden tests 的 workspace |
| Test Execution | F2P/P2P test IDs | `test_executor.py` | 调用 pytest 并生成结构化报告文件 | logs、JUnit XML |
| Log Parsing | JUnit XML、exit code | `log_parser.py` | 转为统一 `TestCaseResult` | F2P/P2P 结构化结果 |
| Grading | 测试结果、安全结果 | `grader.py`、`status.py` | 计算 rate 并判定 FULL/PARTIAL/NO | `EvaluationResult` |
| Persistence | 各阶段产物 | `artifacts.py` | 原子保存单任务与整次 run 产物 | `report.json`、JSONL |
| Aggregation | 所有 `EvaluationResult` | `aggregate.py` | 计算 Pass@1、漏斗和效率指标 | `results.json` |
| Presentation | 聚合结果、manifest | `markdown.py` | 生成可阅读报告 | Markdown report |
| External SWE-bench | 官方 dataset/result | `adapters/swebench.py` | 字段转换、Prediction 导出、结果导入 | 官方 predictions/results |

#### 3.11.3 每个关键文件里面应该写什么

下面的代码只是职责骨架，不要求第一版完全照抄类名，但不应把这些职责重新混回一个超大文件。

##### `zzcode/evaluation/schema.py`

保存纯数据模型，不执行 Git、Docker 或模型调用：

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

##### `zzcode/evaluation/dataset.py`

负责把磁盘数据转换成 Schema 对象：

```python
class EvaluationDataset:
    @classmethod
    def load(cls, root: Path, split: str) -> "EvaluationDataset": ...

    def public_task(self, instance_id: str) -> TaskInstance: ...

    def private_spec(self, instance_id: str) -> PrivateTestSpec: ...

    def inference_payload(self, instance_id: str) -> dict: ...

    def digest(self) -> str: ...
```

重点约束：`inference_payload()` 不得包含 `gold_patch`、`test_patch`、F2P 或 P2P。

##### `zzcode/evaluation/execution/runner.py`

负责连接所有模块，是整个 Evaluation Harness 的主入口：

```python
class EvaluationRunner:
    def run(self, dataset, split, agent_adapter, run_config):
        run = self.artifacts.start_run(...)
        for task in dataset.tasks(split):
            result = self.run_instance(task, dataset.private_spec(task.instance_id), run)
            self.artifacts.append_result(result)
        return self.reporter.finalize(run)

    def run_instance(self, task, private_spec, run):
        inference_workspace = self.workspaces.create_inference(task)
        agent_result = self.agent.run(task, inference_workspace, run.config)
        prediction = self.patch_collector.collect(task, agent_result)
        self.artifacts.append_prediction(prediction)
        return self.evaluate_prediction(task, private_spec, prediction, run)
```

Runner 应捕获已知阶段异常并转成明确状态，但不能把编程 bug 静默转成普通模型失败。

##### `zzcode/evaluation/inference/adapter.py`

定义可替换 Agent 的接口：

```python
class AgentAdapter(Protocol):
    def run(
        self,
        task: TaskInstance,
        workspace: Path,
        config: AgentRunConfig,
    ) -> AgentRunResult: ...
```

##### `zzcode/evaluation/inference/zzcode_adapter.py`

实现上述接口，并连接现有产品代码：

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

这里必须调用真实 provider，不允许没有配置时回退到 Fake LLM。

##### `zzcode/evaluation/inference/patch_collector.py`

```python
def collect_patch(workspace: Path) -> str:
    result = run_checked(
        ["git", "diff", "--binary", "--no-ext-diff"],
        cwd=workspace,
    )
    return result.stdout


def build_prediction(task, model_name, patch) -> Prediction: ...
```

这个文件只负责提取和包装 patch，不负责运行隐藏测试。

##### `zzcode/evaluation/prediction.py`

```python
def append_prediction(path: Path, prediction: Prediction) -> None: ...

def load_predictions(path: Path) -> dict[str, Prediction]: ...

def validate_predictions(tasks, predictions) -> None: ...
```

它负责 JSONL 格式和 instance 配对，不负责评分。

##### `zzcode/evaluation/execution/workspace.py`

```python
class WorkspaceManager:
    def create_inference(self, task: TaskInstance) -> Path: ...

    def create_grading(self, task: TaskInstance) -> Path: ...

    def checkout_base(self, workspace: Path, base_commit: str) -> None: ...

    def assert_clean_base(self, workspace: Path, base_commit: str) -> None: ...
```

`create_inference()` 和 `create_grading()` 必须返回两个不同路径。

##### `zzcode/evaluation/execution/docker_runner.py`

```python
class DockerRunner:
    def create(self, environment_id, limits) -> ContainerHandle: ...

    def exec(self, container, command, timeout) -> CommandResult: ...

    def copy_in(self, container, source, destination) -> None: ...

    def cleanup(self, container) -> None: ...
```

它只封装容器生命周期，不判断测试是否代表 F2P/P2P。

##### `zzcode/evaluation/grading/safety.py`

```python
def inspect_patch(patch: str, policy: SafetyPolicy) -> SafetyResult:
    """检查受保护路径、路径逃逸、测试删除和超范围修改。"""
```

##### `zzcode/evaluation/execution/patch_applier.py`

```python
class PatchApplier:
    def check(self, workspace: Path, patch: str) -> PatchApplyResult: ...

    def apply(self, workspace: Path, patch: str) -> PatchApplyResult: ...
```

它返回结构化 `APPLIED / INVALID / CONFLICT`，不要直接返回一段难以解析的 shell 文本。

##### `zzcode/evaluation/execution/test_executor.py`

```python
class TestExecutor:
    def inject_test_patch(self, workspace, test_patch_path) -> None: ...

    def run_fail_to_pass(self, workspace, test_ids, timeout) -> TestRun: ...

    def run_pass_to_pass(self, workspace, test_ids, timeout) -> TestRun: ...
```

这个模块调用 Docker/pytest 并生成 JUnit XML，但不决定 FULL/PARTIAL/NO。

##### `zzcode/evaluation/execution/log_parser.py`

```python
def parse_junit(path: Path) -> list[TestCaseResult]: ...

def reconcile_expected_tests(
    expected_ids: tuple[str, ...],
    observed: list[TestCaseResult],
) -> TestGroupResult: ...
```

`reconcile_expected_tests()` 用于识别 `NOT_RUN`、collection error 或测试 ID 不匹配。

##### `zzcode/evaluation/grading/status.py`

```python
class ResolvedStatus(StrEnum):
    FULL = "FULL"
    PARTIAL = "PARTIAL"
    NO = "NO"
    AGENT_ERROR = "AGENT_ERROR"
    INFRA_ERROR = "INFRA_ERROR"
```

##### `zzcode/evaluation/grading/grader.py`

```python
def grade(
    task: TaskInstance,
    f2p: TestGroupResult,
    p2p: TestGroupResult,
    safety: SafetyResult,
) -> EvaluationResult:
    if not safety.passed:
        return unresolved(task, "SAFETY_VIOLATION")
    if f2p.rate == 1.0 and p2p.rate == 1.0:
        return resolved(task, ResolvedStatus.FULL)
    if 0.0 < f2p.rate < 1.0 and p2p.rate == 1.0:
        return resolved(task, ResolvedStatus.PARTIAL)
    return unresolved(task, "TEST_FAILURE")
```

##### `zzcode/evaluation/reporting/artifacts.py`

```python
class ArtifactStore:
    def start_run(self, manifest: RunManifest) -> RunPaths: ...

    def write_agent_result(self, result: AgentRunResult) -> None: ...

    def append_prediction(self, prediction: Prediction) -> None: ...

    def write_instance_result(self, result: EvaluationResult) -> None: ...
```

所有文件应先写临时文件再原子替换，避免中断留下半个 JSON。

##### `zzcode/evaluation/reporting/aggregate.py`

```python
def aggregate(results: list[EvaluationResult]) -> BenchmarkSummary:
    """计算 Pass@1、Partial、F2P/P2P、执行漏斗和错误数量。"""
```

##### `zzcode/evaluation/reporting/markdown.py`

```python
def render_report(
    manifest: RunManifest,
    summary: BenchmarkSummary,
    results: list[EvaluationResult],
) -> str: ...
```

##### `zzcode/evaluation/adapters/swebench.py`

```python
def from_swebench_instance(row: dict) -> TaskInstance: ...

def to_swebench_prediction(prediction: Prediction) -> dict: ...

def import_swebench_report(path: Path) -> list[EvaluationResult]: ...
```

该文件只做兼容转换。官方 SWE-bench 的 Docker、repo test spec 和 Grader 仍由官方 Harness 执行。

#### 3.11.4 顶层数据和配置文件分别包含什么

##### `evaluation/configs/benchmark-v1.yaml`

定义“跑哪套题、跑哪个 split、在哪里保存结果”：

```yaml
dataset: evaluation/datasets/zzcode-bench-v1
split: test
max_workers: 1
run_root: evaluation/runs
report_root: evaluation/reports
```

##### `evaluation/configs/agent-zzcode.yaml`

定义 Agent 和真实模型预算：

```yaml
agent: zzcode
provider: openai
model: provider/model-name
temperature: 0.0
max_steps: 30
max_new_tokens: 8192
timeout_seconds: 900
network: disabled
```

密钥只从环境变量读取，不写入 YAML，也不能写入运行日志。

##### `evaluation/configs/release-gates.yaml`

定义结果是否达到发布或展示标准：

```yaml
min_pass_at_1: 0.30
min_patch_apply_rate: 0.90
min_p2p_preservation_rate: 0.95
max_safety_violation_rate: 0.0
max_infrastructure_errors: 0
```

第一版可以只报告 Gate，不自动阻断代码发布。

##### `evaluation/datasets/zzcode-bench-v1/manifest.jsonl`

一行一个 public Repo Task：

```jsonl
{"instance_id":"ZZCODE-BUG-001","repo":"local/zzcode","base_commit":"abc123","problem_statement_path":"instances/ZZCODE-BUG-001/problem_statement.md","environment_id":"zzcode-py313-v1"}
```

##### `evaluation/datasets/zzcode-bench-v1/instances/ZZCODE-BUG-001/task.json`

保存单任务 public metadata：

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

##### `evaluation/datasets/zzcode-bench-v1/instances/ZZCODE-BUG-001/problem_statement.md`

只写 Agent 应当知道的问题、复现条件和用户可见要求，不写测试名或实现答案：

```markdown
# File summary remains stale after external edits

When a previously summarized file is changed outside the agent, a resumed
session may continue to use the old summary. Update zzcode so stale summaries
are not reused while preserving valid summaries for unchanged files.
```

##### `evaluation/datasets/zzcode-bench-v1/private/ZZCODE-BUG-001/grading.json`

保存私有评分规则：

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

##### `evaluation/environments/zzcode-py313/Dockerfile`

固定 OS、Python、Git 和测试工具版本。它不包含任何模型密钥或私有任务数据。

##### `evaluation/environments/zzcode-py313/setup.sh`

安装锁定依赖并验证仓库可导入。环境镜像构建时运行。

##### `evaluation/environments/zzcode-py313/evaluate.sh`

在评分容器中执行测试命令、生成 JUnit XML，并将原始退出码保留下来。它不计算 FULL/PARTIAL/NO。

#### 3.11.5 顶层脚本分别负责哪一步

| 脚本 | 用户什么时候运行 | 内部调用 | 结果 |
|---|---|---|---|
| `validate_eval_dataset.py` | 新增或修改 Task 后 | Dataset Loader、Null/Gold Validator | 数据集认证报告 |
| `run_internal_eval.py` | 运行自建真实模型评测 | Evaluation Runner、ZZCode Adapter | internal run artifacts |
| `run_swebench_inference.py` | 让 zzcode 解官方任务 | SWE-bench Adapter、ZZCode Adapter | 官方 `predictions.jsonl` |
| `import_swebench_results.py` | 官方 Harness 完成后 | SWE-bench report importer | 统一外部结果 |
| `render_eval_report.py` | 已有结果需要重新生成报告 | Aggregate、Markdown Renderer | Markdown 报告 |

用户执行：

```bash
python scripts/run_internal_eval.py \
  --benchmark-config evaluation/configs/benchmark-v1.yaml \
  --agent-config evaluation/configs/agent-zzcode.yaml
```

调用顺序应当是：

```text
run_internal_eval.py
        ↓
load configs
        ↓
EvaluationDataset.load(...)
        ↓
ZZCodeAgentAdapter(...)
        ↓
EvaluationRunner.run(...)
        ↓
ArtifactStore + Aggregate + Markdown Report
```

#### 3.11.6 文件边界检查清单

实现时用下面的问题判断代码是否放错文件：

| 问题 | 应该放在哪里 |
|---|---|
| “如何读取一个任务？” | `dataset.py` |
| “任务对象有哪些字段？” | `schema.py` |
| “如何调用真实 zzcode？” | `zzcode_adapter.py` |
| “如何得到 git diff？” | `patch_collector.py` |
| “如何写 predictions.jsonl？” | `prediction.py` |
| “如何从 base commit 创建目录？” | `workspace.py` |
| “如何启动/清理 Docker？” | `docker_runner.py` |
| “patch 是否修改了 hidden tests？” | `safety.py` |
| “如何应用 patch？” | `patch_applier.py` |
| “如何运行 pytest？” | `test_executor.py` |
| “如何解释 JUnit XML？” | `log_parser.py` |
| “什么时候是 FULL？” | `grader.py` |
| “FULL 有哪些合法值？” | `status.py` |
| “如何保存本次运行？” | `artifacts.py` |
| “Pass@1 怎么算？” | `aggregate.py` |
| “Markdown 报告怎么排版？” | `markdown.py` |
| “整个顺序由谁控制？” | `runner.py` |
| “官方 SWE-bench 字段怎么转？” | `adapters/swebench.py` |

---

## 4. 关于 Fake LLM 的最终政策

本方案明确规定：

### 4.1 能力 benchmark 禁止 Fake LLM

以下运行必须使用真实模型客户端：

- internal zzcode-bench 正式运行；
- internal benchmark smoke；
- official SWE-bench inference；
- 所有对外报告中的 Agent Pass@1；
- 所有用于简历的模型能力数据。

不得使用预设 `<tool>` 或 `<final>` 输出冒充 Agent 任务结果。

### 4.2 产品单元测试可以保留 test double

单元测试仍然需要稳定模拟：

- 空输出；
- malformed tool call；
- reasoning-only response；
- HTTP 错误；
- retry limit；
- 特定 checkpoint 状态。

这些测试可以使用 deterministic test double，但需要：

1. 命名为 `ScriptedModelStub` 或 `ModelClientStub`，避免与能力评测混淆；
2. 只出现在 `tests/unit` 或 `tests/integration`；
3. 不进入 benchmark runner；
4. 不产生 Resolved Rate；
5. 报告中明确标记为 product regression。

### 4.3 现有 FakeModel benchmark 的处理

现有 `SCRIPTED_MODEL_OUTPUTS` 不直接迁入新 benchmark runner。其验证意图将被拆分到：

- Harness integration tests；
- Security regression tests；
- Diagnostic experiments；
- 使用真实模型重新定义的 repo tasks。

---

## 5. 最终目标目录

```text
zzcode/
├── zzcode/                              # 产品实现
│   └── evaluation/                      # 新 Evaluation Python 包
│       ├── __init__.py
│       ├── schema.py
│       ├── dataset.py
│       ├── prediction.py
│       ├── errors.py
│       │
│       ├── inference/
│       │   ├── __init__.py
│       │   ├── adapter.py
│       │   ├── zzcode_adapter.py
│       │   └── patch_collector.py
│       │
│       ├── execution/
│       │   ├── __init__.py
│       │   ├── runner.py
│       │   ├── workspace.py
│       │   ├── docker_runner.py
│       │   ├── patch_applier.py
│       │   ├── test_executor.py
│       │   └── log_parser.py
│       │
│       ├── grading/
│       │   ├── __init__.py
│       │   ├── grader.py
│       │   ├── safety.py
│       │   └── status.py
│       │
│       ├── reporting/
│       │   ├── __init__.py
│       │   ├── artifacts.py
│       │   ├── aggregate.py
│       │   └── markdown.py
│       │
│       └── adapters/
│           ├── __init__.py
│           └── swebench.py
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
├── evaluation/                          # L3：数据、环境和运行产物
│   ├── README.md
│   ├── configs/
│   │   ├── benchmark-v1.yaml
│   │   ├── agent-zzcode.yaml
│   │   └── release-gates.yaml
│   │
│   ├── datasets/
│   │   └── zzcode-bench-v1/
│   │       ├── manifest.jsonl
│   │       ├── dataset-card.md
│   │       ├── splits/
│   │       │   ├── dev.txt
│   │       │   └── test.txt
│   │       ├── instances/
│   │       └── private/
│   │
│   ├── environments/
│   │   └── zzcode-py313/
│   │       ├── Dockerfile
│   │       ├── setup.sh
│   │       └── evaluate.sh
│   │
│   ├── tests/                           # L2：Harness 测试
│   │   ├── unit/
│   │   ├── integration/
│   │   └── golden/
│   │
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

代码放在 `zzcode/evaluation/`，数据与运行资产放在顶层 `evaluation/`，避免将运行数据与 Python 模块混在一起。

---

## 6. 各模块详细解释

## Part A：Schema 与领域模型

### A1. `schema.py`

#### 它做什么

定义 Evaluation 系统中所有模块共享的数据对象：

- `TaskInstance`
- `PrivateTestSpec`
- `Prediction`
- `TestCaseResult`
- `EvaluationResult`
- `RunManifest`

#### 为什么需要

如果 Runner、Grader、Reporter 分别直接操作任意字典，很快会出现字段名不一致、缺字段和版本无法迁移的问题。

Schema 是模块之间的合同。

#### 示例

```python
@dataclass(frozen=True)
class TaskInstance:
    schema_version: str
    dataset_version: str
    instance_id: str
    repo: str
    base_commit: str
    problem_statement: str
    environment_id: str
    metadata: dict[str, object]
```

#### 必测内容

- 缺失字段被拒绝；
- 重复 `instance_id` 被拒绝；
- 空 `base_commit` 被拒绝；
- 未知 schema version 被拒绝；
- JSON 序列化再加载后内容一致。

### A2. `prediction.py`

#### 它做什么

负责读写 SWE-bench-compatible prediction：

```json
{
  "instance_id": "ZZCODE-001",
  "model_name_or_path": "gpt-x",
  "model_patch": "diff --git ..."
}
```

#### 为什么单独拆分

Agent 内部可能有 session、trace 和 memory，但 Grader 只应该依赖最终 patch。单独的 Prediction 层可以避免评测器与 zzcode 内部实现耦合。

#### 必测内容

- JSONL 一行一个 Prediction；
- 空 patch 明确标记而不是丢失实例；
- 重复 prediction 被拒绝；
- patch 中换行和 Unicode 可正确保存；
- 导出的字段兼容官方 SWE-bench。

## Part B：Dataset 层

### B1. `dataset.py`

#### 它做什么

- 加载 `manifest.jsonl`；
- 校验 public task；
- 按 split 选择 dev/test；
- 加载 private grading spec；
- 计算数据集 digest；
- 防止 private 信息进入 Agent 输入。

#### Public 与 Private 分离

Agent 可见：

```text
instance_id
repo
base_commit
problem_statement
普通资源限制
```

Agent 不可见：

```text
gold.patch
test.patch
FAIL_TO_PASS 名单
PASS_TO_PASS 名单
expected implementation
```

#### 必测内容

- private 字段不会出现在 inference payload；
- split 引用不存在 instance 时失败；
- public/private instance ID 必须匹配；
- dataset digest 对内容变化敏感；
- task 顺序稳定。

### B2. Dataset Card

每个版本必须有 `dataset-card.md`，记录：

- 任务来源；
- 数据创建日期；
- 仓库和 license；
- 数据规模；
- 任务类型分布；
- 难度定义；
- 已知限制；
- 是否公开过 hidden tests；
- 数据污染风险；
- 版本变更记录。

## Part C：Inference 与 Agent Adapter

### C1. `adapter.py`

#### 它做什么

定义统一接口：

```python
class AgentAdapter(Protocol):
    def run(self, task, workspace, run_config) -> AgentRunResult:
        ...
```

#### 为什么需要 Adapter

Evaluation 系统不应只支持 zzcode。以后可以比较：

- 不同 zzcode commit；
- 不同模型；
- 不同 prompt；
- 其他 Agent。

它们最终都转换成相同 Prediction。

### C2. `zzcode_adapter.py`

#### 它做什么

1. 使用真实 Provider 配置创建 zzcode；
2. 将 `problem_statement` 作为任务输入；
3. 在任务 workspace 内运行 Agent；
4. 限制 tool steps、timeout 和 token；
5. 记录 session、trace、模型用量；
6. 任务结束后导出 patch。

#### 输入

```text
TaskInstance
Base workspace
Model/provider config
Resource limits
```

#### 输出

```text
AgentRunResult
├── completion status
├── trajectory path
├── token usage
├── latency
├── tool steps
└── prediction
```

#### 真实模型要求

- Provider、model、temperature、token budget 必须写入 run manifest；
- 正式结果不能回退到 `FakeModelClient`；
- 模型初始化失败应判定为 infrastructure/config error；
- 模型返回空结果应判定为 agent failure。

### C3. `patch_collector.py`

#### 它做什么

任务结束后运行：

```bash
git diff --binary --no-ext-diff
```

将 diff 保存为 `patch.diff`，并构造 Prediction。

#### 为什么不能直接用 Agent 最终文本

Agent 可能说“已完成”，但文件没有变化；也可能修改了文件但最终回答没有包含 patch。`git diff` 是更稳定、更可执行的提交接口。

#### 必测内容

- 空 diff；
- 新文件；
- 删除文件；
- 二进制文件；
- 非 UTF-8 输出；
- 修改 `.git` 或受保护目录时拒绝提交。

## Part D：Workspace 与执行环境

### D1. `workspace.py`

#### 它做什么

为每个任务准备独立工作区：

1. 获取仓库快照；
2. checkout 到 `base_commit`；
3. 清理未跟踪文件；
4. 验证 commit hash；
5. 为 inference 和 grading 分别创建副本。

#### 两个环境必须分离

```text
Inference Workspace
Agent 可读写，只用于生成 patch

Grading Workspace
从 base commit 重新创建，只应用最终 patch 和 test patch
```

这样 Agent 无法通过修改测试环境制造假通过。

### D2. `docker_runner.py`

#### 它做什么

- 创建容器；
- 设置 CPU、内存、进程数和 timeout；
- 默认关闭网络；
- 将工作区放入固定路径；
- 执行 setup/test 命令；
- 捕获 stdout/stderr/exit code；
- 确保超时后清理容器。

#### 第一版保持简单

第一版只支持一个 zzcode Python 环境，不实现通用多语言镜像系统。

```text
python:3.13-slim
+ git
+ locked Python dependencies
+ pytest/ruff
```

#### 必测内容

- 正常退出；
- 非零退出；
- timeout；
- 容器被强制终止后清理；
- 网络关闭；
- 环境变量 allowlist；
- 只允许指定 mount。

### D3. `patch_applier.py`

#### 它做什么

在干净 grading workspace 中应用 candidate patch。

建议顺序：

```text
git apply --check
git apply
```

第一版不使用高 fuzz 自动“修复”非法 patch，避免评测器替 Agent 纠错。

#### 输出分类

```text
APPLIED
EMPTY_PATCH
INVALID_PATCH
PATCH_CONFLICT
PROTECTED_PATH_MODIFIED
```

#### 必测内容

- 合法 patch；
- 上下文冲突；
- 路径逃逸；
- 修改 hidden tests；
- 修改 `.git`；
- patch 中包含符号链接。

### D4. `test_executor.py`

#### 它做什么

1. 应用 private test patch；
2. 执行 F2P 测试；
3. 执行 P2P 测试；
4. 可选运行 build/compile；
5. 保存完整日志。

不要只依赖一次完整 `pytest` 的总退出码，因为 Grader 需要知道具体哪些 F2P/P2P 通过。

### D5. `log_parser.py`

#### 它做什么

把 pytest 原始输出转换为结构化测试结果：

```python
TestCaseResult(
    test_id="...",
    status="passed | failed | error | skipped",
    duration_ms=123,
)
```

#### 实现建议

优先让 pytest 生成 JUnit XML 或 JSON report，再解析结构化文件。不要只用正则解析终端点号输出。

#### 必测内容

- pass/fail/error/skip；
- collection error；
- process crash；
- timeout；
- 空报告；
- duplicate test ID。

## Part E：Grading 与安全

### E1. `status.py`

定义统一状态：

```text
FULL
PARTIAL
NO
AGENT_ERROR
INFRA_ERROR
```

内部再记录详细 failure type，而不是把所有失败压成一个布尔值。

### E2. `grader.py`

#### 核心公式

```text
F2P Rate
= 通过的 FAIL_TO_PASS / FAIL_TO_PASS 总数

P2P Rate
= 通过的 PASS_TO_PASS / PASS_TO_PASS 总数
```

#### 判定

```text
FULL
F2P == 1.0 AND P2P == 1.0 AND safety pass

PARTIAL
0 < F2P < 1.0 AND P2P == 1.0 AND safety pass

NO
其他情况
```

#### 分母政策

- Agent timeout：计入 Agent 未解决；
- 空 patch：计入 Agent 未解决；
- patch apply failure：计入 Agent 未解决；
- 测试正常执行但失败：计入 Agent 未解决；
- Harness 自身崩溃：记为 INFRA_ERROR，单独报告；
- 数据集定义错误：该 instance 无效，阻止正式报告发布。

### E3. `safety.py`

第一版检查：

- 是否修改 private/hidden test 路径；
- 是否修改 `.git`、`.env` 或 secrets；
- 是否访问父目录；
- 是否修改 allowed scope 外文件；
- 是否删除现有测试；
- 是否超过文件数和 diff LOC 限制；
- 是否发生网络访问；
- 是否超过资源限制。

安全结果作为独立 Gate，不能被高 F2P 抵消。

## Part F：Artifacts 与 Reporting

### F1. `artifacts.py`

每个 run 保存：

```text
evaluation/runs/<run_id>/
├── run_manifest.json
├── predictions.jsonl
├── instance_results.jsonl
├── results.json
└── instances/
    └── ZZCODE-001/
        ├── task.json
        ├── agent.log
        ├── trajectory.jsonl
        ├── patch.diff
        ├── patch_apply.log
        ├── test_output.log
        ├── junit.xml
        └── report.json
```

`run_manifest.json` 至少记录：

- dataset version 与 digest；
- Agent commit；
- model/provider；
- temperature/token budget；
- max steps 与 timeout；
- Docker image digest；
- OS/architecture；
- run start/end time；
- runner version。

### F2. `aggregate.py`

聚合指标：

#### 主指标

- Pass@1 / Full Resolved Rate
- Partial Resolved Rate
- F2P Pass Rate
- P2P Preservation Rate
- Patch Apply Rate

#### 执行漏斗

- Instances Total
- Agent Completed
- Patch Generated
- Patch Applied
- Tests Completed
- Fully Resolved

#### 效率指标

- Tool Steps：平均、P50、P95
- Latency：平均、P50、P95
- Input/Output/Cached Tokens
- Files Changed
- Added/Deleted LOC
- Cost per Instance
- Cost per Resolved Instance

#### 可靠性指标

- Agent Timeout Count
- Infrastructure Error Count
- Test Timeout Count
- Safety Violation Rate

### F3. `markdown.py`

生成面试可读报告：

```text
1. Run Configuration
2. Dataset Distribution
3. Primary Results
4. Results by Category
5. Execution Funnel
6. Efficiency
7. Failure Taxonomy
8. Safety
9. Known Limitations
10. Reproduction Commands
```

## Part G：SWE-bench Adapter

### G1. `adapters/swebench.py`

#### 它做什么

将官方 SWE-bench instance 转换成内部 `TaskInstance`，并将 zzcode 的结果导出为官方 Prediction。

字段映射：

```text
官方字段                     内部字段
instance_id        →         instance_id
repo               →         repo
base_commit        →         base_commit
problem_statement  →         problem_statement
FAIL_TO_PASS       →         fail_to_pass
PASS_TO_PASS       →         pass_to_pass
```

#### 不做什么

- 不复制官方 Docker Harness；
- 不重新实现官方 repo-specific test spec；
- 不修改官方评分结果；
- 不把官方 gold patch 传给 Agent。

### G2. 官方评测流程

```text
读取官方 instances
        ↓
运行 zzcode inference
        ↓
输出 predictions.jsonl
        ↓
调用官方 run_evaluation
        ↓
读取官方 results
        ↓
转换为内部统一报告
```

第一版只跑：

- 3～5 个 smoke instances；
- 成功后扩展到 10～20 个 Verified pilot。

---

## 7. 自建数据集设计

## 7.1 第一版规模

建议 8 个高质量任务，而不是为了数量做大量简单文本替换：

| 类型 | 数量 | 说明 |
|---|---:|---|
| Bug Fix | 4 | 真实异常、边界条件、状态错误 |
| Feature/API | 2 | 新增可验证行为 |
| Multi-file Integration | 1 | 跨模块接口与持久化 |
| Refactor/Compatibility | 1 | 行为保持或兼容升级 |

标签交叉要求：

- 至少 3 个需要定位未知文件；
- 至少 3 个包含非显然边界条件；
- 至少 2 个跨文件；
- 至少 2 个涉及状态或持久化；
- 至少 1 个涉及 provider/parser；
- 至少 1 个涉及 CLI/config。

## 7.2 任务来源优先级

优先级从高到低：

1. 真实历史 Issue + PR/commit；
2. 真实发现但尚未公开的 bug；
3. 基于真实需求构造的 feature task；
4. 人工 mutation task，仅用于补足边界覆盖。

不要将简单 README 替换作为主能力任务。它们可以保留为 smoke task。

## 7.3 Public Task 示例

```json
{
  "schema_version": "1.0",
  "dataset_version": "zzcode-bench-v1",
  "instance_id": "ZZCODE-BUG-001",
  "repo": "local/zzcode",
  "base_commit": "<sha>",
  "problem_statement": "File summaries remain stale after an out-of-band edit.",
  "environment_id": "zzcode-py313-v1",
  "task_type": "bug_fix",
  "scope": "multi_file",
  "subsystems": ["memory", "runtime"],
  "difficulty": "medium",
  "resource_limits": {
    "timeout_seconds": 900,
    "max_tool_steps": 30,
    "memory_mb": 4096,
    "cpus": 2
  }
}
```

## 7.4 Private Grading 示例

```json
{
  "instance_id": "ZZCODE-BUG-001",
  "gold_patch": "gold.patch",
  "test_patch": "test.patch",
  "FAIL_TO_PASS": [
    "hidden_tests/test_file_freshness.py::test_out_of_band_edit_invalidates_summary"
  ],
  "PASS_TO_PASS": [
    "tests/unit/test_memory.py",
    "tests/unit/test_context_manager.py"
  ],
  "test_command": "pytest -q --junitxml=/tmp/result.xml",
  "timeout_seconds": 300
}
```

## 7.5 任务验收门禁

一个任务只有同时满足以下条件才能进入 test split：

- [ ] base commit 可以获取；
- [ ] 环境可以离线构建；
- [ ] Null Patch 至少有一个 F2P 失败；
- [ ] Null Patch 的 P2P 全部通过；
- [ ] Gold Patch 可以 clean apply；
- [ ] Gold Patch 的 F2P/P2P 全部通过；
- [ ] 重复运行三次结果一致；
- [ ] hidden test 不依赖 gold patch 的具体代码结构；
- [ ] problem statement 不泄漏答案；
- [ ] Agent workspace 不包含 private 数据；
- [ ] 人工审核认为任务清晰且可解；
- [ ] 记录来源、license 和创建时间。

---

## 8. 实现顺序与依赖关系

必须按下面顺序实施。后一个阶段依赖前一个阶段的稳定接口。

## Phase 0：冻结现状并建立测试地图

### 目标

在不移动、不删除旧测试的前提下，记录当前状态。

### 工作

1. 收集现有测试 node IDs；
2. 记录测试结果与耗时；
3. 将测试按 product/harness/diagnostic/security 分类；
4. 记录当前 benchmark 的 12 个任务；
5. 建立旧测试到新结构的迁移表；
6. 保存基线，不在这一阶段修产品行为。

### 产物

```text
docs/testing/current-test-map.md
docs/testing/migration-matrix.md
artifacts/test-baseline.json
```

### Gate

- 每个现有测试都有迁移目标；
- 每个旧 benchmark 都有新去向；
- 用户确认迁移矩阵后再开始代码重构。

## Phase 1：实现 Schema、Dataset 和 Prediction

### 目标

先固定接口，不执行模型和 Docker。

### 实现顺序

1. `schema.py`
2. `prediction.py`
3. `dataset.py`
4. JSON/JSONL 序列化
5. dataset digest
6. public/private 隔离测试

### Gate

- 能加载一个最小 task；
- 能拒绝错误 task；
- 能写出官方兼容 prediction；
- private 数据不会进入 inference payload。

## Phase 2：实现 Artifacts 与状态模型

### 目标

先确定“每次运行保存什么”，再实现执行器。

### 实现顺序

1. Run ID 与目录规则；
2. `RunManifest`；
3. `AgentRunResult`；
4. `EvaluationResult`；
5. JSONL append；
6. 原子写入与中断恢复。

### Gate

- 任意失败阶段都能留下可诊断产物；
- run manifest 可复现实验配置；
- 同一 instance 重跑不会覆盖旧结果。

## Phase 3：实现本地 Workspace、Patch 和 Test Executor

### 目标

先不用真实 Agent，使用人工 patch 验证执行链路。

### 实现顺序

1. base commit workspace；
2. grading workspace；
3. patch apply；
4. private test patch 注入；
5. pytest JUnit 输出；
6. log parser；
7. timeout/error 分类。

### 测试输入

只使用人工准备的：

- Null Patch；
- Gold Patch；
- 一个故意错误 patch；
- 一个无法应用的 patch。

这不是 Fake LLM，而是在测试 Evaluation Harness 本身。

### Gate

- Null/Gold/Error/Conflict 四条路径全部正确；
- test ID 解析稳定；
- grading 环境始终从干净 base 创建。

## Phase 4：加入 Docker 隔离

### 目标

将 Phase 3 的执行从宿主机迁到固定容器。

### 实现顺序

1. 固定 Dockerfile；
2. lock dependencies；
3. image digest；
4. 无网络运行；
5. 资源限制；
6. mount allowlist；
7. timeout cleanup。

### Gate

- 同一个 Gold Patch 连续三次结果一致；
- 网络不可用；
- timeout 后没有残留容器；
- 输出包含 image digest。

## Phase 5：实现真实模型 zzcode Agent Adapter

### 目标

首次让真实模型生成 patch。

### 实现顺序

1. 定义 `AgentAdapter`；
2. 接入当前 CLI/runtime；
3. 加载真实 provider；
4. 将 problem statement 传给 Agent；
5. 记录 tool steps、tokens、latency；
6. 收集 `git diff`；
7. 输出 Prediction。

### Gate

- runner 中不存在 FakeModel fallback；
- 真实模型配置写入 manifest；
- 空 patch、超时和模型错误正确分类；
- patch 可以交给 Phase 4 的 Grader，而不依赖 Agent 内部状态。

## Phase 6：构造 2 个 Vertical Slice Task

### 目标

用最少数据跑通完整链路，而不是立即制作 8 个任务。

### Task 选择

1. 一个 single-file bug fix；
2. 一个 multi-file state bug。

### 对每个 Task 执行

```text
Null Validation
Gold Validation × 3
Real Agent Inference
Agent Patch Evaluation
Report Generation
```

### Gate

- 两个任务都能产生完整 artifacts；
- Gold 均为 FULL；
- Null 均为 NO；
- Agent 结果无论成功失败都能正确报告。

## Phase 7：扩展为 8 个 Internal Verified Tasks

### 目标

形成可用于面试的第一版数据集。

### 工作

- 扩展到 8 个任务；
- 完成 dev/test split；
- 编写 dataset card；
- 完成任务三次稳定性验证；
- 冻结 `zzcode-bench-v1` digest；
- 跑一次正式 Pass@1。

### Gate

- 8/8 Gold FULL；
- 8/8 Null 非 FULL；
- 0 个不稳定任务；
- 正式运行没有 private leakage；
- 生成 internal benchmark report。

## Phase 8：实现官方 SWE-bench Adapter

### 目标

复用真实模型 Agent Adapter，但使用官方任务和官方 Grader。

### 实现顺序

1. 官方 dataset loader；
2. 字段映射；
3. 官方 workspace 准备；
4. prediction export；
5. 官方 CLI invocation；
6. 官方结果 import；
7. 统一报告转换。

### 运行顺序

1. 单个 gold instance 验证官方环境；
2. 单个 zzcode prediction；
3. 3～5 个 smoke；
4. 10～20 个 Verified pilot。

### Gate

- predictions 通过官方格式校验；
- 官方 Harness 能消费结果；
- external 结果与 internal 结果分表展示；
- 不修改官方 grader 口径。

## Phase 9：提取并迁移现有 Benchmark

这一阶段在新系统稳定后进行，避免旧设计反向限制新架构。

具体迁移见第 9 节。

## Phase 10：最后迁移现有测试

按照用户要求，现有测试在新 Evaluation 主链路完成后统一整合。

具体迁移见第 10 节。

## Phase 11：CI、文档与面试材料

### CI 层级

```text
Pull Request
Product unit + integration + security
Evaluation unit + one golden smoke

Nightly / Manual
Full internal zzcode-bench

Release / Evidence Freeze
Internal benchmark + official SWE-bench pilot
```

### Gate

- 一条命令运行 product tests；
- 一条命令验证 dataset；
- 一条命令运行 internal eval；
- 一条命令导出 SWE-bench predictions；
- README 提供从零复现步骤。

---

## 9. 现有 12 个 Benchmark 的提取与整合

现有 benchmark 不能整体原样搬入新 Agent capability benchmark，因为其中一些任务依赖预设模型犯错、人工注入 checkpoint 或 FakeModel 输出。正确做法是保留其验证意图，并迁移到合适层级。

## 9.1 迁移矩阵

| 现有任务 | 当前验证意图 | 新去向 | 是否计入 Pass@1 | 是否使用真实模型 |
|---|---|---|---:|---:|
| `readme_intro_locked` | 基本 patch 链路 | real-model smoke task | 否，作为 smoke | 是 |
| `readme_schema_note` | README patch | real-model smoke task | 否，作为 smoke | 是 |
| `sample_beta_locked` | 单文件 patch | real-model smoke task | 否，作为 smoke | 是 |
| `sample_gamma_locked` | 单文件 patch | real-model smoke task | 否，作为 smoke | 是 |
| `invalid_patch_recovery` | malformed tool 后恢复 | product integration test | 否 | 使用 stub 模拟边界 |
| `path_escape_recovery` | 路径逃逸后恢复 | security test + optional real safety eval | 否 | 安全 eval 可用真实模型 |
| `repeated_read_recovery` | 重复调用防护 | product integration/diagnostic | 否 | unit/integration 使用 stub |
| `context_reduction_checkpoint` | 压缩时 checkpoint | context diagnostic | 否 | 可另做真实模型实验 |
| `freshness_reanchor_resume` | stale file 恢复 | resume diagnostic | 否 | 可另做真实模型实验 |
| `workspace_mismatch_resume` | workspace drift | resume diagnostic | 否 | 可另做真实模型实验 |
| `durable_promotion_accept` | durable memory 接受 | memory integration test | 否 | stub 或人工状态即可 |
| `durable_promotion_reject` | secret/transient 拒绝 | memory/security test | 否 | stub 或人工状态即可 |

## 9.2 为什么不能全部改成真实 Agent capability task

例如 `invalid_patch_recovery` 当前依赖脚本强制模型先发出一个缺字段的 tool call。真实模型不一定会犯这个特定错误，因此无法稳定比较不同 Agent。

它真正要验证的是：

```text
当 malformed tool call 已经发生时，runtime 能否安全恢复
```

这是产品 integration test，而不是未知任务能力评测。

同样，人工注入 checkpoint 的任务主要验证 resume 机制，不应该进入 SWE-bench 式 patch resolved rate。

## 9.3 移除 FakeModel benchmark 路径

新系统稳定后：

1. 停止从正式 benchmark CLI 调用 `SCRIPTED_MODEL_OUTPUTS`；
2. 将脚本化场景移动为测试 fixture；
3. 将旧 `BenchmarkEvaluator` 标记 deprecated；
4. 正式报告只读取新 `evaluation/runs`；
5. 如果旧 artifacts 需要保留，移动到 `artifacts/legacy/` 并注明历史口径；
6. 删除或冻结旧 provider experiment 对 scripted benchmark 的依赖；
7. 所有 real-model benchmark 显式要求 provider/model 配置。

## 9.4 新旧指标不能混写

旧指标：

```text
Scripted Harness Pass Rate
Context Compression
Memory Repeated Reads
Resume Detection
```

新指标：

```text
Agent Pass@1
F2P Pass Rate
P2P Preservation Rate
Patch Apply Rate
```

报告中必须分章节，不能将旧的 12/12 scripted pass 宣称为真实模型解决率。

---

## 10. 现有测试最终整合方案

现有测试在 Phase 10 统一迁移，先建立公共 fixture，再按职责搬迁。

## 10.1 目标结构

```text
tests/
├── README.md
├── conftest.py
├── factories.py
│
├── unit/
│   ├── test_task_state.py
│   ├── test_run_store.py
│   ├── test_memory.py
│   ├── test_context_manager.py
│   ├── test_openai_parser.py
│   └── test_anthropic_parser.py
│
├── integration/
│   ├── test_agent_loop.py
│   ├── test_tool_protocol.py
│   ├── test_session.py
│   ├── test_resume.py
│   ├── test_checkpoint.py
│   ├── test_artifacts.py
│   ├── test_cli.py
│   └── test_provider_requests.py
│
├── security/
│   ├── test_path_boundaries.py
│   ├── test_secret_redaction.py
│   ├── test_shell_environment.py
│   └── test_delegation.py
│
└── diagnostics/
    ├── test_context_ablation.py
    ├── test_memory_ablation.py
    └── test_recovery_ablation.py
```

## 10.2 文件级迁移

| 现有文件 | 新位置/拆分 |
|---|---|
| `test_task_state.py` | `tests/unit/test_task_state.py` |
| `test_run_store.py` | `tests/unit/test_run_store.py` |
| `test_memory.py` | `tests/unit/test_memory.py` |
| `test_context_manager.py` | `tests/unit/test_context_manager.py` |
| `test_safety_invariants.py` | 拆到 `tests/security/*` |
| `test_evaluator.py` | 旧逻辑冻结；意图迁到 `evaluation/tests/*` |
| `test_metrics.py` | 拆到 `tests/diagnostics/*` 和新 reporting tests |
| `test_zzcode.py` | 按 Agent/Tool/Session/Provider/CLI/Artifact 拆分 |

## 10.3 公共 Fixture

集中管理：

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

不要让 benchmark runner 引入 `scripted_model_stub`。

## 10.4 pytest markers

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

## 10.5 推荐命令

```bash
# 每次提交
pytest -m "unit or integration or security"

# Evaluation Harness，不调用真实模型
pytest evaluation/tests -m "eval_harness and not real_model"

# Docker smoke
pytest evaluation/tests -m docker

# 真实模型 smoke，显式执行
pytest evaluation/tests -m real_model

# 诊断实验
pytest -m diagnostic
```

## 10.6 迁移安全规则

1. 每次只迁移一个领域；
2. 先复制到新位置并验证，再删除旧文件；
3. 保持旧 test node 与新 test node 的映射表；
4. 不在“移动测试”的同一个 commit 中修改产品行为；
5. 删除重复测试必须说明覆盖由哪个新测试承接；
6. 所有旧测试必须是 migrated、merged 或 intentionally retired 三种状态之一。

---

## 11. Evaluation Harness 自身测试计划

## 11.1 Unit Tests

| 模块 | 测试内容 |
|---|---|
| Schema | 缺字段、版本、序列化、重复 ID |
| Dataset | split、digest、private leakage |
| Prediction | JSONL、空 patch、Unicode、重复记录 |
| Patch | apply/check/conflict/protected path |
| Parser | pass/fail/error/skip/collection error |
| Grader | FULL/PARTIAL/NO/零分母 |
| Reporting | 聚合、百分比、P50/P95、空集合 |
| Safety | path、test deletion、diff scope |

## 11.2 Integration Tests

- 一个 fixture repo 的完整 Null Patch 流程；
- 一个 fixture repo 的完整 Gold Patch 流程；
- 一个 invalid patch；
- 一个 timeout；
- 一个测试 collection error；
- 一个 P2P regression；
- 一个 partial F2P；
- 中断后 artifacts 保留；
- 相同 run 不覆盖；
- inference/private 路径隔离。

## 11.3 Golden Tests

Golden Test 用已知输入验证整条 Evaluation 链路：

```text
Null Patch → NO
Gold Patch → FULL
Partial Patch → PARTIAL
Regression Patch → NO
```

Golden Test 不需要 LLM，因为它测试的是 Harness，而不是 Agent。

## 11.4 Real Model Smoke

Real Model Smoke 使用真实模型，但只跑 1～2 个简单任务，目标是发现：

- provider 配置失效；
- Agent 无法启动；
- tool contract 变化；
- patch 无法导出；
- token/usage metadata 丢失。

它不作为稳定 CI，必须显式触发。

---

## 12. 指标定义与报告规则

## 12.1 主要指标

```text
Pass@1
= FULL instances / valid evaluated instances

F2P Pass Rate
= passed F2P tests / total F2P tests

P2P Preservation Rate
= passed P2P tests / total P2P tests

Patch Apply Rate
= successfully applied patches / submitted non-empty patches
```

只有一次独立 Agent 提交时使用 Pass@1，不使用 pass@k。

## 12.2 执行漏斗

```text
Total
→ Submitted
→ Agent Completed
→ Patch Generated
→ Patch Applied
→ Tests Completed
→ FULL
```

漏斗可以快速区分问题发生在模型、工具、patch 还是测试阶段。

## 12.3 分类指标

按以下维度分组：

- task type；
- single/multi-file；
- subsystem；
- difficulty；
- failure type。

小数据集上必须同时报告分子/分母，例如 `3/8 = 37.5%`，不能只报告百分比。

## 12.4 Failure Taxonomy

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

## 12.5 不允许的指标表达

- 不把 scripted harness 通过率写成 Agent Resolved Rate；
- 不把 infrastructure error 默认当作模型失败；
- 不把 diagnostic ablation 与 Pass@1 加权；
- 不在 N 很小时只写百分比；
- 不比较使用不同任务、预算或模型的 run 而不披露差异；
- 不将 dev split 调参结果当作 held-out test 结果。

---

## 13. CI 与运行策略

## 13.1 PR CI

目标：快、确定、无真实模型费用。

```text
Product unit
Product integration
Product security
Evaluation unit
One local golden fixture
Ruff
```

## 13.2 Docker CI / Nightly

```text
Docker golden validation
Dataset validation
Null/Gold revalidation
Container cleanup checks
```

## 13.3 Real Model Evaluation

不在普通 PR 自动运行。触发条件：

- 手动 workflow；
- nightly 且有预算；
- release candidate；
- 面试数据冻结。

运行时必须保存完整 manifest。

## 13.4 Official SWE-bench

- 本地只做 1～5 个 smoke；
- 正式 pilot 使用稳定 x86 Linux 或远程环境；
- 固定官方 SWE-bench 版本；
- 保存官方原始结果，不只保存二次聚合报告。

---

## 14. 建议时间表

这是一个“近期完整实现”而非大平台建设，建议按 10 个工作日安排。

| 日期 | 主要工作 | 交付物 |
|---|---|---|
| Day 1 | 现状冻结、测试地图、迁移矩阵 | test map、baseline |
| Day 2 | Schema、Dataset、Prediction | 核心数据接口与 unit tests |
| Day 3 | Artifacts、状态与错误分类 | run manifest、result schema |
| Day 4 | Workspace、Patch、Test Executor | 本地 Null/Gold 链路 |
| Day 5 | Docker 与隔离、安全边界 | container golden test |
| Day 6 | Real zzcode Agent Adapter | 真实模型输出 patch |
| Day 7 | 2 个 vertical slice tasks | 完整端到端报告 |
| Day 8 | 扩展到 8 个 internal tasks | zzcode-bench-v1 |
| Day 9 | SWE-bench Adapter 与 3～5 smoke | predictions + official results |
| Day 10 | 旧 benchmark/测试迁移与面试材料 | 最终结构、架构图、README |

如果时间不足，最低完整版本必须包含：

```text
Schema
2 个 Verified Tasks
Null/Gold/Agent
Docker
F2P/P2P
Artifacts
Real Model
1 个官方 SWE-bench Smoke
测试迁移矩阵
```

可以推迟：

- 从 2 个任务扩展到 8 个；
- 完整官方 pilot；
- P95/cost 等丰富指标；
- 深度拆分所有旧测试文件。

---

## 15. 风险与对策

## 15.1 数据泄漏

风险：Agent 读取 gold/test patch。

对策：

- public/private 物理分离；
- inference 容器只挂载 public task；
- grading 使用全新 workspace；
- 测试 private path 不出现在 prompt 和 trace。

## 15.2 任务过于简单

风险：README 字符串替换导致结果没有说服力。

对策：

- 简单任务只作为 smoke；
- 正式数据至少包含多文件、状态、边界和 parser；
- 报告任务分布和 gold patch 规模。

## 15.3 任务偏向作者

风险：自建任务只测量对 zzcode 的熟悉程度。

对策：

- 保留 held-out test split；
- 不使用 test task 调 prompt；
- 增加官方 SWE-bench external pilot；
- 披露数据来源和限制。

## 15.4 Docker 和依赖不稳定

对策：

- lock dependencies；
- 固定 image digest；
- 默认禁网；
- Gold 连续三次验证；
- Infra Error 单独报告。

## 15.5 模型非确定性

对策：

- 记录完整模型配置；
- 第一版报告 Pass@1；
- 固定预算和工具；
- 需要统计稳定性时再增加重复 runs，不伪装成确定结果。

## 15.6 重构旧测试时丢失覆盖

对策：

- 测试迁移矩阵；
- 一次只迁一个领域；
- 先新增后删除；
- 记录 node ID 映射；
- 产品行为修改与测试移动分开提交。

---

## 16. 完成定义（Definition of Done）

## 16.1 架构完成

- [ ] Product、Harness、Benchmark 三层清晰分离；
- [ ] Task/Prediction/Result schema 已版本化；
- [ ] Agent 与 Grader 通过 patch 解耦；
- [ ] Inference 与 Grading workspace 分离；
- [ ] private tests 对 Agent 不可见；
- [ ] 官方 SWE-bench 使用 Adapter 而非重写评分器。

## 16.2 数据完成

- [ ] 至少 8 个 internal verified tasks；
- [ ] 每个任务有 Null/Gold 验证；
- [ ] 每个任务重复运行三次稳定；
- [ ] dataset card 和 digest 已冻结；
- [ ] dev/test split 已建立。

## 16.3 测试完成

- [ ] Harness unit tests；
- [ ] Harness integration tests；
- [ ] Null/Gold golden tests；
- [ ] Docker timeout/cleanup tests；
- [ ] Real model smoke；
- [ ] 现有测试全部 migrated/merged/retired；
- [ ] 现有 benchmark 全部有明确新去向。

## 16.4 报告完成

- [ ] Internal Pass@1；
- [ ] F2P/P2P/Patch Apply；
- [ ] 执行漏斗；
- [ ] Tokens/Latency/Tool Steps；
- [ ] Failure Taxonomy；
- [ ] Safety Violations；
- [ ] Infrastructure Errors；
- [ ] 官方 SWE-bench smoke/pilot 独立结果。

## 16.5 Fake LLM 退出正式 benchmark

- [ ] Internal benchmark 只接受真实 provider；
- [ ] SWE-bench inference 只接受真实 provider；
- [ ] 正式报告不读取 scripted benchmark；
- [ ] FakeModel/test double 仅存在于产品或 Harness 单元测试；
- [ ] 所有历史 scripted artifacts 标记为 legacy。

---

## 17. 面试讲解顺序

建议按下面顺序讲，而不是从文件列表开始：

### 17.1 问题

> 原有 pytest 能验证 Agent runtime，却不能证明 Agent 能解决未知仓库任务；原有 benchmark 又依赖脚本化模型输出，存在能力指标口径混淆。

### 17.2 架构决策

> 我将体系拆为 Product Tests、Evaluation Harness Tests 和 Executable Agent Benchmarks 三层，并使用 patch 作为 Agent 与 Grader 的稳定接口。

### 17.3 执行式评分

> 每个任务固定 base commit，Agent 只接收 issue 和仓库，最终提交 patch；独立 Docker 环境注入隐藏测试，通过 FAIL_TO_PASS 与 PASS_TO_PASS 判断修复和回归。

### 17.4 数据可信度

> 每个任务都经过 Null Patch、Gold Patch 和三次稳定性验证，基础设施错误与 Agent 失败分开统计。

### 17.5 外部有效性

> 同一个 Agent Adapter 可以导出官方 SWE-bench predictions，并用官方 Harness 验证外部任务表现。

### 17.6 结果

最终用真实数据替换占位符：

```text
Internal zzcode-bench
8 tasks, Pass@1 = X/8, P2P = Y%, safety violations = 0

External SWE-bench Verified Pilot
N tasks, Pass@1 = A/N, patch apply rate = B%
```

---

## 18. 简历表达模板

没有真实数据前：

> 重构 Coding Agent 测试体系，将产品回归、Evaluation Harness 和 Agent 能力评测分层；设计 SWE-bench-compatible 的 Task/Patch 接口，在隔离 Docker 中通过 FAIL_TO_PASS 与 PASS_TO_PASS 进行可执行评分。

> 构建版本化 Repo Task 数据集和 Null/Gold/Agent 三重验证流程，自动保存模型配置、执行轨迹、patch、测试日志及分实例结果，并支持导出官方 SWE-bench predictions。

有真实数据后：

> 在 **[N] 个 Internal Verified Tasks** 上取得 **[X%] Pass@1** 和 **[Y%] P2P Preservation Rate**，并在 **[M] 个 SWE-bench Verified Pilot Tasks** 上完成外部基准验证，安全违规率为 **[Z%]**。

---

## 19. 实施时的第一条规则

不要从移动 `tests/` 文件开始，也不要先删除旧 `evaluator.py`。

正确的第一步是：

```text
冻结现状
→ 生成测试地图
→ 确认迁移矩阵
→ 实现最小 Schema
→ 用人工 Null/Gold Patch 跑通 Harness
→ 再接真实模型
→ 最后迁移旧测试和旧 benchmark
```

这样可以避免在新架构尚未可用时，同时失去旧系统提供的回归保护。
