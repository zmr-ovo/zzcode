# zzcode SWE-bench 式 Evaluation MVP 面试说明

## 1. 它是什么

这是一套面向 Coding Agent 的可执行评测系统：让 Agent 在真实仓库中修复问题，再通过测试判断代码是否真的修好。

```text
固定 Repo Task
→ Agent 修改代码
→ 生成 Git Patch
→ 在干净仓库应用 Patch
→ 运行隐藏测试
→ 评分并生成报告
```

zzcode 实现的是内部 SWE-bench 式 MVP，不是直接运行官方 SWE-bench 数据集。

## 2. 核心概念

### Repo Task

一项真实代码修复任务，包含：

- `base_commit`：存在 Bug 的固定代码版本。
- `problem_statement`：Agent 能看到的问题描述。
- `gold.patch`：人工准备的正确修改，Agent 不可见。
- `test.patch`：隐藏测试，Agent 不可见。
- `grading.json`：F2P/P2P 测试选择器。

### Git Patch 与 Prediction

Agent 修改完成后，通过 `git diff` 提取 Patch：

```diff
- return ""
+ raise RuntimeError("输出预算可能不足")
```

然后写入 `predictions.jsonl`，每行对应一道任务：

```json
{"instance_id":"ZZCODE-BUG-001","model_name_or_path":"model-name","model_patch":"diff --git ..."}
```

### 隐藏 F2P/P2P

- F2P（Fail to Pass）：修复前失败、正确修复后通过，检查 Bug 是否修好。
- P2P（Pass to Pass）：修复前通过、修复后仍通过，检查是否破坏原有功能。

每个 Task 有自己的 F2P/P2P，公共回归测试可以复用。

## 3. 目录架构

```text
zzcode/
├── evaluation/
│   ├── datasets/zzcode-bench-v1/
│   │   ├── manifest.jsonl              # 任务索引和 base_commit
│   │   ├── dataset-lock.json           # 固定数据集版本
│   │   ├── splits/
│   │   │   ├── dev.txt                 # 开发调试任务
│   │   │   ├── test.txt                # 正式评分任务
│   │   │   └── all.txt
│   │   └── instances/ZZCODE-BUG-001/
│   │       ├── problem_statement.md    # 公开题目
│   │       └── task.json               # 类型、模块、难度
│   ├── private/zzcode-bench-v1/
│   │   └── ZZCODE-BUG-001/
│   │       ├── gold.patch              # 标准修复
│   │       ├── test.patch              # 隐藏测试
│   │       └── grading.json            # F2P/P2P 选择器
│   ├── tests/                           # Harness 自身测试
│   └── runs/                            # 每次评测结果
│
├── zzcode/evaluation/
│   ├── dataset.py                       # 加载任务
│   ├── schema.py                        # 数据结构
│   ├── prediction.py                    # Prediction 读写
│   ├── inference/                       # 调用 Agent、收集 Patch
│   ├── execution/                       # 工作区、Patch、测试执行
│   ├── grading/                         # 安全检查和评分
│   └── reporting/                       # 保存产物和报告
│
└── scripts/
    ├── run_internal_eval.py             # 评测入口
    ├── validate_eval_dataset.py         # Null/Gold 校验
    └── render_eval_report.py            # 重新生成报告
```

公开题目与 `evaluation/private/` 分离，Agent 不能读取 Gold Patch 和隐藏测试。

## 4. 一项 Repo Task 的完整流程

```text
1. Dataset Loader 读取 manifest.jsonl
   获得 instance_id、base_commit 和题目路径
                         ↓
2. Workspace 创建干净仓库
   checkout 到题目指定的 base_commit
                         ↓
3. Worker 读取 problem_statement.md
   调用 zzcode Agent 分析并修改代码
                         ↓
4. Patch Collector 执行 git diff
   得到 model_patch
                         ↓
5. 保存到 predictions.jsonl
                         ↓
6. Grader 创建新的干净评分仓库
   checkout 相同的 base_commit
                         ↓
7. 应用 model_patch，再注入隐藏 test.patch
                         ↓
8. 根据 grading.json 运行 F2P/P2P
                         ↓
9. 计算 FULL、PARTIAL 或 NO
                         ↓
10. 保存测试日志和汇总报告
```

推理工作区和评分工作区分开，防止 Agent 留下的临时文件影响评分。

## 5. 模块内容与实现

