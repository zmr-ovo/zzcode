# zzcode 升级与优化实施方案 v2

> 编写日期：2026-10-04  
> 项目：https://github.com/zmr-ovo/zzcode  
> 参考：https://github.com/earendil-works/pi  
> 来源：对原 `plan.md` 的审阅，以及当前工作区源码和产品测试结果。  
> 用途：指导增量升级；本文中的设计、目录和接口均为目标状态，不代表已经实现。

## 1. 结论与项目定位

升级方向可行。保留 zzcode 的定位：**可靠性优先、评测驱动的本地 Coding Agent Harness**。

近期优先解决协议不稳定、工具输出丢失、崩溃恢复不确定和上下文预算不准确的问题，再整理 Runtime 架构。Skills、Session Tree 和第三方 Extensions 按实际需求推进，不作为完成核心升级的前置条件。

参考 Pi 的模型接口、Agent Core、工具契约、上下文压缩和可组合入口。安全策略与副作用恢复需要由 zzcode 自己设计和验证，不能把 Pi 的扩展能力等同于安全隔离。

升级成功的判断分两类：

- **机制重构**：行为兼容，协议、安全、持久化和恢复不变量成立；不要求每次拆模块都提高解题成功率。
- **能力优化**：通过真实任务证明成功率、成本、延迟或长任务连续性改善，并报告退化。

## 2. 当前真实基线

以下为本次评审观测，不是未来阶段的固定事实。每个实施批次开始前重新确认。

| 项目 | 当前观测 |
|---|---|
| 工作区 | `/Users/miaoran/Documents/Program/zzcode` |
| HEAD | `d3db1e0329da9f408d8f1f8d171aeb5387ee74e2` |
| Runtime 规模 | `zzcode/runtime.py` 为 1414 行 |
| 产品测试命令 | `uv run python -m pytest -q tests` |
| 本次产品测试 | **103 passed，2 failed，6 warnings**，耗时约 39.83 秒 |
| 测试失败 1 | `test_welcome_screen_keeps_box_shape_for_long_paths`：欢迎界面字符断言不匹配 |
| 测试失败 2 | `test_reviewer_skeleton_docs_exist`：review-pack 文档不存在 |
| 原测试入口 | `uv run pytest -q tests` 在当前环境收集时发生 `ModuleNotFoundError: zzcode` |
| 工作区状态 | 已有若干未跟踪文档和文件，不能把 HEAD 当成完整工作区快照 |

当前代码确认具备：文本模型接口、多 Provider 适配、工具注册和校验、审批、路径边界、分层 Context/Memory、Checkpoint/Resume、Trace/Report，以及独立 Evaluation 体系。

当前代码确认的限制：

- Provider 返回文本，Runtime 通过 `parse()` 识别 Tool/Final。
- OpenAI-compatible 当前调用 `/responses`，但将完整 prompt 放入单条 user 输入；仍需迁移结构化会话和工具往返。
- Context 默认总预算为 12000 字符。
- Session 为线性 JSON，`SessionStore.save()` 直接写文件。
- 工具返回字符串；Runtime 裁剪结果，并从 Shell 文本解析 exit code。
- 工具预算耗尽后已支持一次 final-only 请求；P0 保留该行为，不把它误列为完全新增功能。
- 产品 Shell 在宿主机执行，Evaluation 已有 Docker Tool Sandbox。
- 当前未发现原计划声称已有的 `verification.py`、Verification Profile 和 Completion Gate 对应实现；应作为待确认/新增能力。

原计划的 `23b85d6…`、`120 passed` 和 Runtime 超过 1700 行不继续作为本次基线。

## 3. 范围与实施原则

### 3.1 近期范围

1. 更新基线和迁移护栏。
2. 统一 Message/Tool/Usage/Stop 契约并接入原生 Tool Calling。
3. 统一工具结果、输出 Artifact、Gateway 和文件操作恢复。
4. 引入 Token 预算、完整消息组裁剪和任务 Compaction。
5. 提取 Coordinator/Loop，建立观测事件、最小 SDK/JSONL。
6. 复用 Local/Docker Executor 接口。
7. 完成一次独立验证的优化闭环。

