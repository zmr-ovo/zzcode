# ZZCode Coding Agent 全链路与 Evaluation：面试复习稿

> 主链路：CLI 装配 → 工作区感知 → Context 构建 → LLM 决策 → Tool Gateway → 文件 / Shell 行动 → History / Memory / Checkpoint / Trace → Git Patch → Evaluation Inference → 独立 Grader → Hidden F2P/P2P → Pass@1 与报告

## 使用口径说明

本文分成两类事实，面试前必须区分：

1. **当前真实实现**：Agent Runtime、Context、Memory、Checkpoint、Tool Gateway，以及 SWE-bench 式 Evaluation Harness、8 项 Internal Repo Tasks、Docker Grader、F2P/P2P、Artifact 和 Markdown Report，均按当前 zzcode 仓库代码书写。
2. **迁移完成后的假设口径**：用户要求假设旧测试和旧 benchmark 已完成迁移，并给出一组测试结果。因此第 19～21 节使用“面试演示假设结果”，这些数字不是当前仓库已经产生的真实模型正式成绩，正式投递简历前应替换为真实运行结果。

当前已经真实验证的数据包括：

- Evaluation 非 Docker 测试：`115 passed, 6 deselected`；
- 8 项 Repo Task：8/8 Null 非 FULL，8/8 Gold FULL；
- Docker 数据稳定性门禁：8 项任务的 Null + Gold×3 全部通过；
- 产品测试基线：`103 passed, 2 failed`，两个失败是欢迎页旧断言和缺失 review-pack 文档；
- Phase 7 提交：`d3db1e0`。

本文后续使用的假设正式成绩为：

```text
迁移后测试：全部通过
Internal test split：3/4 FULL，Pass@1 = 75.0%
Internal all split 探索性汇总：5/8 FULL，62.5%
全量另外 2 项 PARTIAL、1 项 NO
P2P aggregate = 100%
```

这组数字用于练习如何解释指标，不应伪装成真实实验记录。

## 一句话介绍

ZZCode 是一个运行在本地 Git 仓库中的轻量 Coding Agent Harness：它把大模型包装成受预算约束、可调用工具、可管理上下文、可恢复并可审计的多步执行系统，并配套实现了一套 SWE-bench 式可执行评测框架，用独立仓库、Git Patch、Hidden Tests、F2P/P2P 和 Docker 隔离验证 Agent 是否真正修复了问题。

## 架构定位

这个项目的重点不是“接入一个 LLM API”，而是补齐模型外部的平台能力：

- 模型决定下一步做什么；
- Runtime 控制模型可以执行多少步、何时停止；
- ContextManager 决定模型每轮看见哪些信息；
- Tool Gateway 决定模型可以对仓库做什么；
- Memory 和 Checkpoint 负责跨轮状态连续性与过期检测；
- Trace 和 Report 负责解释一次运行为什么成功或失败；
- Evaluation Harness 不相信 Agent 的最终文字，而是只读取 Agent 产生的 Git Patch，在独立环境中执行测试评分。

因此项目有两条相互独立的主链路：

```text
产品执行链：用户请求 → Agent → 工具 → 仓库修改 → 最终回答

评测链：Repo Task → Agent → git diff → model_patch
       → 干净仓库 → hidden tests → F2P/P2P → FULL/PARTIAL/NO
```

## 1. 总体架构

```text
┌────────────────────────────── 1. 用户入口 ──────────────────────────────┐
│  one-shot CLI  │  交互式 REPL  │  --resume 恢复会话                  │
└────────────────────────────────┬───────────────────────────────────────┘
                                 ▼
┌──────────────────────────── 2. 依赖装配 ───────────────────────────────┐
│ CLI 参数 / 环境变量 → Provider → WorkspaceContext → SessionStore      │
│ → RunStore → approval / step / output budget → ZZCode Runtime         │
└────────────────────────────────┬───────────────────────────────────────┘
                                 ▼
┌──────────────────────────── 3. 工作区感知 ─────────────────────────────┐
│ repo root / branch / git status / recent commits / project docs       │
│ → Workspace Fingerprint → Stable Prompt Prefix                        │
└────────────────────────────────┬───────────────────────────────────────┘
                                 ▼
┌─────────────────────────── 4. ContextManager ──────────────────────────┐
│ Prefix + Working Memory + Relevant Memory + History + Current Request │
│ → 分区预算 → 分级压缩 → Prompt + Prompt Metadata                       │
└────────────────────────────────┬───────────────────────────────────────┘
                                 ▼
┌──────────────────────────── 5. Agent Loop ─────────────────────────────┐
│ ModelClient.complete → parse                                          │
│       ├─ tool → Tool Gateway → tool result → history/checkpoint ─┐    │
│       ├─ final → completed → durable promotion → final report     │    │
│       └─ malformed → retry / retry limit                          │    │
│                              ▲                                    │    │
└──────────────────────────────┴────────────────────────────────────┘    │
                                 ▼
┌──────────────────────────── 6. 工具执行层 ─────────────────────────────┐
│ list/read/search │ write/patch │ run_shell │ bounded delegate          │
│ Registry → 参数 → 路径 → 重复调用 → 审批 → 执行 → Diff → 审计           │
└────────────────────────────────┬───────────────────────────────────────┘
                                 ▼
┌──────────────────────────── 7. 状态沉淀 ───────────────────────────────┐
│ Session / Working-Episodic-Durable Memory / TaskState / Checkpoint    │
│ trace.jsonl / task_state.json / report.json                           │
└────────────────────────────────┬───────────────────────────────────────┘
                                 ▼
┌────────────────────────── 8. Evaluation Adapter ───────────────────────┐
│ TaskInstance → 独立 inference workspace → 真实 Agent → git diff       │
│ → patch.diff → predictions.jsonl → AgentRunResult                     │
└────────────────────────────────┬───────────────────────────────────────┘
                                 ▼
┌─────────────────────────── 9. 独立 Grader ─────────────────────────────┐
│ 新建干净 grading workspace → Patch Safety → git apply                 │
│ → 注入 private test.patch → Docker F2P / P2P → ResolvedStatus         │
└────────────────────────────────┬───────────────────────────────────────┘
                                 ▼
┌──────────────────────────── 10. 评测报告 ──────────────────────────────┐
│ run_manifest / prediction / validation / JUnit / instance result     │
│ → results.json → report.md → Pass@1、F2P、P2P、失败分类               │
└────────────────────────────────────────────────────────────────────────┘
```

## 2. 项目解决的核心问题

### 2.1 模型不是 Agent Runtime

LLM 只能生成文本，本身不会自动获得下面这些能力：

- 感知 Git 仓库；
- 决定哪些文件可以访问；
- 安全执行 Shell；
- 控制多轮工具预算；
- 保留跨轮工作状态；
- 检测恢复时文件是否变化；
- 记录每次模型和工具调用；
- 判断代码是否真的修复。

ZZCode 将这些职责放在模型外部，模型只负责下一步决策，平台负责控制、执行和验证。

### 2.2 Agent 说“完成”不等于任务解决

Coding Agent 可能出现：

- 只分析，没有修改；
- 修改了错误文件；
- 生成空 Patch；
- 只修复新测试，破坏旧行为；
- 测试命令本身没有执行成功；
- 最终回答宣称成功，但代码不能运行。

