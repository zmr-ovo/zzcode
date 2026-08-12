# zzcode 测试与 Evaluation 统一迁移矩阵

本文件把 Phase 0 的盘点结果转换成可执行的迁移顺序。它不要求一次性搬完所有文件；每一行都有退出条件，只有新位置验证通过后，旧实现才退出。

## 1. 迁移决策

| 现有资产 | 当前数量 | 新层级/模块 | 处理方式 | Fake/scripted 策略 | 是否影响 Pass@1 | 退出条件 |
|---|---:|---|---|---|---|---|
| ContextManager 测试 | 7 tests | `tests/unit/` | 原语义迁移 | `FakeModelClient` 改为无任务答案的 `DeterministicModelStub` | 否 | 新路径全通过，node id 映射完整 |
| 旧 evaluator 测试 | 8 tests | `evaluation/tests/unit`、`integration`、`security` | loader/aggregate 按新 schema 重写；旧 runner 暂留 regression | formal harness 禁止 FakeModel | 间接 | 新 WorkspaceManager、Dataset、Grader 测试覆盖后删除 legacy runner 测试 |
| Memory 测试 | 5 tests | `tests/unit/` | 直接迁移 | 不需要模型 | 否 | 新路径全通过 |
| 旧 metrics/ablation | 4 tests | `evaluation/tests/diagnostics`、reporting | 从默认快速门禁拆出，改读统一结果模型 | 不使用任务答案 | 不直接影响 | 新报告能读取 `TaskResult/RunSummary`，旧报告仅作历史对照 |
| RunStore 测试 | 4 tests | `tests/unit/` | 直接迁移；后续与 eval artifact schema 对齐 | 不需要模型 | 否 | 新路径全通过且 artifact 字段稳定 |
| Safety invariants | 11 tests | `tests/security/` + 少量 integration | 按 path/secret/shell/delegation 拆分 | 协议型 stub 可用 | 否，但属于发布门禁 | 安全套件独立命令全通过 |
| TaskState 测试 | 6 tests | `tests/unit/` | 直接迁移 | 不需要模型 | 否 | 新路径全通过 |
| 大型混合测试文件 | 60 tests | unit/integration/security/contract 四层 | 按当前测试地图逐项拆分 | scripted 只用于协议分支；改名并禁止被 eval import | 否 | 60 个旧 node id 全有新映射，旧文件清空后删除 |
| 四个简单文本 benchmark | 4 tasks | `evaluation/datasets/smoke/` | 改为真实 provider 的链路 smoke | 完全移除 scripted 答案 | 不计 formal Pass@1 | 能产生 patch、prediction、grader result；报告标记 smoke |
| 三个 tool-boundary benchmark | 3 tasks | integration/security；可选 live behavior eval | 确定性错误序列用于回归，真实模型只观测 | stub 不包含正确 patch；formal 路径不用 stub | 不计 formal Pass@1 | 对应安全/恢复测试可独立运行 |
| 三个 recovery benchmark | 3 tasks | `evaluation/diagnostics/` | 作为 checkpoint/resume 诊断 | 不预写任务答案 | 不计 formal Pass@1 | 输出统一 diagnostic metrics |
| 两个 durable benchmark | 2 tasks | integration/security | 测 memory promotion 与 secret rejection 合同 | 协议 stub 可用 | 不计 formal Pass@1 | 记忆合同和 secret 不落盘测试通过 |
| 新 curated Repo Tasks | 初始 3–5 | `evaluation/datasets/zzcode_curated/` | 从零构造真实 Git commit、public/private tests | 禁止 Fake/scripted | 是 | 每项 base fail、gold pass、patch isolation、自检全部通过 |
| SWE-bench adapter | 初始 1–3 smoke | `evaluation/adapters/swebench.py` | 复用同一 Runner/Grader 接口 | 真实 Agent/provider | 单独报告 | 能导入 instance、执行并输出同一结果 schema |

## 2. 实现顺序与依赖