### 3.2 暂缓范围

Session Tree、第三方 Extensions、并行工具、异步 Loop、复杂 TUI、多 Agent 扩张、自动模型路由、通用 MCP、自动架构搜索与全量 SWE-bench 均不进入近期必做清单。当前已有 `delegate` 的兼容行为需要保留或明确弃用，不新增递归协作能力。

Skills 可以作为独立增量接入，不依赖完整 Event Bus。Session 的最小 Entry 格式在 Compaction 前确定，树形导航后置。

### 3.3 迁移规则

- 先最小纵向切片，再扩展 Provider 和工具覆盖面。
- 保留 `ZZCode.ask()` 外观，避免 CLI 和 Evaluation 同时大改。
- 不要求先移动所有目录；仅在边界稳定后迁移文件。
- 新旧副作用路径使用不同工作区，不进行双重真实执行。
- 使用少量命名配置：`structured`、`reliable_tools`、`token_context`（历史 `legacy` 仅在 P0 冻结提交重放）；内部可有开关，但只支持明确测试过的组合，非法组合启动时拒绝。
- 状态 Schema 版本独立于功能配置；回滚不能通过关闭开关假装 v2 数据是 v1。
- 各阶段均做相关测试；真实模型和 Docker 检查按涉及范围运行。

## 4. 目标架构与边界

```text
CLI / Evaluation / SDK
          ↓
    RunCoordinator
          ↓
       AgentLoop ←→ Provider
          ↓
      ToolGateway → Executor
          ↓
 Session / Ledger / Artifact

ContextEngine 为 Loop 构造请求
核心 Policy 是执行和完成的必经步骤
Observation Events 供 Trace / Metrics / Renderer 消费
```

### 4.1 职责

| 组件 | 职责 |
|---|---|
| Coordinator | 装配依赖、任务状态、恢复、顶层异常、最终结果 |
| Loop | 请求模型、处理内容块和调用批次、驱动下一轮 |
| Provider | 协议映射、能力声明、必要续接数据、错误分类 |
| Gateway | 最终参数校验、安全/审批、账本、执行、结果归一化 |
| ContextEngine | 预算、保留规则、Compaction、freshness |
| Session Store | 会话原始事实与 Entry；不作为纯 Trace 的复制品 |
| Ledger | 操作执行状态与恢复证据；不充当全局参数去重表 |
| Observation Events | 有序观测，不授权工具、不改变持久化事实 |

### 4.2 目录迁移

当前已有 `zzcode/tools.py`，禁止在保留其兼容职责时直接新增同名 `zzcode/tools/` 包。第一版使用 `zzcode/tooling/`，以后需要重命名时单独迁移和测试公开导出。

建议逐步新增：

```text
zzcode/core/          # messages、loop、coordinator、events、errors
zzcode/providers/     # capabilities、adapters
zzcode/tooling/       # spec、gateway、result、ledger、artifact
zzcode/context/       # token_budget、compaction
zzcode/session/       # entries、store、migration
zzcode/executors/     # base、local、docker
```

旧模块先转发，不预先建立大量空目录。状态路径由 Store 配置统一管理，不要求立即改动现有默认存储位置。

## 5. 阶段顺序

| 阶段 | 内容 | 依赖 |
|---|---|---|
| P0 | 基线与迁移护栏 | 无 |
| P1 | 结构化协议、Legacy Adapter、原生工具纵向切片 | P0 |
| P2 | Tool Result/Artifact、Gateway、文件恢复账本 | P1 |
| P3 | Session 最小 Entry、Token Context、Compaction | P1；P2 的结果契约 |
| P4 | Coordinator/Loop、观测事件、SDK/JSONL | 已稳定的 P1–P3 边界 |
| P5 | Local/Docker Executor 统一 | P2；不依赖 Session Tree |
| P6 | 一次评测优化闭环 | 候选机制可运行；评测从 P0 持续执行 |
| 可选 A | Skills | 稳定工具接口和 Context 注入点 |
| 可选 B | Session Tree | Session Entry、Compaction、工作区身份 |
| 可选 C | 可信 Extensions | Policy 和事件契约稳定；有实际扩展需求 |