因此正式评分不读取最终回答的主观描述，只接受：

```text
Agent 产生的 Git Patch
        +
独立环境中的可执行测试结果
```

### 2.3 内部回归测试不等于模型能力评测

项目区分两种测试：

- 产品与 Harness 测试允许使用 Deterministic Stub，目标是稳定覆盖状态机、错误分支和安全边界；
- Formal Evaluation 必须使用真实模型生成未知任务 Patch，不能把 Gold Patch 或 scripted output 塞给 FakeLLM。

这条边界是整个评测系统可信度的基础。

## 3. CLI 与依赖装配

入口位于 `zzcode/cli.py`。CLI 是 Composition Root，负责把外部配置转换成 Runtime 对象图。

```text
argv / environment
  → build_arg_parser
  → provider / model / endpoint / secret names
  → WorkspaceContext.build(cwd)
  → SessionStore(.zzcode/sessions)
  → new session 或 --resume session
  → ZZCode(...)
  → one-shot ask 或 REPL ask
```

### 3.1 支持的 Provider

- Ollama；
- OpenAI-compatible Responses API；
- Anthropic-compatible Messages API。

Runtime 只依赖统一的：

```python
complete(prompt, max_new_tokens, ...)
```

Provider 协议差异、鉴权、响应解析、usage 和缓存元数据封装在 `models.py`。

### 3.2 one-shot 与 REPL 为什么共用 `ask()`

如果两种入口各写一套执行流程，会造成工具权限、记忆、Trace 和停止条件不一致。ZZCode 让入口只负责交互方式，核心行为统一收口到 `ZZCode.ask()`。

### 3.3 关键参数

| 参数 | 作用 |
|---|---|
| `--cwd` | 目标仓库工作目录 |
| `--provider` | 模型后端 |
| `--model` | 显式模型覆盖 |
| `--resume` | 恢复指定或最近 Session |
| `--approval` | `ask / auto / never` 风险工具策略 |
| `--max-steps` | 最大工具步骤 |
| `--max-new-tokens` | 每轮最大模型输出预算 |

面试表达：

> CLI 不参与 Agent 推理，它只负责配置、对象装配和生命周期。这样替换 Provider、入口或存储方式时，不需要重写控制循环。

## 4. WorkspaceContext：低成本感知仓库

`zzcode/workspace.py` 不会启动时索引整个代码库，而是创建一个小型导航快照：

- 当前目录和 repo root；
- 当前分支和默认分支；
- Git status；
- 最近提交；
- `AGENTS.md`、`README.md`、`pyproject.toml`、`package.json` 等少量项目文档。

### 4.1 为什么不把整个仓库塞给模型

完整仓库会导致：

- 首轮 Prompt 很大；
- 大量无关代码干扰决策；
- 每轮重复传输成本高；
- 文件变化后缓存很难复用。

ZZCode 使用 lazy inspection：先提供导航信息，模型通过 `list_files / search / read_file` 按需探索。

### 4.2 Workspace Fingerprint

`fingerprint()` 对结构化工作区信息计算 SHA-256，用于判断：

- Stable Prefix 是否需要重建；
- 恢复会话时工作区是否漂移；
- Runtime Identity 是否仍然匹配。

边界：它不是全仓库内容哈希。关键文件变化还会通过 Memory Freshness 单独检测。

## 5. ContextManager：分区预算而不是粗暴截断

`zzcode/context_manager.py` 将 Prompt 拆成五个部分：

```text
1. prefix
2. memory
3. relevant_memory
4. history
5. current_request
```

### 5.1 各分区的职责

| 分区 | 内容 | 优先级 |
|---|---|---|
| Prefix | Agent 规则、工具合同、工作区、恢复状态 | 高 |
| Working Memory | 当前目标、最近文件、文件摘要 | 高 |
| Relevant Memory | 根据当前请求召回的 Episodic/Durable 笔记 | 中 |
| History | 近期对话和工具轨迹 | 中 |
| Current Request | 当前用户原始请求 | 最高且不裁剪 |

### 5.2 超预算处理

默认使用字符预算。超预算时按优先级逐步压缩：

1. 减少 Relevant Memory；
2. 压缩较早 History；
3. 压缩 Working Memory；
4. 最后才裁剪 Prefix 的低优先级内容；
5. Current Request 始终保留。

历史压缩并非只取最后 N 个字符：

- 最近工具轨迹优先；
- 旧的重复读取可以合并；
- 已有新鲜文件摘要时，用摘要替代旧的长读取结果；
- 每次预算决策写入 Prompt Metadata。

### 5.3 为什么保留 Prompt Metadata

如果评测只记录最终答案，很难解释失败是模型能力、上下文丢失还是预算过小。Metadata 会记录各分区长度、压缩步骤、召回条目和 Prefix 状态，可用于 Trace 与消融实验。

面试表达：

> 上下文管理的核心不是“截断”，而是定义哪些信息在预算不足时可以先牺牲，以及哪些不变量必须保留。

## 6. 模型适配层

`zzcode/models.py` 使用窄接口隔离不同 Provider。

### 6.1 OpenAI-compatible

- 兼容 JSON 与 SSE 响应；
- 提取 text、usage 和 cache 信息；
- 对 reasoning-only 但没有可执行文本的响应给出明确错误；
- 网络错误和部分服务端错误采用有限重试。

### 6.2 Anthropic-compatible

- 转换 Messages 请求；
- 提取 content blocks；
- 保持 Runtime 看到的仍是纯文本工具协议。

### 6.3 Ollama

- 支持本地模型和独立 host；
- 复用同一个 Runtime 合同。

### 6.4 为什么不直接在 Runtime 写 Provider if/else

Provider 适配和 Agent 状态机是不同变化轴。把它们拆开后：

- Runtime 测试不依赖网络；
- Provider 响应解析可以单独测试；
- 新增后端不会修改工具循环；
- Evaluation 可以固定 Provider 配置并记录到 Manifest。

## 7. `ZZCode.ask()`：受控 ReAct 式循环

核心代码位于 `zzcode/runtime.py`。

```text
用户请求
  → 创建 TaskState / RunStore
  → 写 run_started
  → while tool_steps < max_steps and attempts < max_attempts
       → ContextManager.build
       → ModelClient.complete
       → parse(raw)
           ├─ tool
           │    → run_tool
           │    → history / memory / checkpoint / trace
           │    → 下一轮
           ├─ final
           │    → completed
           │    → durable promotion
           │    → final checkpoint / report
           └─ retry
                → 保存纠错提示
                → 下一轮或 retry limit
  → step limit / retry limit / model error
```

### 7.1 attempts 与 tool_steps 为什么分开

- `attempts`：模型被调用了多少次；
- `tool_steps`：真正执行了多少次工具动作。

Malformed response 应消耗模型尝试，但不应该伪装成成功工具步骤。拆开后可以分别限制协议异常和工具预算。

### 7.2 模型输出协议

支持两类 Tool Call：

```xml
<tool>{"name":"read_file","args":{"path":"README.md"}}</tool>
```

以及适合多行文件的 XML 风格结构。最终答案使用：