| 顺序 | Phase | 主要工作 | 输入 | 输出 | 验收标准 |
|---:|---|---|---|---|---|
| 0 | 冻结与审计 | 跑全量测试、逐项分类 105 tests 与 12 tasks | 当前仓库 | 本目录三份地图 + `artifacts/test-baseline.json` | 数量对齐；已知失败有记录；未改产品行为 |
| 1 | 评测骨架 | 建 `evaluation/` 包、配置、CLI、结果模型、空目录约定 | Phase 0 文档 | 可 import 的骨架与 schema tests | `python -m evaluation.cli --help` 可用；schema round-trip 通过 |
| 2 | 核心 harness | 实现 Dataset、WorkspaceManager、AgentAdapter、PatchCollector、PredictionWriter | Repo Task manifest | `model_patch` 与 `predictions.jsonl` | 干净 checkout；patch 非空/格式合法；禁止越界路径 |
| 3 | Grader | 独立 checkout、apply patch、注入 private tests、F2P/P2P、分类失败 | prediction + private bundle | `grader_result.json`、`TaskResult` | agent 看不到 private tests；base fail/gold pass；超时和 apply failure 可复现 |
| 4 | 首批数据 | 制作 3–5 个 curated Repo Tasks | zzcode 历史/人工 bug | public manifest、private bundle、gold patch | 每项通过数据 QA checklist；至少覆盖 bugfix、regression、安全/恢复中的适合项 |
| 5 | 端到端入口 | 串联 generate、grade、aggregate、report | dataset + provider config | run 目录、summary、Markdown/JSON 报告 | 一条命令跑单任务和数据集；失败可定位到阶段 |
| 6 | 旧测试迁移 | 按 `current-test-map.md` 拆 105 tests | 旧 tests | unit/integration/security/contract/diagnostics | 测试数与映射一致；已知失败单独决议；默认 CI 分层 |
| 7 | 旧 benchmark 迁移 | 按 `current-benchmark-map.md` 转换 12 tasks | legacy benchmark | smoke、integration/security、diagnostics | 正式 eval 不 import `FakeModelClient` 或 `SCRIPTED_MODEL_OUTPUTS` |
| 8 | 外部 adapter 与 CI | 接 SWE-bench，增加 smoke/nightly/report artifact | 同一 harness 接口 | adapter、CI jobs、趋势报告 | curated 与 SWE-bench 共享结果 schema，但分开统计 |

依赖关系是：Phase 1 的数据模型先于 Phase 2；Phase 2 的 prediction 格式先于 Phase 3；Phase 3 的 grader 可用后才能验收 Phase 4 数据。Phase 6 可以在 Phase 2–4 期间分批进行，但不能阻塞第一条 Repo Task 端到端链路。

## 3. 每阶段不得混淆的边界

### unit/integration stub 与 formal evaluation

- unit/integration 的目标是让错误分支稳定、快速、可复现，所以允许确定性 stub。
- stub 只能返回协议响应或构造特定错误，不能携带某个 benchmark 的 gold patch。
- formal evaluation 的目标是测 Agent 在未知任务上的可执行修复能力，只接受真实 Agent 生成的 patch。
- 通过 import 检查或架构依赖测试，禁止 `evaluation/runner`、`evaluation/grader` 引用 `FakeModelClient`、`SCRIPTED_MODEL_OUTPUTS`。

### public 与 private

- public manifest 可包含问题描述、repo、base commit、环境说明、允许公开的测试命令和 public test patch。
- private bundle 包含 hidden tests、F2P/P2P 期望和 grader-only metadata。
- Agent 运行目录只挂载 public 内容；Grader 在独立目录、Agent 结束后才读取 private bundle。
- predictions 只传递 task id、model id、model patch 和运行元数据，不能传递 private 内容。

### 任务解决与系统诊断

- `Resolved/Pass@1` 只由 Grader 的 F2P + P2P 结果决定。
- tool retries、checkpoint、resume、token、latency、cost、secret redaction 是诊断或安全指标。
- 诊断指标可以解释失败原因，但不得把未通过功能测试的任务提升为 resolved。
- smoke 证明链路可运行，不代表能力成绩；报告中必须分区展示。

## 4. 迁移过程的持续门禁

每个 PR/提交至少执行与改动对应的最小集合：

```bash
# 快速行为门禁
pytest tests/unit tests/integration tests/security tests/contract

# 评测框架自身测试
pytest evaluation/tests/unit evaluation/tests/integration evaluation/tests/security

# 单项 Repo Task 的数据自检
python -m evaluation.cli validate --task ZZCODE-BUG-001

# 单项端到端，使用明确 provider 配置
python -m evaluation.cli run --task ZZCODE-BUG-001 --config evaluation/configs/smoke.yaml
```

在目录尚未建立时，上述命令是目标接口而非当前可执行命令。Phase 1 起每建立一层，就把对应命令加入 CI。

## 5. Phase 0 完成检查

- [x] 记录 commit、Python、pytest、运行命令和机器可读测试结果。
- [x] 105 个 pytest 全部进入逐项迁移表。
- [x] 12 个旧 benchmark 全部进入逐项迁移表。
- [x] 明确记录 2 个基线失败，未擅自修改。
- [x] 明确 scripted/FakeModel 只能服务协议回归，不能进入 formal evaluation。
- [x] 给出新层级、迁移顺序、退出条件和 Pass@1 边界。
- [ ] Phase 1 尚未开始：没有创建 `evaluation/` 骨架或修改产品代码。