不同时重写多个核心边界。P6 是完整成果验收，并不表示此前阶段可以不评测。

## 6. P0：更新基线与建立护栏

### 实现任务

- [ ] 定位两项现有产品测试失败，判断修复实现还是更新失效断言；禁止简单删除失败测试。
- [ ] 固定包安装和测试入口，核对旧虚拟环境是否仍引用历史项目路径。
- [ ] 保存 Commit、工作区 diff/必要未跟踪文件清单、Python、依赖锁、命令和结果。
- [ ] 对已有审批、计步、Retry、Resume、脱敏、部分成功语义建立行为表。
- [ ] 保存 3–5 条 Legacy Golden Transcript。
- [ ] 跑现有 12 条确定性诊断任务，记录为机制回归，不能标为模型解题成绩。
- [ ] 固定两个真实 Repo Smoke Task 的仓库、base commit、环境和入口。
- [ ] 固定真实任务集划分：开发诊断集、迁移回归集、保留验证集。
- [ ] 建立命名迁移配置与回滚说明。

### 验收

产品基线通过，或经明确记录确认为环境限制；环境限制不等同于测试通过。Baseline Artifact 能在干净环境重新生成，当前未跟踪用户文件不被覆盖或清理。

## 7. P1：结构化协议与原生 Tool Calling

### 7.1 协议契约

以消息中的有序内容块作为唯一事实来源：

```python
Message(id, role, content, provider_state)
TextBlock(text)
ToolCallBlock(call_id, name, arguments)
ToolResultBlock(call_id, name, status, content, artifact_refs)
ModelRequest(messages, system, tools, output_limit, metadata)
ModelResponse(message, stop_reason, usage)
```

这些是接口轮廓，实施时补类型、错误和版本。若提供 `tool_calls` 快捷属性，应从 `message.content` 派生，不能维护两个可独立修改的列表。

`Usage` 的未知字段使用 `None`，不能用 0 冒充；定义 input/output/cached token 的包含关系。Provider 状态与 Runtime 结果分开：

- Provider Stop：`end_turn / tool_call / max_tokens / content_filter / unknown`。
- Runtime Stop：预算耗尽、审批阻塞、验证失败、取消、Provider 错误、上下文容量不足等。

Provider 异常使用类型化错误与重试类别，不制造正常 Final。`max_tokens` 不能被当成任务完成。

### 7.2 会话与 Provider 兼容

- 按当前实现决策直接移除原 `parse()` 和文本标签协议，不保留 Legacy Adapter。FakeModel 使用结构化响应，字符串只表示普通文本。
- 同时迁移请求历史：system/user/assistant/tool 分角色表示；不能只改变返回类型。
- Provider Adapter 保存其续接所必需的 reasoning、签名或不透明状态。核心只存储/传递，不解释它们；普通 metadata 使用允许字段集，禁止记录完整原始响应。
- `provider_state` 与可公开 metadata 分开，采用受限存储，不输出到 Trace、JSONL 或 Durable Memory；必要状态无法保存/重放时明确拒绝恢复，不能静默丢弃。
- 显式声明 API 家族与工具能力：OpenAI 使用 Responses `function_call/function_call_output`，Claude 使用 Messages `tool_use/tool_result`，Ollama 使用 `/api/chat`。
- OpenAI-compatible 不等于支持 Responses 全部功能。端点必须支持对应原生工具协议；不支持时明确报错，不能回退到文本解析。

### 7.3 多调用批次