```xml
<final>...</final>
```

返回格式错误时，Runtime 不直接执行猜测出来的命令，而是生成结构化 retry notice。

### 7.3 当前完成语义的边界

当前产品 Runtime 接收到有效 `<final>` 就可以结束；它没有 Phase 6.5 那种强制 Coding 模式、专用 Verify 工具或 Patch 完成门禁。

Evaluation 在 Runtime 外部处理这个问题：

- Agent 无修改 → `EMPTY_PATCH`；
- Patch 非空 → 独立 Grader 测试；
- 最终回答宣称成功但测试失败 → 仍然是 `NO` 或 `PARTIAL`。

这是刻意保持产品 Agent 简单、把能力判定放在评测层的设计。

## 8. Tool Gateway：模型不能直达文件系统

工具定义在 `zzcode/tools.py`，所有执行统一经过 `ZZCode.run_tool()`。

### 8.1 工具集合

| 工具 | 作用 | 风险级别 |
|---|---|---|
| `list_files` | 浏览目录 | 只读 |
| `read_file` | 按行读取文本 | 只读 |
| `search` | 使用 rg 或 fallback 搜索 | 只读 |
| `write_file` | 创建或覆盖文本文件 | 写操作 |
| `patch_file` | 精确替换唯一文本块 | 写操作 |
| `run_shell` | 执行受时限命令 | 高风险 |
| `delegate` | 受限只读子 Agent 调查 | 只读、限深度 |

### 8.2 执行流水线

```text
工具名存在？
  → 参数完整、类型和范围正确？
  → 路径 resolve 后仍在 workspace？
  → 是否访问 private path？
  → 是否连续重复同一调用？
  → 风险工具是否通过 approval policy？
  → 执行前工作区快照
  → 执行工具
  → 执行后工作区快照与 Diff
  → 状态分类与 Trace
```

### 8.3 路径安全

Runtime 使用解析后的绝对路径检查 workspace 边界，同时防止：

- `../` 路径逃逸；
- 绝对路径越界；
- 符号链接解析后跳出仓库；
- `.env` 等私密路径被文件工具读取。

### 8.4 Shell 环境

普通产品模式中 Shell 只继承 allowlist 环境变量，并对 Trace/Report 做 Secret 脱敏。

正式 Evaluation 中，`run_shell` 被替换为 `DockerToolSandbox`：

- `network=none`；
- CPU、内存和 PID 限制；
- read-only rootfs；
- workspace 是唯一可写 bind mount；
- drop all capabilities；
- `no-new-privileges`；
- 超时后强制清理容器。

### 8.5 `patch_file` 为什么要求唯一命中

如果 `old_text` 匹配多个位置，自动替换会产生不确定结果。ZZCode 要求精确命中一次，失败时把错误反馈给模型重新定位。

## 9. Working、Episodic、Durable 三层记忆

`zzcode/memory.py` 将完整 History 和可复用知识分开。

### 9.1 Working Memory

保存当前任务所需的短期状态：

- task summary；
- 最近文件；
- 文件摘要；
- 当前工作集。

### 9.2 Episodic Memory

保存少量跨轮事实与过程笔记：

- 文本；
- tags；
- source；
- timestamp；
- kind。

召回使用透明的词法匹配、tag、时间和顺序，不依赖向量数据库。

### 9.3 Durable Memory

用于长期项目约定、关键决策、依赖事实和用户偏好。它不是自动把所有对话永久保存，而是：

1. 用户表达明确的保存意图；
2. 最终回答包含可识别的稳定事实；
3. 通过 Secret、临时状态和噪声过滤；
4. 写入 `.zzcode/memory/`。

### 9.4 Freshness

文件摘要保存内容哈希。发生以下情况时旧摘要失效：

- `write_file`；
- `patch_file`；
- 恢复 Session 时磁盘内容已经变化。

这防止 Agent 用旧摘要推理新代码。

面试表达：

> 记忆不是无限追加聊天记录，而是有限工作集、可召回经历和显式长期事实；代码记忆还必须带 freshness，否则恢复能力会放大错误。

## 10. Session、Checkpoint、TaskState 与 RunStore

这些对象回答的问题不同：

| 对象 | 回答的问题 |
|---|---|
| Session | 跨轮对话和记忆如何继续 |
| TaskState | 当前一次请求执行到哪里、为什么停止 |
| Checkpoint | 恢复时从哪个状态锚点继续 |
| Trace | 每一步具体发生了什么 |
| Report | 一次运行最终结果和聚合元数据是什么 |

### 10.1 持久化目录

```text
.zzcode/
├── sessions/<session-id>.json
├── memory/
└── runs/<run-id>/
    ├── task_state.json
    ├── trace.jsonl
    └── report.json
```

### 10.2 TaskState

关键字段包括：

- status；
- attempts；
- tool_steps；
- last_tool；
- stop_reason；
- final_answer；
- checkpoint_id；
- resume_status。

`status` 和 `stop_reason` 分开，便于统计“任务是否完成”和“为什么停止”。

### 10.3 Checkpoint 恢复判断

恢复状态包括：

- `full-valid`：结构、文件和运行身份一致；
- `partial-stale`：关键文件已经变化；
- `workspace-mismatch`：模型、审批策略、工具签名或工作区指纹改变；
- `schema-mismatch`：Checkpoint 版本不兼容。

### 10.4 为什么 Trace 使用 JSONL

逐行追加具有两个优点：

- 运行中崩溃时，之前事件仍然存在；
- 可以流式分析，无需每步重写完整 JSON。

TaskState 和最终 Report 使用原子替换写入，避免半个 JSON 文件。

## 11. 一项普通 Coding 请求的完整时序

```text
用户：修复模型响应为空的问题
  │
  ▼
CLI 创建 WorkspaceContext、ModelClient、SessionStore、RunStore
  │
  ▼
ZZCode.ask 创建 TaskState，记录 run_started
  │
  ▼
ContextManager 组装 Prefix / Memory / History / Request
  │
  ▼
ModelClient 返回 read_file Tool Call
  │
  ▼
run_tool 校验工具、路径、参数与审批
  │
  ▼
读取 models.py，结果写 History / Memory / Trace / Checkpoint
  │
  ▼
下一轮模型 search / read / patch_file / run_shell
  │
  ▼
文件修改后旧摘要失效，Trace 记录 affected paths 和 diff summary
  │
  ▼
模型返回 <final>
  │
  ▼
TaskState completed → Durable 过滤 → final checkpoint → report.json
```

这里的产品链路只说明 Agent 如何执行，不代表修复一定正确。正确性由 Evaluation Grader 决定。

## 12. 为什么需要 SWE-bench 式 Evaluation

旧 benchmark 采用 scripted output 驱动 FakeModel，能够证明 Runtime 对固定 Tool Call 的处理正确，但不能证明真实模型能独立解决未知缺陷。

SWE-bench 式评测的关键变化是：

```text
旧模式：已知答案 → FakeModel → Agent → 公开 verifier

新模式：公开问题 → 真实 Agent → Git Patch
      → 独立干净仓库 → 私有测试 → 可执行评分
```

它解决四个问题：