| 模块 | 主要文件 | 内容与实现 |
|---|---|---|
| Dataset | `dataset.py`、`schema.py` | 读取 manifest 和 split，校验字段，构造统一 Repo Task 对象。 |
| Inference | `inference/worker.py`、`zzcode_adapter.py` | 在指定工作区调用真实 zzcode Agent，并记录执行状态。 |
| Patch Collector | `inference/patch_collector.py` | 执行 `git diff`，收集 Agent 的代码修改。 |
| Prediction | `prediction.py` | 将任务 ID、模型、Patch 和失败原因写入 JSONL。 |
| Workspace | `execution/workspace.py` | 为每个阶段创建独立仓库并 checkout `base_commit`。 |
| Patch Applier | `execution/patch_applier.py` | 应用 Agent Patch；为空、冲突或格式错误时记录失败。 |
| Test Executor | `execution/test_executor.py` | 按 `grading.json` 运行 F2P/P2P，记录退出码、超时和测试数量。 |
| Safety | `grading/safety.py` | 检查危险路径、越界文件和隐藏测试修改。 |
| Grader | `grading/grader.py` | 根据 F2P/P2P 结果计算任务状态。 |
| Runner | `execution/runner.py` | 串联加载、推理、Patch、测试、评分和报告。 |
| Reporting | `reporting/artifacts.py`、`markdown.py` | 保存结构化结果、日志和 Markdown 报告。 |

## 6. F2P/P2P 示例

任务要求禁止读取 `.env`：

```python
# F2P：原代码失败，修复后应通过
def test_cannot_read_env():
    assert read_file(".env").status == "rejected"


# P2P：原代码和修复后都应通过
def test_can_read_normal_file():
    assert read_file("README.md").status == "ok"
```

如果 Agent 直接禁止读取所有文件，F2P 会通过，但 P2P 会失败，所以不能算完全解决。

## 7. 评分规则

```text
F2P 全部通过，并且 P2P 全部通过 → FULL
只通过一部分                         → PARTIAL
无有效修复                           → NO
```

主要汇总指标：

```text
Pass@1 = FULL 任务数 / 总任务数
```

`Pass@1` 表示每道题只让 Agent 尝试一次时的解决率。

## 8. 如何校验题目本身

### Null 校验

不应用任何修复，只运行隐藏测试。F2P 应该失败，证明原始版本确实有 Bug。

```text
base_commit + hidden tests → 不能 FULL
```

### Gold 校验

应用人工正确 Patch 后运行隐藏测试。F2P/P2P 应全部通过，证明题目可解。

```text
base_commit + gold.patch + hidden tests → FULL
```

MVP 可以重复运行 Gold 三次，用来排除不稳定测试。

## 9. 数据集任务分类

1. 模型客户端：响应解析、空响应、网络重试。
2. Agent Runtime：执行循环、步骤预算、停止条件。
3. 文件工具：读取、搜索、写入和 Patch 失败处理。
4. 工作区安全：路径越界和私密文件隔离。
5. Shell 安全：命令执行和密钥隔离。
6. CLI 与配置：配置定位、加载顺序和默认参数。
7. Memory/Checkpoint：状态保存和会话恢复。
8. Trace/Report：事件记录和结果一致性。

面试用 MVP 准备 8～12 项真实、可复现的任务即可。

## 10. 与其他测试的关系

```text
单元测试       → 保证单个组件正确
集成测试       → 保证多个组件组合正确
Harness 测试  → 保证评测器本身正确
SWE 式评测     → 测量真实 Coding Agent 能否解决任务
```

SWE-bench 式 Evaluation 不能替代原有单元测试和集成测试。

## 11. 面试表述

### 30 秒版本

> 我为 zzcode 实现了一套 SWE-bench 式内部评测 MVP。每项任务固定一个存在 Bug 的 base commit，并提供自然语言问题描述。Agent 修改代码后，系统提取 Git Patch，在独立的干净仓库中重新应用 Patch 和隐藏测试。F2P 判断问题是否修好，P2P 判断是否产生回归，最后计算 FULL、PARTIAL、NO 和 Pass@1。数据集还使用 Null 和 Gold Patch 校验，保证每道题真实可解。

### 模块版本

> 系统分为 Dataset、Inference、Execution、Grading 和 Reporting。Dataset 读取任务；Inference 调用真实 Agent 并生成 Patch；Execution 创建干净仓库、应用 Patch 和运行测试；Grading 汇总 F2P/P2P；Reporting 保存 Prediction、日志和报告。公开任务和隐藏评分数据相互隔离。

## 12. MVP 边界

当前重点是完整跑通：

```text
Repo Task → Agent → Patch → 隐藏测试 → 评分 → 报告
```

暂不需要大规模分布式调度、Pass@k、Evaluator–Optimizer、排行榜或复杂可视化。运行环境的进一步隔离属于后续增强，不影响先讲清核心评测逻辑。