第一版接受多 Tool Call，按消息顺序串行执行。每条调用都有唯一关联结果，包括校验拒绝、审批拒绝和未执行取消；不得静默丢弃第二条调用。批次工具结果完整后再调用模型。

预算不足时，为未执行调用生成明确结果；在完整批次边界停止。第一版不并行。

### 7.4 纵向切片与验收

1. Message + 原生 FakeModel + Golden 测试。
2. 一个 OpenAI Responses Provider + `read_file` + Tool Result 回传。
3. 写工具沿用现有审批、路径与只读护栏；P2 再抽取 Gateway 与恢复账本。
4. Anthropic Tool Use 适配。
5. Ollama 原生 `/api/chat` 工具调用。

覆盖内容块顺序、工具 ID 绑定、多调用、非法参数、空响应、截断、Provider 错误和恢复重放。确定性 HTTP 契约测试必须通过；真实网络 Smoke 单独标记，未运行不算通过。

## 8. P2：工具结果、Gateway 与恢复账本

### 8.1 类型化工具和统一结果

`ToolSpec` 声明 JSON Schema、风险、副作用、超时和恢复策略。风险/幂等标签只是策略输入，不能代替实际检查。Schema 支持范围明确，不宣称未经实现的完整 JSON Schema 兼容。

统一 `ToolResult`；状态至少包括：

```text
succeeded / failed / partial_success / rejected / cancelled / unknown
```

附带 `error_code`、`exit_code`、`affected_paths`、工作区身份、截断信息与 Artifact 引用。Shell exit code 从 Executor 对象读取，不再解析显示文本。模型展示与程序读取使用同一事实对象。

输出策略：

- 文件：所请求行范围及继续读取入口。
- 搜索：限制匹配数，说明总量是否已知。
- Shell：Head/Tail、exit code、超时/取消状态。
- Diff：摘要和完整 Artifact。
- 验证：测试结论、命令、环境、工作区/Patch Digest。

输出读取和存储设硬上限；不能只在全部 `capture_output` 完成后裁剪。Artifact 持久化前脱敏，路径受 Store 约束，不暴露任意宿主文件；需要模型可用的受控读取工具。原始敏感输出默认不保存，引用不进入 Durable Memory。

### 8.2 Gateway 的强制顺序

```text
接收提案 → 可信扩展变换（如启用） → 冻结最终参数
→ Schema/路径校验 → 核心安全判断 → 记录 planned
→ 对最终参数审批 → 记录 running 并确认持久化
→ 执行 → 记录终态 → 返回结果/发观测事件
```

无效/危险提案也留下脱敏拒绝记录，但不存储未经处理的秘密参数。审批拒绝进入 `rejected`。审批之后禁止修改执行参数；如修改，重新走校验和审批。任何必要持久化失败都禁止开始工具执行。

Gateway 是模型调用和内部嵌套工具调用的共同入口。计数分别记录 model requests、transport retries、proposed/executed/rejected/succeeded calls；另保留全局迭代和时间上限，防止大量拒绝绕开预算。

### 8.3 操作身份

- `call_id`：Provider 协议中的一次调用，至少在对应 assistant 消息内唯一。
- `operation_id`：一次逻辑操作的恢复身份，生成后持久化，恢复沿用。
- `args_hash`：规范化参数签名，仅提供比较证据。
- `idempotency_key`：绑定逻辑操作；外部系统支持时传入并查询。

相同工具和参数可以是用户有意重复的不同操作，禁止全局参数去重。旧 repeated-call guard 是循环检测，与操作幂等分开。

### 8.4 持久化与状态

建议第一版采用标准库 SQLite 的事务账本，单写者模式；JSONL 用于 Trace 或账本导出。配置事务持久化、并发限制和故障处理，不把事务提交与外部副作用宣称为原子整体。

```text
planned → running → succeeded / failed / partial_success / unknown
planned → rejected / cancelled
running 在恢复时 → unknown → reconcile → 已核对终态或保持 unknown
```