1. 答案不进入模型输入；
2. Agent 与 Grader 解耦；
3. 新行为与回归行为分开统计；
4. 每次运行配置、Patch、日志和测试结果都可以复现。

## 13. Repo Task 数据模型

Repo Task 是一个完整仓库级工程任务，至少包含：

- `instance_id`；
- `repo`；
- `base_commit`；
- `problem_statement`；
- `environment_id`；
- 公开 metadata；
- 私有 Gold Patch；
- 私有 Test Patch；
- FAIL_TO_PASS；
- PASS_TO_PASS。

### 13.1 Public 目录

```text
evaluation/datasets/zzcode-bench-v1/
├── manifest.jsonl
├── dataset-card.md
├── dataset-lock.json
├── splits/
│   ├── dev.txt
│   ├── test.txt
│   └── all.txt
└── instances/<instance-id>/
    ├── problem_statement.md
    └── task.json
```

Agent 可以读取 Public 数据，但这里不能包含：

- Gold Patch；
- hidden test 代码；
- F2P/P2P selector；
- Grader 私有路径；
- 暴露正确实现的提示。

### 13.2 Private 目录

```text
evaluation/private/zzcode-bench-v1/<instance-id>/
├── grading.json
├── gold.patch
└── test.patch
```

Private 只由数据校验和 Grader 读取，不挂载到 Agent inference workspace。

### 13.3 Dataset Lock

`dataset-lock.json` 固定：

- split；
- task count；
- 完整 dataset digest。

Digest 覆盖公开任务、Gold Patch、Test Patch 和 F2P/P2P selector。任务内容被修改后，正式运行会因为 lock mismatch 停止。

## 14. 一项 Repo Task 的完整评测流程

下面以 `ZZCODE-BUG-001` 为例：

```text
manifest.jsonl 中读取 ZZCODE-BUG-001
  │
  ├─ base_commit
  ├─ problem_statement_path
  └─ environment_id
  │
  ▼
Dataset Loader 读取公开题面
  │
  ▼
Harness 在私有根目录读取 grading.json
  │
  ├─ gold.patch
  ├─ test.patch
  ├─ FAIL_TO_PASS
  └─ PASS_TO_PASS
  │
  ▼
Null Validation
  │  干净 checkout(base_commit)
  │  不应用修复
  │  注入 hidden tests
  │  F2P 必须至少一个失败，P2P 必须全过
  │
  ▼
Gold Validation ×3
  │  每次重新 checkout(base_commit)
  │  git apply gold.patch
  │  注入 hidden tests
  │  F2P/P2P 必须连续三次全部通过
  │
  ▼
Inference Workspace
  │  独立 checkout(base_commit)
  │  只传入 problem statement
  │  真实 ZZCode Agent 修改代码
  │
  ▼
git diff HEAD --binary
  │
  ├─ patch.diff
  └─ predictions.jsonl 一行
  │
  ▼
Grading Workspace
  │  再次独立 checkout(base_commit)
  │  Patch Safety
  │  git apply model_patch
  │  注入 test.patch
  │
  ▼
Docker 执行 F2P / P2P
  │
  ▼
FULL / PARTIAL / NO / AGENT_ERROR / INFRA_ERROR / DATASET_ERROR
  │
  ▼
report.json + results.json + report.md
```

## 15. `git diff`、Patch 与 `predictions.jsonl`

### 15.1 `git diff`

Agent 在 inference workspace 修改文件后，Harness 执行 Git diff，得到相对 base commit 的标准差异：

```diff
diff --git a/zzcode/models.py b/zzcode/models.py
--- a/zzcode/models.py
+++ b/zzcode/models.py
@@ ...
-old behavior
+new behavior
```

这个差异就是 `model_patch`。

### 15.2 为什么只传 Patch

Grader 不读取 Agent History、Memory 或最终回答，只接收 Patch。这样可以保证：

- 评分只看仓库最终改动；
- Grader 不依赖 Agent 内部实现；
- 不同 Agent 可以复用同一评分协议；
- Patch 可以独立保存和复现。

### 15.3 `predictions.jsonl`

每一行代表一个任务的模型预测：

```json
{"instance_id":"ZZCODE-BUG-001","model_name_or_path":"provider/model","model_patch":"diff --git ..."}
```

它与 SWE-bench 的核心提交形态兼容：任务 ID、模型标识、模型 Patch。

## 16. Null、Gold、F2P 和 P2P

### 16.1 Null Validation

Null 的目的是证明任务在 base commit 上真实存在。

合格条件：

- 测试能够正常收集和执行；
- P2P 全过；
- F2P 不能全过；
- 结果应为 `NO` 或 `PARTIAL`，不能是 `FULL`。

如果 Null 已经 FULL，说明题目没有缺陷或 hidden test 无效。

### 16.2 Gold Validation

Gold 的目的是证明任务存在已知正确修复，且 Grader 可以识别。

连续执行三次是为了排除：

- 随机测试；
- 时间依赖；
- 外部网络依赖；
- 容器环境漂移；
- 测试顺序污染。

### 16.3 FAIL_TO_PASS

修复前失败、修复后应通过的新行为测试。

```text
F2P rate = 通过的 FAIL_TO_PASS 数 / FAIL_TO_PASS 总数
```

### 16.4 PASS_TO_PASS

修复前通过、修复后仍必须通过的回归测试。

```text
P2P rate = 通过的 PASS_TO_PASS 数 / PASS_TO_PASS 总数
```

### 16.5 ResolvedStatus

| 状态 | 含义 |
|---|---|
| `FULL` | F2P=100% 且 P2P=100% |
| `PARTIAL` | 修复了一部分 F2P，P2P 未破坏 |
| `NO` | 没有有效修复或测试要求未满足 |
| `AGENT_ERROR` | 空 Patch、Agent 超时、工具失败、安全违规等 |
| `INFRA_ERROR` | Docker、Provider、文件系统等基础设施错误 |
| `DATASET_ERROR` | Null/Gold 门禁、数据 schema 或私有包错误 |

## 17. Inference Adapter 与 Agent/Grader 解耦

Evaluation 使用 `ZZCodeAgentAdapter` 将 Repo Task 转换成一次真实 Agent 运行。

### 17.1 独立 Worker

Adapter 启动独立 Python Worker，原因包括：

- Agent 总超时可终止整个进程组；
- 目标仓库可能是旧版 zzcode，不能错误导入目标 checkout 中的 Harness；
- stdout/stderr 可以独立保存和脱敏；
- Agent 崩溃不会直接破坏批次 Runner。

### 17.2 Agent 输入

Worker 只获得：

- 公开 `TaskInstance`；
- 固定 Provider 配置；
- inference workspace；
- Agent artifact 目录。

它不会获得 `PrivateTestSpec`。

### 17.3 Agent 输出分类

- 非空 Patch：生成 `Prediction`；
- 正常结束但无修改：`EMPTY_PATCH`；
- Provider 不可用：`PROVIDER_UNAVAILABLE`；
- 超时：`AGENT_TIMEOUT`；
- Runtime/Tool 异常：`TOOL_FAILURE`；
- Worker 基础设施异常：`INFRASTRUCTURE_ERROR`。

即使 Agent 失败，Runner 仍然保存 `agent_result.json` 和任务报告，不丢失失败样本。

