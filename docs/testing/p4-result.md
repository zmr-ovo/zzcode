# P4：统一运行入口与目录重构

实现保存于 `improve_pi` 分支的工作区。本阶段将 Runtime 拆成 Coordinator、单一同步 Loop、公共请求/事件/结果和完成策略。源码、测试、脚本按职责归类，历史重复副本集中归档。目录及 API 说明见 [project-layout.md](../architecture/project-layout.md)，逐项迁移关系见 [p4-path-migration.json](../architecture/p4-path-migration.json)。

## 行为变化

- CLI Human/JSONL、SDK 和真实 Evaluation 使用同一运行入口。已有 `ZZCode.ask()` 通过同一 Loop 保留字符串和异常兼容。
- 公开事件有版本、稳定顺序、事件 ID、关联 ID 和不可变允许字段。Session、TaskState、Ledger 先提交事实，事件随后产生。
- Trace/Report 故障产生诊断，不重试已执行工具；重要存储失败停止运行。消费者关闭流时取消尚未执行的调用，保留已执行事实。
- 增加 question/investigation/code_change/auto 完成策略。代码修改的验证命令走 Gateway，计入工具及时间预算；证据绑定当前工作区/验证环境。
- 模型 Final、Runtime completed、独立评分器 resolved 分开表示。完成决定和验证操作证据保存到 TaskState；评测侧通过公开 Result 获取状态和产物路径。
- 新增 29 项 P4 契约测试，覆盖事件、取消、重要存储与观测故障、完成证据、修复后验证、JSONL 和渲染失败。

## 验证

最终基线通过，证据见 [manifest.json](../../artifacts/p4-baseline/verified/manifest.json)：

- 产品测试：228 passed（其中新增 P4 测试 29 项）。
- Evaluation：115 passed；6 项依赖 Docker/真实模型的测试按基线选择范围排除。
- 原生协议 Golden：7/7；确定性回归两轮各 12/12，语义结果一致。
- Ruff、CLI 入口、安装包导入、P3 上下文对照、仓库评测前置检查通过。
- 已配置 OpenAI-compatible 后端的只读原生工具往返通过，调用/结果 ID 匹配。

第一次完整回归发现新增 CLI 测试加载本机 `.env` 导致环境污染，已在测试中隔离环境加载；最终基线使用修复后的代码。

已通过独立 wheel 构建与仓库外导入检查：新包目录完整打包，归档副本不进入包；CLI 参数与一次经过验证的代码修改运行通过。

本机 Docker daemon 不可用，真实 Docker 仓库评分未执行。本阶段保持同步消费边界，不承诺跨线程即时停止正在执行的 `next()`；Shell 超时/中断继续清理整个进程组。当前 SDK 的 auto 按实际修改判断类型，已知代码修改任务应明确使用 code_change 并配置验证命令。