`reconcile` 是核对过程，记录证据和结论，不靠“已核对”本身判断成功。执行已发生但终态写入失败时中止 Run，恢复时核对，不能返回普通失败让 Agent 自动重试。

### 8.5 按工具恢复

| 工具 | 策略 |
|---|---|
| Read/Search | 可重新读取；结果可能随工作区改变，不直接复用旧缓存 |
| Write/Patch | 保存完整文件前态和预期后态哈希；包含文件不存在状态 |
| Shell | 崩溃、超时、取消后不能假设未执行，默认 unknown/partial_success |
| Verify | 基于当前工作区和验证配置重新运行，旧结果不能证明新版本通过 |
| 外部副作用 | 外部幂等键和状态查询；无核对能力时保持 unknown 并要求显式处理 |

Write/Patch 使用同目录临时文件、原子替换，并按需要同步文件/目录；明确元数据和软链接处理。执行前重新检查前态，检测用户并发编辑。无协作锁不能保证排除所有外部编辑竞态，应说明支持边界。

恢复比较完整文件哈希：等于后态则核对为完成；等于前态则按策略允许重试；其他情况为冲突，不自动覆盖。检查 old/new 文本存在与否不足以证明操作完成。

### 8.6 故障注入与验收

在 planned 后、running 后、文件替换后、终态提交前分别模拟崩溃。验证：无未持久化执行、无盲重试、文件冲突不覆盖、秘密不落盘、重复意图不被错误去重、恢复不能启动第二个仍在运行的操作。

目标是可检测、可核对的恢复；对任意 Shell/外部操作不承诺普遍 exactly-once。

## 9. P3：Token Context、Session Entry 与 Compaction

### 9.1 最小 Session Entry 提前落地

定义有版本的 Message、Tool Batch、Checkpoint、Compaction Entry，包含 ID、顺序、时间、关联操作和工作区身份。第一版仅线性追加，不实现树导航。

Session、Ledger、Checkpoint 的职责和引用关系明确：Session 保存会话事实，Ledger 保存操作状态，Checkpoint 引用恢复锚点，Trace 是观测投影。恢复以 Ledger 核对工具状态，不能根据 Trace 缺一行推断未执行。

Session 采用单写者和完整记录恢复；JSONL 尾部半行可截断恢复，中间损坏明确报错。Active Leaf/索引作为可重建投影；元数据原子替换。旧 JSON 先备份/复制迁移，不原地破坏。

### 9.2 能力和预算

模型能力保存来源和版本。显式用户配置优先，其次 Provider 模型信息、已知表和保守默认。工具/缓存/流式能力分别声明。

使用一个统一计数入口：

```text
完整请求估算 = system + messages + tool schemas + 协议开销
完整请求估算 ≤ context window - reserved output - safety margin
```

工具 Schema 只算一次。Tokenizer 不可用时使用保守估算并记录误差来源；模型调用返回的 usage 用于校准。输出限制同时遵守模型最大输出和剩余窗口。

当前请求和必要规则不静默裁剪。如果它们本身超窗口，返回 `CONTEXT_CAPACITY_EXCEEDED`，提示拆分输入，不无限压缩。

### 9.3 保留与压缩

- 按完整会话组裁剪，禁止孤立 Tool Result 或缺失调用结果。
- 未完成工具批次不能被压缩移除。
- 高优先级保留用户约束、当前目标、关键文件版本、失败结论和下一步。
- Compaction 保留 source range、first retained entry、摘要版本、来源、成本和工作区身份。
- 结构化任务状态优先从运行事实生成；模型摘要补充推理结论，并标明不确定内容。
- 摘要不能提升文档/工具输出的信任级别，不能把不可信内容变成系统指令。
- Compaction 与 Working/Durable Memory、Checkpoint 分别保存，不重复堆入 Context。

验证记录绑定工作区树指纹（含相关未跟踪文件）、Patch Digest、命令和环境；文件或验证配置变化后失效。用户编辑和分支切换后重新检查 freshness。