## 18. Docker 隔离与 Patch Safety

### 18.1 Agent Tool Plane

Agent 可以修改 inference workspace，但 Shell 进入短生命周期 Docker：

- 网络关闭；
- 资源受限；
- 容器 rootfs 只读；
- workspace mount 可写；
- 不继承 Provider Key；
- 超时清理。

### 18.2 Grading Plane

Grader 使用另一套严格容器策略：

- `network=none`；
- immutable image digest；
- non-root user；
- read-only rootfs；
- drop capabilities；
- `no-new-privileges`；
- CPU、内存、PID 和 tmpfs 限制；
- mount source allowlist；
- `/workspace` 只读挂载；
- `/artifacts` 可写挂载；
- 超时后移除容器。

### 18.3 Patch Safety

在 `git apply` 之前检查：

- Patch 是否为空；
- 是否有合法 `diff --git` header；
- 是否路径逃逸；
- 是否修改 `.git`、`.env`、`.zzcode`、`evaluation`、`hidden_tests`、`private`、`tests`；
- 是否包含 rename/copy；
- 是否包含 binary 或 symlink；
- 修改文件数和行数是否超限。

为什么保护 `tests/`：正式任务中 Agent 不能通过删除或改写评分相关测试来获得高分。

## 19. 假设迁移完成后的统一测试体系

本节按用户要求，假设原有 105 个产品测试和 12 个旧 benchmark 已经完成迁移。

```text
tests/
├── unit/                         纯逻辑和状态机
├── integration/                  Runtime、Provider mock、恢复和 Artifact
├── security/                     路径、Secret、Shell、委派边界
└── contract/                     Schema、协议与持久化合同

evaluation/tests/
├── unit/                         Dataset、Schema、Grader、Parser、Report
├── integration/                  Workspace、Adapter、Vertical Slice
├── security/                     public/private、Patch 与 mount 隔离
└── golden/                       Harness 的确定性黄金链路

evaluation/datasets/
├── smoke/                        真实模型链路 smoke，不计 Pass@1
└── zzcode-bench-v1/              正式 Internal Repo Tasks

evaluation/private/               hidden tests / Gold / selectors

evaluation/diagnostics/
├── context/
├── memory/
└── resume/
```

### 19.1 各层职责

| 层次 | 测什么 | 是否允许 Stub | 是否计入 Pass@1 |
|---|---|---:|---:|
| Product Unit | 纯函数、状态机、记忆、Context | 是 | 否 |
| Product Integration | Agent Loop、工具恢复、Artifact | 是，但不能带任务答案 | 否 |
| Security | 路径、Secret、Shell、Private Leakage | 是 | 否，属于发布门禁 |
| Evaluation Harness | Dataset、Patch、Grader、Docker、Report | 是，测试 Harness 合同 | 否 |
| Diagnostics | Context/Memory/Resume 消融 | 按实验定义 | 否 |
| Smoke | 真实 Provider 是否能走完整链路 | 否 | 否 |
| Formal Repo Task | 未知任务可执行修复能力 | 禁止 | 是 |

### 19.2 Stub 使用边界

迁移后不再使用 `FakeModelClient + SCRIPTED_MODEL_OUTPUTS` 生成正式成绩。

允许的 Stub：

- 返回 malformed tool 测解析恢复；
- 返回固定 stop reason 测状态机；
- 模拟 Provider 5xx 或超时；
- 构造一次错误 evidence 或恢复轨迹；
- 验证 Context/Memory 不变量。

禁止的 Stub：

- 携带某项 Repo Task 的 Gold Patch；
- 在 Formal Runner 中替代真实模型；
- 把 scripted benchmark pass rate 写成 Coding 能力成绩。

## 20. 假设完成后的 12 个旧 Benchmark 迁移结果

| 旧任务 | 迁移后位置 | 新职责 | 计入 Pass@1 |
|---|---|---|---:|
| `readme_intro_locked` | `evaluation/datasets/smoke/` | 真实模型 Patch 链路 smoke | 否 |
| `readme_schema_note` | `evaluation/datasets/smoke/` | 文档修改 smoke | 否 |
| `sample_beta_locked` | `evaluation/datasets/smoke/` | 单文件修改 smoke | 否 |
| `sample_gamma_locked` | `evaluation/datasets/smoke/` | 单文件修改 smoke | 否 |
| `invalid_patch_recovery` | `tests/integration/agent_recovery` | malformed tool 后恢复 | 否 |
| `path_escape_recovery` | `tests/security/path_boundaries` | 路径越界拒绝 | 否 |
| `repeated_read_recovery` | `tests/integration/agent_recovery` | 重复调用保护 | 否 |
| `context_reduction_checkpoint` | `evaluation/diagnostics/context` | 压缩与 checkpoint 指标 | 否 |
| `freshness_reanchor_resume` | `evaluation/diagnostics/resume` | stale 文件重锚定 | 否 |
| `workspace_mismatch_resume` | `evaluation/diagnostics/resume` | workspace drift 检测 | 否 |
| `durable_promotion_accept` | `tests/contract/durable_memory` | 长期记忆晋升合同 | 否 |
| `durable_promotion_reject` | `tests/security/memory_redaction` | Secret/临时事实拒绝 | 否 |

迁移的核心不是“把文件移动到新目录”，而是重新定义每项测试回答的问题。

## 21. 假设测试与评测结果

> 本节是面试演示假设，正式使用前必须用真实 Run Artifact 替换。

### 21.1 迁移后自动化测试

| Suite | 假设结果 | 说明 |
|---|---:|---|
| Product unit/integration/security/contract | 105/105 passed | 两个历史基线问题已单独修复或更新断言 |
| Evaluation Harness | 127/127 passed | 包括迁移后新增测试 |
| Docker isolation integration | 7/7 passed | 含 mount、network、timeout、cleanup |
| Legacy migration mapping | 12/12 covered | 每项都有新归属与退出检查 |
| Real-model smoke | 4/4 produced valid patch | 不计能力成绩 |
| Dataset Null gate | 8/8 valid | 全部非 FULL、P2P 100% |
| Dataset Gold×3 | 24/24 FULL | 8 题各连续 3 次 |
| Private leakage scan | 0 finding | 公开 Payload 无私有评分字段 |

### 21.2 假设正式 Internal Pass@1

固定条件：

- dataset：`zzcode-bench-v1`；
- split：`test`，一次固定运行；
- task count：4；
- temperature：0；
- 每题单次采样；
- Agent commit、dataset digest、model、container image digest 固定；
- 真实模型，不使用 Fake、Gold 或重试挑最好结果。

假设结果：

| 指标 | 结果 |
|---|---:|
| Dataset gate passed | 4/4，100% |
| Patch generated | 4/4，100% |
| FULL | 3/4 |
| PARTIAL | 1/4 |
| NO | 0/4 |
| Pass@1 / Resolution Rate | 75.0% |
| Aggregate F2P | 87.5% |
| Aggregate P2P | 100% |
| Safety violations | 0 |
| Private leakage | 0 |

为了分析全部任务，还可以单独报告一次冻结的 `all` split 探索性运行：5/8 FULL、2/8 PARTIAL、1/8 NO，Resolution Rate 62.5%。这个全量数字包含 dev 任务，不能替代 held-out test split 成绩。

