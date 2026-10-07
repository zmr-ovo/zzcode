# 项目目录与运行入口（P4 / P5）

源码、测试、评测资产、运行证据分别有固定归属。按业务职责找文件，不再把运行、存储、工具和评测代码都放在源码根目录。

```text
zzcode/
  agent/        coordinator.py · loop.py · contracts.py · events.py · policy.py · state.py
  context/      manager.py · memory.py · workspace.py · budget.py · compaction.py
  core/         原生消息与工具协议类型
  providers/    OpenAI / Anthropic / Ollama 适配
  execution/    gateway.py · policy.py · tools.py · ledger.py · shell.py · files.py · output.py
  storage/      session.py · runs.py
  evaluation/   真实仓库评测、独立评分器、Docker 评测执行
  benchmarks/   确定性回归与实验指标计算
  cli.py        命令行界面
  runtime.py    已有公共导出的外观
  models.py     已有模型客户端导出的外观
  __init__.py · __main__.py

tests/          产品与评测系统的测试统一入口
  evaluation/   评测系统的 unit/integration/security/golden 测试
  fixtures/     共享协议录制资产
scripts/        testing/ · evaluation/ · experiments/ · docs/
evaluation/     数据集、配置、Docker 环境；本地 runs/reports/workspaces 被忽略
benchmarks/     coding_tasks.json 等确定性回归任务资产
docs/           architecture/ · testing/ · evaluation/
artifacts/      P0–P5 验证证据；冻结的历史源代码不随目录迁移改写
```

当前路径迁移关系在 `p4-path-migration.json`。旧的 ` 2` 副本和已被替代的评测重构方案／迁移矩阵已清理；需要回看历史内容时可查阅冻结的 P4/P5 源码快照。历史文档中的阶段统计仍代表当时的结果。

## 单一运行入口

```python
from zzcode import Agent, RunRequest, SessionStore, WorkspaceContext

agent = Agent(model_client, WorkspaceContext.build(workspace), SessionStore(session_dir))
request = RunRequest("修复问题", task_type="code_change", verification_commands=("python -m pytest -q",))
result = agent.run_to_completion(request)
# 逐事件消费使用 agent.run(request)，两种形式共用 agent/loop.py。
```

Coordinator 管理依赖、会话和运行工件；Loop 管理模型与工具的调度；CompletionPolicy 判断是否满足完成条件；Gateway 负责执行校验、审批和操作账本。Safety 校验集中在执行层的路径/参数校验，Approval 在 `execution/policy.py` 显式执行，不由事件消费者决定。

`ZZCode.ask(prompt) -> str` 保留原有行为，以 question 策略消费同一循环，并保持原有异常类型。新任务应使用 `RunRequest`；CLI 和真实 Evaluation 已改用新入口。确定性旧回归保留 `ask` 以便比较历史行为，读取统计改用公开 `last_result`。

## 完成判断与证据

- `question`：有效 Final 可完成。
- `investigation`：无工作区修改并给出说明可完成；发生修改时拒绝 Final。
- `code_change`：配置的验证命令全部通过，且证据仍匹配当前工作区和验证环境，才完成。无修改必要的说明也必须满足这项任务的验证配置。
- `auto`：根据运行中的工作区变化选择 question 或 code_change；已知修改任务应明确传入 code_change，避免把“没有改动”误当作问答。

验证命令也走 Gateway，计入同一工具/时间预算，保留审批、超时和进程组清理。失败后允许继续修复；Final 拒绝次数默认最多两次。缺少验证配置或预算不足时直接停止，不向模型重复请求无法补齐的运行配置。最终预算轮只允许模型总结，不执行模型提出的工具或额外验证。

`TaskState.completion` 持久化任务类型、验证命令摘要、完成决定及验证操作证据。`AgentResult.model_final` 表示模型给过 Final；`status == completed` 表示运行策略已接受；`resolved` 留给独立评分器，默认为 null。

## 事件与取消

