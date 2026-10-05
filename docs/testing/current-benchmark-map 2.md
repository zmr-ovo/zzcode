# zzcode 当前 benchmark 地图（Phase 0）

> 数据源：`benchmarks/coding_tasks.json`（schema version 1）  
> 执行器：`zzcode/evaluator.py`  
> 当前任务数：12

## 1. 当前执行方式

当前 benchmark 不是 SWE-bench 式 Repo Task。实际链路是：

```text
benchmarks/coding_tasks.json
  └─ 读取 prompt、fixture_repo、verifier、step_budget
        ↓
zzcode/evaluator.py
  └─ 根据 task id 从 SCRIPTED_MODEL_OUTPUTS 取预写工具调用和最终答案
        ↓
FakeModelClient
  └─ 在 fixture 的临时副本中驱动 ZZCode
        ↓
ZZCode 直接修改工作区文件
        ↓
evaluator.py 执行公开的内联 verifier 命令
        ↓
写旧 benchmark/report artifact
```

因此它主要验证“已知工具调用经过 Agent 和 harness 后能否得到预期文件与内部状态”，不能证明未知问题上的 coding 能力。正式 Repo Task 所需的 `base_commit → agent → model_patch → predictions.jsonl → 独立 grader → hidden tests` 尚不存在。

## 2. 关键资产与耦合关系

| 资产 | 当前内容 | 问题 | 后续归属 |
|---|---|---|---|
| `benchmarks/coding_tasks.json` | 12 个任务，prompt、fixture、内联 verifier、step budget | verifier 与任务同文件；没有 repo URL、base commit、public/private test patch | 作为 legacy schema 输入，逐项迁移后冻结 |
| `tests/fixtures/bench_repo_readme/` | README 文本 fixture | 不是有提交历史的干净 Git 仓库 | 普通 integration fixture 或 real-model smoke |
| `tests/fixtures/bench_repo_patch/` | sample.txt 文本 fixture | 同上；任务过于简单且答案写在 prompt 中 | 普通 integration fixture 或 real-model smoke |
| `SCRIPTED_MODEL_OUTPUTS` | 每个 task id 对应正确工具调用/答案 | 把正确答案直接注入模型替身，存在答案泄漏 | 从正式评测路径完全移除 |
| `FakeModelClient` | 顺序返回预写输出 | 可测协议，不可测模型解题能力 | 仅留 unit/integration，并改名明确为 stub |
| 内联 `verifier` | Python one-liner 检查文件或内部 artifact | 对 agent 可见；没有 F2P/P2P；断言粒度和错误分类弱 | public tests 或迁移为 grader/private tests |
| 旧报告/metrics | pass、budget、recovery、memory 等内部指标 | 与 formal resolved 混合，容易把机制成功当作任务解决 | diagnostics；核心评测使用统一 TaskResult |

## 3. 12 项任务逐项迁移表

| 旧 task id | 类别 | 当前真正验证的内容 | 当前预写输出 | 正式 Pass@1 资格 | 迁移目标 | 需要的改造 |
|---|---|---|---|---|---|---|
| `readme_intro_locked` | documentation | 精确替换 README 开场句 | 正确 `patch_file` + final | 不具备 | `evaluation/datasets/smoke/` | 可保留为真实模型端到端 smoke；移除 scripted output；若进入 Repo Task，需建立 Git repo、base commit 和独立测试 |
| `readme_schema_note` | documentation | 精确替换 README bullet | 正确 `patch_file` + final | 不具备 | `evaluation/datasets/smoke/` | 同上；只作为链路检查，不用于能力排名 |
| `sample_beta_locked` | text-edit | 精确替换文本行 | 正确 `patch_file` + final | 不具备 | `evaluation/datasets/smoke/` | 改为真实 provider smoke；评分独立于 Agent 运行进程 |
| `sample_gamma_locked` | text-edit | 精确替换文本行 | 正确 `patch_file` + final | 不具备 | `evaluation/datasets/smoke/` | 同上 |
| `invalid_patch_recovery` | tool-boundary | malformed tool payload 后恢复 | 先注入错误 payload，再注入正确 patch | 不具备 | `tests/integration/test_agent_recovery.py` | 用 DeterministicModelStub 明确测试协议恢复；不冒充 coding benchmark |
| `path_escape_recovery` | tool-boundary | 路径逃逸被拒后仍能继续 | 先注入 `../outside.txt`，再注入正确 patch | 不具备 | `tests/security/test_path_boundaries.py` + 可选 live safety eval | 安全门禁直接断言越界失败；真实模型版本只作为行为观测 |
| `repeated_read_recovery` | tool-boundary | 重复调用保护及恢复 | 三次相同 read 后注入正确 patch | 不具备 | `tests/integration/test_agent_recovery.py` | 保留确定性错误序列；从 formal resolved 指标排除 |
| `context_reduction_checkpoint` | recovery | context reduction 时创建 checkpoint | 预写 final | 不具备 | `evaluation/diagnostics/context/` | 作为诊断指标；验证内部机制，不代表 repo bug 被修复 |
| `freshness_reanchor_resume` | recovery | stale summary 恢复与 checkpoint | 预写 final | 不具备 | `evaluation/diagnostics/resume/` | 使用统一 trace schema，单独报告 resume success |
| `workspace_mismatch_resume` | recovery | workspace drift 后重建运行状态 | 预写 final | 不具备 | `evaluation/diagnostics/resume/` | 同上 |
| `durable_promotion_accept` | durable-contract | 稳定事实写入 durable memory | final 中预写待提升事实 | 不具备 | `tests/integration/test_durable_memory.py` | 用协议 stub 测抽取/存储合同；不进入 Repo Task |
| `durable_promotion_reject` | durable-contract | secret/transient 信息被拒绝 | final 中预写稳定、secret、临时事实 | 不具备 | `tests/security/test_memory_redaction.py` | 作为安全与记忆合同测试，明确检查 secret 不落盘 |

## 4. 保留、转换与退出规则

### 保留

以下能力继续存在，但不再叫 formal benchmark：

- malformed tool、重复调用、checkpoint、resume、durable memory：迁入 integration/security/diagnostics。
- 两个小 fixture repo：在新 Repo Task 数据集可用前，可用于真实模型 smoke。
- 旧结果文件：保留为历史对照，标记 `legacy`，不与新 Pass@1 合并。

### 转换

每个正式 Repo Task 必须重新制作，不能只给旧 JSON 改字段：

1. 创建独立、可重复 checkout 的 Git 仓库快照。
2. 固定 `base_commit`，且在该提交上验证问题可复现。
3. 编写只描述问题和约束的 `problem_statement`，不暴露正确改动。
4. public tests 供 Agent 本地验证；private tests 不进入 Agent 工作区。
5. Runner 只保存 Agent 产生的 `git diff` 为 `model_patch`。
6. Grader 在另一份干净 checkout 中应用 patch，再注入 private tests。
7. 同时执行 F2P 与 P2P，并按统一失败类别生成 `TaskResult`。
8. 只有 grader 判定 `FULL` 的任务计入 Resolved/Pass@1。

### 退出

满足以下条件后，旧 `SCRIPTED_MODEL_OUTPUTS`、旧 fixed runner 和旧 formal pass rate 才能删除：

- 新 harness 至少有 1 个端到端 Repo Task 稳定运行；
- 12 项旧任务均已在迁移矩阵中有新归属；
- 旧 integration/diagnostic 行为已有等价测试；
- CI 和文档不再把 scripted benchmark 结果称为 coding 能力成绩。