### 21.3 如何解释这个结果

正确表达：

> 在 4 项 held-out test Repo Task 上，固定模型单次采样解决 3 项，Pass@1 为 75%。全量 8 题的探索性汇总是 5 项 FULL、2 项 PARTIAL、1 项 NO，Resolution Rate 62.5%。P2P 为 100%，说明已生成的 Patch 没有破坏选定回归行为；PARTIAL 主要来自跨文件覆盖不完整。这个小数据集证明的是评测闭环和内部基线，不代表模型在官方 SWE-bench 上的成绩。

错误表达：

- “我的 Agent 达到了 SWE-bench 62.5%”；
- “P2P 100% 就代表没有任何回归”；
- “Gold 100% 说明模型能力很强”；
- “Smoke 4/4 就是 Pass@1 100%”。

### 21.4 指标边界

- Pass@1 只由 `FULL / task_count` 决定；
- P2P 只覆盖选中的回归测试，不等于全仓库无回归；
- Gold 证明数据和 Grader 有效，不是模型成绩；
- 8 题样本太小，不适合宣称通用能力；
- Internal Task 与官方 SWE-bench 必须分表报告。

## 22. 运行产物与可复现性

一次 Evaluation Run：

```text
evaluation/runs/<run-id>/
├── run_manifest.json
├── predictions.jsonl
├── instance_results.jsonl
├── results.json
├── report.md
└── instances/<instance-id>/
    ├── validation/
    │   ├── null/
    │   ├── gold-1/
    │   ├── gold-2/
    │   ├── gold-3/
    │   └── gate.json
    ├── agent/
    │   ├── agent_request.json
    │   ├── agent_response.json
    │   ├── stdout/stderr logs
    │   └── runtime/
    ├── patch.diff
    ├── grading/
    │   ├── f2p.xml / f2p.log / f2p.result.json
    │   └── p2p.xml / p2p.log / p2p.result.json
    └── report.json
```

`run_manifest.json` 固定：

- dataset name 和 digest；
- split；
- Agent Git commit；
- Provider 和 model；
- temperature、top_p、step/token/timeout；
- Docker image digest；
- CPU、内存和 PID 限制；
- Run 生命周期时间。

这使“同一个模型为什么两次结果不同”可以从配置、代码、数据和环境四个维度排查。

## 23. 状态与错误分类

### 23.1 Run 生命周期

```text
CREATED → RUNNING → COMPLETED
                  ├→ FAILED
                  └→ INTERRUPTED
```

终态 Artifact 不允许覆盖，同一 Run ID 不能重复创建。

### 23.2 三类责任域

| 类别 | 示例 | 处理方式 |
|---|---|---|
| `DATASET_ERROR` | Null/Gold 失败、schema 错误、test patch 无效 | 修数据，不归咎模型 |
| `INFRA_ERROR` | Docker daemon、镜像、磁盘、Worker 启动失败 | 修环境，可重试 |
| `AGENT_ERROR` | 空 Patch、Patch apply 失败、测试失败、安全违规 | 计入 Agent 结果 |

为什么必须分开：如果 Docker 故障也算模型 NO，模型成绩会被基础设施噪声污染；如果数据无效仍调用模型，则会浪费成本并生成不可解释结果。

## 24. 测试命令与发布门禁

假设迁移完成后的常用命令：

```bash
# 产品快速测试
uv run pytest -q tests/unit tests/contract

# 产品集成与安全
uv run pytest -q tests/integration tests/security

# Evaluation Harness，不运行 Docker
uv run pytest -q evaluation/tests -m "not docker"

# 校验冻结数据集
uv run python scripts/validate_eval_dataset.py \
  --public-root evaluation/datasets/zzcode-bench-v1 \
  --private-root evaluation/private \
  --split all

# Null + Gold×3 Docker 稳定性门禁，不调用模型
RUN_DOCKER_TESTS=1 uv run pytest -q \
  evaluation/tests/integration/test_phase6_dataset_docker.py

# 正式真实模型 test split
uv run python scripts/run_internal_eval.py \
  --split test \
  --provider openai \
  --model provider/model-name \
  --base-url https://provider.example/v1

# 从已有 JSON 重新渲染报告
uv run python scripts/render_eval_report.py evaluation/runs/<run-id>
```

发布门禁顺序：

```text
Product tests
  → Harness tests
  → Dataset lock / leakage
  → Docker Null/Gold stability
  → Real-model smoke
  → Formal Pass@1
```

## 25. 可靠性与安全设计速记

| 风险 | ZZCode 的处理 |
|---|---|
| 模型输出格式错误 | parse + retry notice + attempts limit |
| 工具无限循环 | max_steps、max_attempts、重复调用拒绝 |
| 路径逃逸 | resolve + workspace boundary + symlink 防护 |
| 读取 `.env` | private path policy |
| Secret 进入 Shell | allowlist 环境 + Evaluation 容器不继承 Provider Key |
| Secret 进入 Artifact | trace/report redaction |
| Shell 网络外发 | Evaluation Tool Sandbox `network=none` |
| Shell 资源耗尽 | CPU / memory / PID / timeout |
| 恢复使用过期文件 | freshness hash + stale invalidation |
| 工作区或运行配置漂移 | workspace fingerprint + runtime identity |
| Agent 只说完成不修改 | Adapter 标记 `EMPTY_PATCH` |
| Agent 修改测试骗分 | Patch Safety 保护 tests/private/evaluation |
| 新修复破坏旧行为 | P2P |
| 数据题目本身无效 | Null + Gold×3 |
| 模型失败导致批次中断 | per-task failure artifact，继续剩余任务 |
| Docker 故障污染模型成绩 | INFRA_ERROR 单独统计 |
| 评测结果不可复现 | dataset/agent/image digest + immutable artifacts |

## 26. 面试讲解模板

### 26.1 30 秒版本

ZZCode 是一个本地 Coding Agent Harness。它用显式 ReAct 式循环把模型、工作区和工具连接起来，ContextManager 负责分区预算和压缩，Tool Gateway 负责路径、参数、审批和 Shell 安全，Session、Memory、Checkpoint 和 Trace 负责跨轮恢复与可观测性。为了避免 Agent 自评，我还实现了 SWE-bench 式 Evaluation：Agent 只提交 Git Patch，Grader 在独立干净仓库注入 Hidden Tests，通过 Docker 执行 F2P/P2P，并生成 Pass@1 和可复现报告。

### 26.2 两分钟版本

可以按五点回答：

1. **Runtime**：`ZZCode.ask()` 控制模型调用、工具步骤、格式重试和停止原因，模型只决定下一步。
2. **Context/Memory**：Prompt 拆成 Prefix、Working Memory、Relevant Memory、History 和 Current Request；按预算压缩，文件摘要带 freshness。
3. **Tool Safety**：工具显式注册，统一经过参数、路径、防重复和审批；Evaluation Shell 在禁网 Docker 中执行。
4. **Recovery/Observability**：Session 保存跨轮状态，TaskState、Checkpoint、Trace 和 Report 分别负责状态、恢复锚点和审计。
5. **Executable Evaluation**：Repo Task 固定 base commit；先做 Null 和 Gold×3，再运行真实 Agent 收集 Git Patch；独立 Grader 执行 Hidden F2P/P2P，只有两组都 100% 才是 FULL。