`AgentEvent` 包含 version、event_id、run_id、sequence、type、correlation_id 和递归不可变 payload。按 event_id 去重消费。事件在对应 Session、TaskState 或操作账本事实提交之后产生，Trace 中保存相同的公开事件 ID。公开字段不包含原始工具参数、完整工具输出、模型思考或签名状态。

Run、Model、Tool 有生命周期事件；Compaction 和 Checkpoint 在提交后发布 completed。Trace/Report 失败记录到 stderr 和 `observation_errors`，不会触发工具重执行；Session/TaskState/Ledger 失败则停止并产生失败结果。

第一版是同步迭代器。调用方用 `try/finally` 或 `contextlib.closing` 关闭事件流：在 run.started/model.started 关闭不会发起模型调用，在 model.completed 关闭不会执行尚未执行的工具；批次中途关闭保留已完成工具事实并取消其余调用。关闭后终止事实保存在 Trace/`last_result`，无法再向已关闭的消费者交付终止事件。

工具事件按顺序排队，在工具提交结果后的消费边界交付；`tool.started` 不是实时 shell 输出。同步的 `next()` 正在执行工具时，不能从同一线程同时调用 close；Shell 中断/超时继续由执行层清理整个进程组，不启动后台运行线程。

## CLI

```bash
zzcode --task-type question "解释当前项目"
zzcode --task-type code_change --verify-command "python -m pytest -q" "修复问题"
zzcode --output jsonl --task-type investigation "检查问题并说明是否需要修改"
```

JSONL 仅支持单次请求，stdout 每行一个事件；欢迎信息不进入 JSONL，审批提示和诊断写 stderr。未完成或失败的单次运行返回退出码 1。Human 与 JSONL 共用 SDK 的运行入口。

## 统一执行器

`execution/commands.py` 定义不可变 CommandRequest、CommandResult 和资源限制；`shell.py` 实现 LocalExecutor，`docker.py` 实现 DockerExecutor。Gateway、完成验证、真实评测 Worker 使用相同命令契约；独立评分器复用容器资源策略，保留只读评分挂载。

`docker_policy.py` 集中容器隔离参数及检查，`workspace_copy.py` 管理受控副本、冲突核对和修改同步。容器只挂载副本，禁用网络、只读根文件系统、非 root、限制 CPU/内存/PID，镜像固定到 digest。Local 默认行为保持本机执行；Docker 预检失败没有自动回退。

Docker 命令使用 `/workspace` 路径；副本排除凭证路径、`.git`、`.zzcode`、虚拟环境及缓存。已知环境凭证同时用于文件内容扫描。镜像必须可信并预装依赖；启动检查覆盖敏感环境变量与常见凭证文件，不是对任意镜像的完整秘密审计。拒绝符号链接和特殊文件；新增私有路径或修改已有目录权限会在回传前报错。文件内容、文件权限及目录增删可同步。宿主并发修改冲突停止同步，清理失败保留不确定操作事实。

CommandResult 记录退出码、输出截断、超时、取消、OOM、实际镜像和容器身份；操作账本与验证环境签名包含执行器身份。Shell 取消或超时清理进程组／容器，不将资源失败判断为任务完成。

## 评测与测试的职责边界

- `zzcode/evaluation/` 是真实任务的评测实现：生成 Patch、独立评分、聚合结果。
- 根目录 `evaluation/` 只存评测资产和运行输出；测试已迁移到 `tests/evaluation/`。
- `zzcode/benchmarks/` 保留脚本化机制回归及 Context/Memory 等实验，结果不计入真实任务解题率。
- `tests/` 统一保存产品与评测系统测试，pytest 默认只从此目录收集，避免扫描评测工作副本。

Local 与 Docker 评分器共享 JUnit 解析、缺失测试核对、超时结果及工件写入；容器启动、隔离和清理由 Docker 评分器负责。本机与容器隔离测试覆盖不同执行边界，均保留。各阶段基线脚本显式分开产品测试和评测系统测试，避免重复计数。历史验收中的旧路径仍表示当时的目录。