### 9.4 溢出与缓存

压缩调用本身也有预算、成本、超时和失败回退。真正的上下文长度错误最多触发一次更保守重建；认证、限流和普通 Provider 错误不能误归为溢出。无法压缩时明确停止。

缓存只优化稳定 Prefix，key 包含模型、Schema、工具和指令签名；key 不保证 Provider 缓存命中，使用实际 cached usage 测量。

### 验收

覆盖不同窗口、中英文/代码估算、工具 Schema、超大请求、批次保留、压缩失败、修改后验证失效。至少三个长任务场景验证约束保留、Compaction 后继续修改、模型窗口变化。与 Legacy 比较信息保留和成本，不只检查摘要字段存在。

## 10. P4：Runtime 拆分、观测事件与统一入口

先提取 Gateway/Context/Store，再拆 Coordinator/Loop。Safety、Approval 和完成判断是显式 Policy；不通过可选订阅者决定是否执行。

观测事件第一版限定为 Run/Model/Tool/Compaction/Checkpoint 的 started/completed/rejected/failed 等必要节点。事件有版本、run ID、序号和关联 ID；不可变 payload；发生在相应状态持久化之后。

Trace/Metrics/Renderer 失败不得改变已执行操作事实。重要 Store 写入失败必须停止，不能把它当成普通观测失败。重复订阅/消费按事件 ID 去重，明确观测传递不等于副作用 exactly-once。

最小入口：

```python
Agent.run(request) -> Iterator[AgentEvent]
Agent.run_to_completion(request) -> AgentResult
ZZCode.ask(prompt) -> str  # 兼容外观
```

CLI Human/JSONL 与 Evaluation 使用同一入口。JSONL stdout 只输出合法事件，诊断进入 stderr；输出允许字段并脱敏。消费者提前关闭迭代器要触发取消和清理，不能遗留后台执行。第一版保持同步，不增加另一套 Loop。

### 新增完成策略

当前 Completion Gate 属待新增能力。先定义任务类型：普通问答允许直接结束；代码修改任务按验证配置检查修改和公开验证证据；无修改必要的调查任务允许说明理由。

区分“模型给出 Final”“Runtime 完成”“独立 Grader resolved”。保留现有预算耗尽后的 final-only 请求行为，工具不执行，仍计入时间/模型预算；不能以此把未验证任务标记为解决。新增完成策略拒绝 Final 的次数有上限。

### 验收

外观兼容、CLI/SDK 结果一致、事件顺序稳定、异常有终止事件、Evaluation 不依赖 Runtime 私有字段、输出无秘密、取消能清理进程。架构重构以兼容和职责清晰验收，不强求成功率上涨。

## 11. P5：统一 Local/Docker Executor

定义 `CommandRequest/CommandResult`，统一 cwd、环境、超时、取消、输出上限、exit code 和资源状态。Provider 网络调用在 Executor 外，工具执行与模型凭证分离。

Local 保留审批和环境变量限制；环境 Allowlist 不能阻止进程读取宿主凭证文件，Local 不宣称强隔离。超时清理进程组，不能只终止父 Shell。

Docker 复用现有 Evaluation 实现：固定镜像摘要、禁网、非 Root、只读根、能力移除、资源限制、受控 tmp 和工作区挂载；禁止 Docker Socket 和无关宿主挂载。

“凭证不进入容器”还需要检查镜像、挂载工作区、软链接和项目 `.env`。含凭证文件的工作区使用受控副本/排除策略，否则拒绝宣称凭证隔离。受控副本需明确修改回传和冲突处理。

公开文件工具与 Shell 使用一致工作区身份；不能 Shell 修改容器副本、文件工具读取另一份宿主目录。镜像依赖预先准备，禁网运行缺依赖时明确报环境错误，不偷偷启网。

CLI 提供 `--executor local|docker`，初期默认 Local。正式真实 Repo Evaluation 默认 Docker；确定性单元测试仍可 Local，不能把所有 Evaluation 测试都强制容器化。