### 26.3 五分钟版本

> 我做 ZZCode 时主要解决两个问题：怎样把模型变成可控的 Coding Agent，以及怎样可信地测它。
>
> 产品侧的入口在 CLI，CLI 只做 Provider、Workspace、Session 和预算的装配。核心是 `ZZCode.ask()`，每轮先由 ContextManager 组装 Prompt，然后调用模型。模型必须返回一个 Tool Call 或 Final。Tool Call 不会直接执行，而是进入统一 Gateway，依次经过 Registry、参数、路径、重复调用、审批和执行前后 Diff。结果写回 History、Working Memory、TaskState、Trace 和 Checkpoint，再进入下一轮。
>
> Context 不是无限追加历史，而是拆成稳定 Prefix、Working Memory、Relevant Memory、History 和当前请求。超预算时分级压缩，当前请求始终保留。Memory 分 Working、Episodic 和 Durable，文件摘要带哈希；文件变化后旧摘要失效。恢复时还会检查 workspace fingerprint 和 runtime identity，避免把旧状态错误套到新环境。
>
> 但 Agent 最终说“修好了”不可信，所以我实现了独立 Evaluation。每个 Repo Task 固定 base commit，Public 只有问题描述，Private 保存 Gold、Hidden Tests 和 F2P/P2P。每题先做 Null，证明缺陷存在；再做 Gold×3，证明参考修复稳定。真实 Agent 在独立 workspace 修改代码，Harness 只收集 git diff 写成 predictions.jsonl。Grader 再开一份干净仓库，做 Patch Safety、git apply、注入 Hidden Tests，并在禁网 Docker 中分别跑 F2P 和 P2P。只有新缺陷测试和旧回归测试都通过才算 FULL。
>
> 假设迁移完成后的 held-out test 结果是 4 题解决 3 题，Pass@1 75%；全量探索性汇总是 5/8，P2P 100%。我会把它解释为内部小数据集基线，而不是官方 SWE-bench 成绩。这个项目最有价值的部分，是把 Runtime 合同、工具安全、恢复、可观测性和可执行评分连成了一条完整链路。

## 27. 高频追问与回答

### 为什么不用 LangChain 或现成 Agent Framework？

这个项目的目标是理解和展示 Agent Harness 的核心机制，因此使用显式循环实现预算、解析、Context、Memory、工具安全、Checkpoint 和 Trace。现成框架能提高开发速度，但会隐藏一些面试重点。工程上并不排斥后续替换模型层或工具层。

### 为什么叫 Harness，而不是完整 IDE Agent？

它提供模型外部执行框架，但没有 IDE UI、远程任务队列、多租户权限和大规模分布式调度，因此更准确的定位是轻量本地 Coding Agent Harness。

### 为什么不用向量数据库做仓库索引和记忆？

当前目标是小型本地 Agent，选择 lazy inspection 和透明词法召回，成本低、结果可解释。缺点是大仓库语义召回较弱；扩展时可以增加代码索引和 Embedding，但仍应保留 freshness 与权限边界。

### Context 字符预算准确吗？

不完全准确。字符预算 Provider 无关、实现简单，但不同语言和 tokenizer 的换算不同。生产优化可以引入模型特定 tokenizer；当前 Metadata 已经为替换预算估算器留出位置。

### 为什么要有 Working、Episodic、Durable 三层？

三层生命周期不同：Working 服务当前任务，Episodic 保存少量跨轮经历，Durable 保存经过显式晋升的稳定事实。全部混在 History 中会导致上下文膨胀，全部自动长期保存又会积累噪声和 Secret。

### Checkpoint 和 Session 有什么区别？

Session 是跨轮状态容器；Checkpoint 是某个时间点的恢复锚点，包含当前目标、关键文件 freshness 和 runtime identity。一个 Session 可以有多个 Checkpoint。

### Workspace Fingerprint 为什么不哈希所有文件？

全仓库哈希启动成本高，且构建产物变化会产生大量噪声。ZZCode 用 Git/文档快照做全局低成本指纹，再对关键文件做内容 freshness，属于分层检测。

### 如何防止 Agent 读取 `.env`？

Workspace 列表和工具参数校验复用私密路径定义；路径解析后检查相对部分，直接读取或指定搜索 `.env` 会被拒绝。Evaluation 容器也不挂载 Provider Secret。

### `approval=auto` 是否安全？

普通本地模式下 auto 代表用户选择自动执行风险工具，它不等同于系统沙箱。正式 Evaluation 会进一步把 Shell 放入禁网、受资源限制的 Docker。生产环境还应按用户、仓库和命令策略增加更强隔离。

### 为什么阻止重复工具调用？

模型容易连续读取同一范围或重复执行相同命令。重复拒绝可以节省预算并促使模型改变策略。但它是启发式保护，不能替代完整规划器。

### 为什么 Agent Tool Plane 和 Grading Plane 分开？

Inference 期间 Agent 需要写 workspace；Grading 期间测试只需要读取已经应用 Patch 的仓库并写 Artifact。两者权限需求不同，分开可以使用最小权限。

### 为什么 Grader 不直接在 Agent workspace 跑测试？

Agent workspace 可能残留未跟踪文件、缓存或环境污染。独立 checkout 可以证明 `model_patch` 本身足以复现修改，也防止 Grader依赖 Agent 内部状态。

### Null Validation 解决什么问题？

证明 base commit 确实未解决任务。如果不应用 Patch 已经 FULL，这道题不能区分模型能力。

### Gold Validation 为什么要三次？

一次 Gold 通过可能是偶然。连续三次可以筛掉随机、时间、外部服务或测试顺序导致的不稳定任务。

### Gold 通过是否代表 Agent 通过？

不是。Gold 是数据质量门禁，Agent 必须自己生成 Patch 并通过相同 Grader。

### F2P 和 P2P 分别测什么？

F2P 测缺陷是否被修复，P2P 测已有行为是否保持。只有两者都是 100% 才是 FULL。

### P2P 100% 是否保证没有回归？

不能。它只保证选定 P2P 集合没有回归。扩大公开产品测试和 P2P 覆盖可以提高置信度，但不能证明所有行为。

### `PARTIAL` 有什么价值？

它帮助区分“完全没有修复”和“修复了部分 F2P”。能力榜主指标仍只计 FULL，但 PARTIAL 对定位跨文件遗漏、边界覆盖不足很有价值。

### 为什么正式评测不能用 FakeLLM？

如果 FakeLLM 返回预写正确 Patch，测到的是 Harness 能否执行答案，而不是模型能否推理出答案。Stub 只适合协议和错误分支测试。

### 如何防止 Hidden Test 泄漏？

Public/Private 目录分离；Dataset Loader 检查私有字段名；Agent request 只序列化 TaskInstance；inference workspace 不挂载 private root；Grader 在 Agent 结束后才读取 Test Patch。

### 如何防止 Agent 修改测试骗分？

Patch Safety 把 tests、hidden_tests、evaluation、private 等路径设为 protected prefixes，同时拒绝 path traversal、rename、binary 和 symlink Patch。

