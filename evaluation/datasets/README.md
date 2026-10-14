# 任务数据集

这里保存真实仓库修复任务的公开输入，供 Agent 解题；`tests/` 保存验证程序行为的 pytest 用例。一个任务可以包含多个隐藏评分用例。

当前数据集为 [zzcode-bench-v1](zzcode-bench-v1/dataset-card.md)：8 项任务，协议 1 项、运行时 2 项、配置 2 项、安全 3 项。任务 ID 采用小写 `类别-语义名称`，例如 `security-shell-secrets`。

## 文件结构

```text
<dataset>/
├── manifest.jsonl                 # 任务 ID、仓库、base commit、环境及题面路径
├── dataset-lock.json              # 各 split 的任务数和数据摘要
├── dataset-card.md                # 来源、任务列表、指标及限制
├── splits/
│   ├── dev.txt                    # 开发与调试
│   ├── test.txt                   # 固定配置后的留出评估
│   └── all.txt                    # 数据质量检查
└── instances/<task-id>/
    ├── problem_statement.md       # Agent 可见的问题描述
    └── task.json                  # category、title、类型、范围、难度和来源
```

私有评分输入单独提供，目录 ID 必须与公开任务一致：

```text
evaluation/private/<dataset>/<task-id>/
├── gold.patch                     # 参考修复，仅供数据门禁使用
├── test.patch                     # 独立评分时注入的隐藏测试
└── grading.json                   # 补丁路径及 F2P/P2P 测试选择器
```

可用 `ZZCODE_EVAL_PRIVATE_ROOT` 指定私有根目录。Agent 只能看到公开题面和任务仓库，不能读取 Gold、隐藏测试或评分选择器。私有评分输入不是可重建的运行缓存。

## 任务字段

| 字段 | 含义 |
|---|---|
| `instance_id` | 唯一任务标识，目录、manifest 和私有评分配置保持一致 |
| `base_commit` | 存在问题的固定代码版本，而非当前仓库 HEAD |
| `environment_id` | 测试执行所需的环境标识 |
| `category` / `title` | 任务分类和可读名称 |
| `task_type` / `scope` / `difficulty` | 修改类型、影响范围和难度 |
| `source` | 来源，例如修复历史或人工回归任务 |

评分 selector 指向 base commit 中的文件；不能因为当前项目目录重构就直接改写它们。当前 Agent 修复的是历史工作副本，不是直接修改最新源码。

## 新增 case

1. 从真实 Bug、修复提交或 Issue–PR 中选择可复现的问题，固定修复前 commit 和依赖环境。
2. 写题面，说明症状、复现方法和期望行为，不公开参考修复。
3. 准备 Gold 和隐藏测试；测试检查行为，允许不同但正确的实现。
4. 设置 F2P/P2P：Null 下至少一项 F2P 失败且 P2P 全通过；Gold 下两组全部通过，并重复检查稳定性。
5. 检查公开／私有隔离、Patch 安全和 Agent 成功／失败／超时的产物完整性。
6. 登记 manifest、task.json 与 split，完成验收后生成数据锁。任务定义、私有补丁或选择器变化都需要重算 digest，不能只改计数。

dev 用于分析失败和调参；已经用于调参的任务不能再作为盲评留出任务。建议新增数据时发布新版本，固定版本、模型、预算、Agent commit 和镜像 digest 后再比较结果。

## 校验与评分

```bash
uv run python -m zzcode.evaluation.cli validate-dataset \
  --public-root evaluation/datasets/zzcode-bench-v1 \
  --private-root evaluation/private --split dev
```

该命令校验数据结构及数据锁，不运行评分测试。Null/Gold、真实 Agent 和独立评分通过完整评测入口运行，命令见 [评测指南](../README.md)。

当前 8 项任务来自同一仓库和历史基线，适合验证评测闭环及小规模对照，不能代表通用软件工程能力。