验收包含网络/凭证边界、超时清理、OOM、输出上限、文件同步与相同结果契约。Docker 不可用明确报告 blocked/unverified，不算安全测试通过。

## 12. 可选能力与触发条件

### 12.1 Skills

当重复任务方法值得复用时增加。先发现 name/description/id，按需加载正文；记录来源、版本和内容哈希。项目元数据也属于不可信输入，作为数据展示，不能无条件提升为指令。

项目 Trust 与脚本执行授权分开：读取说明不等于允许执行脚本；脚本通过 Gateway/Executor。与用户指令冲突时遵循用户指令，不让 Skill 绕开核心边界。大量 Skills 应有检索和目录预算，不能假设元数据数量增加时 Prompt 不增长。

### 12.2 Session Tree

当用户确需比较方案、回到历史节点继续时增加。区分同一 Session 内切换分支与 fork 新 Session。迁移无损、Active Leaf 可恢复、跨分支 Memory/验证按 freshness 过滤。

对话回退不等于代码回退；工作区干净也不等于处于历史节点的 commit。检查 base commit、树指纹和未跟踪文件。明确 `context-only`、`require-matching-workspace` 和后续 `git-worktree` 模式；不静默还原文件。Branch Summary 只携带仍有效事实。

### 12.3 Extensions

先内部可信扩展 API，有实际案例后再公开。进程内扩展具有进程权限，不能承诺对恶意代码安全隔离。Trust 绑定实际代码/依赖版本，代码变化后重新确认。

核心 Safety/Secret Filter 保留在核心必经路径；扩展能提出更严格限制，不能解除限制。需要不可信扩展时另设计受限 IPC、进程/容器与能力授权，不以 handler priority 替代隔离。

## 13. P6：评测驱动的一次优化闭环

### 三层验证

| 层次 | 内容 | 可证明的结论 |
|---|---|---|
| L1 产品测试 | 协议、工具、恢复、Context、CLI | 功能契约和机制正确性 |
| L2 Harness 测试 | 数据、隔离、Patch、Docker、Grader | 评测基础设施可靠性 |
| L3 真实 Agent 任务 | 真实模型、工具轨迹、独立验证 | 任务解决能力与效率 |

12 条 scripted 任务只用于机制诊断；两个真实 Repo Task 只用于 Smoke。正式结果不混入 FakeModel 输出，不把 100% 诊断通过率写成 resolved。

### 对照协议

- 固定数据版本、base commit、Provider/model、Prompt、工具、预算、镜像和依赖。
- Baseline/Candidate 从独立干净工作区开始；不共享会影响结果的 Session/Memory。
- 记录缓存策略；冷启动与热缓存分开报告，减少运行顺序偏差。
- 保存模型请求次数、transport retries、工具次数、真实 usage、wall time、成本和失败分类。
- 小样本重复运行并报告逐任务变化与不确定性；数据不足时只报告趋势。
- 私有测试不进入 Agent Context。开发集可分析失败，保留集不用于调参；报告调参来源。

### 门禁

- 安全不变量在确定性测试中必须成立，不能用平均成功率抵消越界。
- 机制重构要求契约通过和已知行为兼容；允许明确记录的展示行为调整。
- 能力优化预先声明目标、样本、预算、容许退化和重复次数，不事后挑最好的一次。
- 展示 Baseline/Candidate 收益与退化，不用单一总分隐藏成本增加。

### 一次闭环

选择一个来自 Trace 的明确失败，例如长输出丢失关键错误。验证原因，实施一项改动，在同一任务集复测，最后用保留集确认。收益不足则保留实验配置，不切换默认值。

建议失败分类包括协议错误、参数错误、执行失败、部分成功、审批拒绝、安全违规、容量不足、摘要信息丢失、恢复冲突、Provider 不可用、超时、验证失败与基础设施错误。

## 14. 推荐提交批次

