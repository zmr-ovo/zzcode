# P2：工具执行与恢复账本

当前分支：`improve_pi`。P1 提交：`09241f7`。P0/P1 冻结证据保持原样。

## 实现

- `execution/gateway.py` 是模型、公开工具方法和嵌套工具的统一入口：冻结参数、集中校验、安全判断、持久化 planned、审批、持久化 running、执行、持久化终态、返回结果。审批修改参数会拒绝；持久化失败立即中止 Run。
- `core/messages.py` 的 ToolResult 保存统一事实，包含六种状态、操作 ID、真实退出码、错误码、工作区、变更路径、摘要、超时、截断和 Artifact。三种 Provider 都序列化该对象；Trace 从它派生。
- `execution/ledger.py` 使用标准库 SQLite（synchronous=FULL）与 POSIX 单写者锁。operation_id 标识操作，session/call_id 绑定协议调用，规范化 args_hash 仅用于对比。同 call ID 返回已记录结果，不按参数全局去重；新用户请求可以重复操作。
- `execution/files.py` 保存完整前态/后态哈希，在执行前重新核对；同目录临时文件、fsync、原子替换、目录 fsync。恢复后态相同则成功，前态相同则未完成，其他状态保持冲突，绝不自动覆盖。
- `execution/shell.py` 返回结构化退出事实；流式有限读取 stdout/stderr，按进程组终止超时/取消的进程。异常、崩溃和超时后的不确定操作会阻止后续写入。
- `execution/output.py` 保存有限且脱敏的输出，展示 Head/Tail；受控 read_artifact 校验 ID、大小、摘要、软链接和行范围。读取文件不加载整个大文件；搜索显示最多 200 条，明确总数未知。
- `/operations` 列出待核对操作；`/resolve ID succeeded|failed|cancelled EVIDENCE` 由用户显式记录证据和终态，模型没有 resolve 工具。恢复和手工处理前检查已记录的 Shell 进程组/容器是否仍活跃。
- 报告分别统计模型请求、传输重试、工具提案、实际执行、拒绝、取消和成功。保留迭代上限，增加 `--max-run-seconds`（默认 300），在模型/工具调用之间检查时间预算。

## 验证

最终本地验证见 `artifacts/p2-baseline/verified/manifest.json`。故障测试包含 planned 后、running 后、替换后、终态提交前，以及真实子进程 os._exit；同时覆盖提交失败、外部编辑、活跃进程、Shell 超时、重复意图、并发写者、脱敏和输出上限。

真实后端和 Docker Smoke 独立记录；未运行不能算通过。

## 支持边界

- 本阶段提供 POSIX/macOS/Linux 执行和单写者锁，不宣称 Windows 支持。
- Shell/外部副作用没有通用 exactly-once 保证。未知操作必须人工核对；脱离进程组的后台进程及进程启动到 PID 记录之间的崩溃窗口不能由本地账本完整证明，需要外部检查。
- 文件写入保留权限位，新文件默认 0644；不保留 ACL/xattr。写入软链接被拒绝。没有外部协作锁，最后一次哈希检查与替换之间仍存在外部编辑竞态。
- 时间预算是调用间检查，正在执行的 HTTP/工具由各自超时约束，因此不是严格墙钟期限。
- 每个 Shell 输出流最多保留 64 KiB，单个 Artifact 最多 64 KiB，模型展示约 3,000 字符；Artifact 保存的是有限输出，不是无限完整日志。Patch 支持最大 8 MiB 文件。
- 持久化前脱敏已配置环境秘密及常见 OpenAI/GitHub token；不能自动识别任意业务秘密。账本不保存原始参数，私有 Session 仍按 P1 保存协议重放所需信息。
- 变更摘要来自工作区文件哈希；未加入独立 verify/diff 工具或外部服务幂等协议。恢复不使用旧测试结果证明新工作区通过。
- SQLite 事务与文件/Shell 副作用不是同一个原子事务；通过先记 running、后核对解决可检测的中断问题。