### 为什么采用 JSONL 保存 Prediction 和 Trace？

JSONL 支持逐条追加、中断保留和流式处理。Prediction 每题一行，Trace 每事件一行，适合长批次和失败恢复。

### Pass@1 是怎样计算的？

固定模型配置下每题只采样一次，`FULL 数 / 总任务数`。不能对同一题运行多次后挑最好结果再称为 Pass@1。

### Internal Benchmark 和官方 SWE-bench 有什么区别？

Internal 使用同样的 Patch + 独立测试思想，但任务来自 zzcode，自定义环境和 Grader。官方 SWE-bench 使用官方数据、Docker 镜像和评分脚本。Internal 成绩不能写成官方 SWE-bench 成绩。

### 当前最大的局限是什么？

- 任务只有 8 项，统计显著性有限；
- 多项任务来自同一个仓库和历史 commit；
- 产品 Runtime 没有专用 Verify/完成门禁；
- Context 是字符预算和词法召回；
- 普通本地工具安全不是完整 OS 沙箱；
- 还没有官方 SWE-bench Adapter 的真实外部成绩。

## 28. 如何回答“你在这个项目里最难的工作是什么”

可以选择下面三个角度。

### 角度一：区分 Harness 正确与模型能力

> 最难的是定义可信的成功口径。旧 benchmark 使用 scripted output，容易把“框架能执行预写答案”误认为“模型能解决任务”。我把测试拆成产品回归、Harness 测试、Diagnostics、Smoke 和 Formal Repo Task，只有真实模型 Patch 经过独立 Hidden F2P/P2P 才计入 Pass@1。

### 角度二：Public/Private 与工作区隔离

> 评测最容易出现隐性泄漏。我让 Agent 只接收 Public TaskInstance，Private Test Patch 和 selector 在另一棵目录中，inference workspace 不挂载 Private；Grader 使用全新 checkout，并在 Agent 结束后注入 Hidden Tests。同时用依赖和 Payload 测试防止 Harness 代码意外把评分信息传给 Adapter。

### 角度三：可复现性和失败分类

> 模型评测天然有 Provider、代码、数据、镜像和资源配置五类变量。我把它们固定到 RunManifest，并保存每题 Patch、JUnit、日志和 Result；再把 Dataset、Infra、Agent 错误分开，避免把 Docker 故障算成模型失败。

## 29. 简历表述模板

### 29.1 精简版

> 设计并实现轻量级本地 Coding Agent Harness，覆盖显式 Agent Loop、分区 Context Budget、Working/Episodic/Durable Memory、Checkpoint 恢复、工具安全与 Trace；构建 SWE-bench 式可执行评测系统，通过版本化 Repo Task、Git Patch、Hidden F2P/P2P、Docker 隔离和可复现 Artifact 评估真实修复能力。

### 29.2 带数据版（假设结果，须替换）

> 构建 8 项 Internal Verified Repo Task 数据集与 Null/Gold×3 数据门禁，完成旧 12 项 scripted benchmark 的 smoke/integration/security/diagnostics 分层迁移；评测链路实现 Public/Private 隔离、真实 Agent Patch、Docker F2P/P2P 和 Markdown 报告。假设 held-out test 固定模型单次采样解决 3/4，Pass@1 75%，全量探索性结果 5/8，P2P 100%。

### 29.3 不应该写的表述

- “SWE-bench 得分 62.5%”——没有运行官方 SWE-bench；
- “实现 exactly-once Agent”——本项目不是分布式任务系统；
- “完全防止代码执行风险”——Docker 与应用护栏降低风险，不是形式化安全证明；
- “上下文 Token 降低 30%”——当前主要预算单位是字符，除非有真实 tokenizer 实验；
- “测试全部通过”——使用假设结果前必须先产生真实 CI Artifact。

## 30. 关键代码索引

| 内容 | 代码位置 |
|---|---|
| CLI 与依赖装配 | `zzcode/cli.py` |
| Agent 主循环 | `zzcode/runtime.py` |
| Context 分区与压缩 | `zzcode/context_manager.py` |
| Working/Episodic/Durable Memory | `zzcode/memory.py` |
| 工具 Registry 和实现 | `zzcode/tools.py` |
| 工作区与 Git 上下文 | `zzcode/workspace.py` |
| 多 Provider Adapter | `zzcode/models.py` |
| 单任务状态 | `zzcode/task_state.py` |
| Trace 与 Report 持久化 | `zzcode/run_store.py` |
| Evaluation Schema | `zzcode/evaluation/schema.py` |
| Public/Private Dataset Loader | `zzcode/evaluation/dataset.py` |
| Evaluation Vertical Runner | `zzcode/evaluation/execution/runner.py` |
| 干净 Workspace | `zzcode/evaluation/execution/workspace.py` |
| Docker Runtime | `zzcode/evaluation/execution/docker_runner.py` |
| Docker Test Executor | `zzcode/evaluation/execution/docker_test_executor.py` |
| Agent Adapter | `zzcode/evaluation/inference/zzcode_adapter.py` |
| Agent Worker | `zzcode/evaluation/inference/worker.py` |
| Agent Shell Sandbox | `zzcode/evaluation/inference/tool_sandbox.py` |
| Patch Collector | `zzcode/evaluation/inference/patch_collector.py` |
| Patch Safety | `zzcode/evaluation/grading/safety.py` |
| F2P/P2P Grader | `zzcode/evaluation/grading/grader.py` |
| Artifact Store | `zzcode/evaluation/reporting/artifacts.py` |
| Markdown Report | `zzcode/evaluation/reporting/markdown.py` |
| Internal Eval 入口 | `scripts/run_internal_eval.py` |
| Dataset 校验入口 | `scripts/validate_eval_dataset.py` |
| Report 重绘入口 | `scripts/render_eval_report.py` |
| 公开 Repo Tasks | `evaluation/datasets/zzcode-bench-v1/` |
| 私有评分包 | `evaluation/private/zzcode-bench-v1/` |

## 31. 最后速记

```text
模型负责决策，Runtime 负责控制；
WorkspaceContext 提供低成本导航，不预加载整个仓库；
Context 按区预算，当前请求不丢；
History 保存过程，Memory 保存有限可复用状态；
代码记忆必须带 freshness；
Session 用于继续，Checkpoint 用于重锚定；
TaskState 看状态，Trace 看过程，Report 看结果；
工具必须显式注册，模型不能直达文件系统；
路径、审批、环境、容器和脱敏是分层护栏；
Agent 最终回答不是评分依据，Git Patch 才是；
Inference Workspace 和 Grading Workspace 必须分开；
Public 给 Agent，Private 只给 Grader；
Null 证明题目真实，Gold×3 证明任务稳定；
F2P 看新缺陷，P2P 看选定回归；
FULL 才计入 Pass@1，PARTIAL 只做诊断；
Dataset、Infra、Agent 错误必须分开；
Fake/Stub 可以测协议，不能生成正式能力成绩；
Internal SWE-bench-style 不等于官方 SWE-bench；
所有数字都要绑定 dataset、Agent、model 和 image digest；
先证明评测可信，再讨论模型分数。
```