| 批次 | 可独立审阅的结果 |
|---|---|
| B0 | 修复/解释现有基线失败，固定环境和 Golden |
| B1 | 核心消息类型、Legacy Adapter、兼容行为 |
| B2 | 一个原生 Provider + read_file 完整往返 |
| B3 | ToolSpec、统一 ToolResult、受控 Artifact |
| B4 | Gateway、文件原子写入、账本、崩溃核对 |
| B5 | 第二 Provider、多调用和重放契约 |
| B6 | 最小 Session Entry 和无损迁移 |
| B7 | Token 预算、能力来源和消息组保留 |
| B8 | Compaction、freshness、长任务诊断 |
| B9 | Coordinator/Loop、观测事件、兼容外观 |
| B10 | SDK/JSONL、完成策略、Evaluation 接入 |
| B11 | Local/Docker Executor 复用和边界验证 |
| B12 | 真实任务优化闭环和结果报告 |

Skills/Tree/Extensions 单独立项，不阻塞 B12。每批次结束更新当前状态，不把未验证功能标记为完成。

## 15. 第一批立即执行的任务

第一批仅做 B0/B1，不立即全面拆 Runtime：

1. 处理当前两项测试失败和 pytest 导入入口，保存新的通过基线。
2. 确认真实工作区与 HEAD 差异，记录已有能力和缺口。
3. 新建 `core/messages.py`，定义最小有序消息契约。
4. 包装原 `ZZCode.parse()` 为 Legacy Adapter，暂保留原方法兼容导出。
5. 为 FakeModel 增加适配器，用 Golden Transcript 验证行为。
6. 写工具身份和恢复状态设计说明，暂不在 `tools/` 下创建同名包，也不先写空 Ledger 实现。

第一批不改网络请求，不改默认工具执行。第二批再跑通原生 read_file；文件副作用切换默认值前完成 Gateway 和恢复验证。

## 16. 通用 Definition of Done

- [ ] 范围、当前缺口、契约、兼容与回滚说明完整。
- [ ] 相关单元/集成/安全测试通过；崩溃恢复功能有故障注入证据。
- [ ] Schema、错误码、身份和计数语义明确。
- [ ] Secrets 不进入 Trace、JSONL、账本明文、Artifact 或 Durable Memory。
- [ ] 无第二套 Agent Loop，无未经审批的副作用重复执行。
- [ ] 相关机制诊断和真实 Smoke 已记录；未运行检查明确注明。
- [ ] 新旧数据可读策略明确；旧实现只在默认迁移和回滚验证后删除。
- [ ] README/架构/测试地图更新；报告同时包含限制和退化。

## 17. 最终演示与验收

演示一个真实 Repo 修复：结构化 Provider 调工具，Gateway 审批并持久化，Executor 修改代码，完整输出保存在受控 Artifact，长任务经 Compaction 保留约束；文件操作在一次注入中断后按哈希核对，公开验证绑定当前代码，CLI/SDK 输出同一结果，独立 Grader 在新工作区验证 Patch。

最终成果至少提供：通过的产品与 Harness 检查、恢复故障注入记录、协议契约报告、长任务对照报告、真实任务 Baseline/Candidate 和可复现配置。

Session Tree 和第三方 Extensions 不作为此次核心升级的完成条件。

## 18. 参考与使用边界

- [Pi 官方仓库](https://github.com/earendil-works/pi)：模块划分、CLI/SDK 与容器化说明。
- [Pi Extensions](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/extensions.md)：事件/工具契约，以及同进程扩展权限边界。
- [Pi Compaction](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/compaction.md)：上下文压缩设计参考。
- 本地 `docs/testing/current-benchmark-map.md`：区分 scripted 机制诊断与真实 Repo resolved。

以上在线资料会变化。P0 保存实际采用的 Pi 版本/commit；Provider 实施时按实际 API 版本核对官方契约，不直接依赖 `latest` 页面作为永久规格。
