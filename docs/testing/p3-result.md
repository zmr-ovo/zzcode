# P3：会话记录、完整请求预算与历史压缩

分支：`improve_pi`。P2 提交：`1e6d642`。P0/P1/P2 的冻结证据保持不变。

## 实现

- `session_store.py` 将 SessionStore 从 runtime 提取出来，公共导出保留。JSONL Entry 使用 schema_version、entry_id、sequence、created_at、type、payload，记录 message、tool_batch、checkpoint、compaction、state、reset。协议调用和操作 ID 在消息块中保留。
- 原始历史只追加，不因压缩被删除；单写者文件锁与 revision 检查防止竞争覆盖。文件权限 0600，追加后 fsync。最后一个未换行残片可以截断恢复，中间损坏、未知版本和序号异常明确报错。
- 旧 JSON 首次读取时先复制 `.json.backup`，在临时目录转换并验证，然后原子发布 JSONL，保留原文件。JSONL 是事实来源，index.json 仅记录可重建索引；Checkpoint 引用会话记录、Compaction 和操作账本。
- `context/budget.py` 统一计算完整请求：system、分角色消息、全部工具声明及协议余量。没有可靠 tokenizer 时使用 UTF-8 字节的保守估算，记录来源，usage 只向更保守方向校准。显式配置优先于 ModelCapabilities；默认配置窗口 32768、最大输出 4096、安全余量 1024。
- `--context-window` 和 `--max-output-tokens` 可以覆盖容量；输出还受 `--max-new-tokens` 限制。默认值是本地配置，未经后端模型信息确认，并不是各模型官方容量。保护输入超过预算时在调用前停止，返回 CONTEXT_CAPACITY_EXCEEDED。
- `ContextManager.build_request` 使用 Token 预算决定实际原生请求。原来的字符策略保留为文本展示及 Memory 检索限制；必要规则和当前用户输入不会按字符静默裁剪。工具调用、结果及推理续接状态按整组处理，近期两个组保留完整；未核对操作的完整批次不能被摘要替换。
- `context/compaction.py` 从旧历史生成确定性事实摘要：用户请求原文、工具执行状态/错误/操作引用、有限输出、最近模型报告。保存 source_range、精确来源 ID、first_retained_entry、摘要版本、来源、工作区身份和成本。合并旧的重复观察，失败/未知结论单独保留。
- 摘要作为带来源的历史观察进入 user 消息，不进入系统指令、Working/Durable Memory 或 Checkpoint 正文。原始工具输出仍按较低信任的数据处理，不透明思考块不进入摘要。
- 先持久化 Compaction 再发送请求；保存失败会停止 Run。确定性压缩不调用模型，模型请求与 token 成本为零；压缩在迭代间检查五秒本地时间预算，不承诺严格墙钟期限。保护内容或摘要仍无法容纳时明确停止。
- Shell 结果新增 execution_evidence：命令摘要、Patch Digest、工作区树摘要和环境/工具配置签名。工作区摘要包括相关未跟踪文件；修改文件、切换为不同内容的分支、改变环境或工具配置后，旧验证信息标记为 stale，不能证明当前版本通过。
- 后端明确的 context_length_exceeded/prompt_too_long 错误最多触发一次更保守重建；认证、限流及普通 Provider 错误不触发压缩重试。缓存 key 包含模型、工具/Schema 和 system 指令签名，仍用实际 cached usage 判断命中。

## 验证

最终结果见 `artifacts/p3-baseline/verified/manifest.json`。新增测试覆盖迁移失败不发布、尾部/中间损坏、竞争写入、历史不可修改、中文/代码/UTF-8 估算、工具开销、超大请求、批次完整性、约束保留、持久化失败、明确溢出分类及验证失效。

三个长任务场景实际压缩后恢复并继续修改文件：普通继续修改、用户修改未跟踪文件、切换更小模型窗口。比较脚本从 P2 冻结 source.zip 加载旧代码，在临时工作区比较信息保留。示例中 P2 丢失旧用户约束，P3 保留约束并满足配置预算；这是脚本化机制比较，不是模型能力或真实成本测量。

真实只读后端 Smoke、Docker 环境状态独立记录，未运行不能算通过。

## 边界

- 第一版采用确定性事实摘要，没有模型生成的语义摘要，也没有声称能无损保存所有旧输出。摘要不够时可以通过已有工具重新读取文件或有限 Artifact；原始消息仍可恢复查阅。
- UTF-8 字节估算更保守，可能比 tokenizer 更早压缩或停止；无精确 tokenizer、没有内置实时模型容量查询。估算和配置检查不等于后端容量已验证。
- 原始日志和投影目前完整加载到内存；追加写入不代表实现了无限历史或流式索引。Session 采用 POSIX 锁，沿用项目 macOS/Linux 支持范围。
- 工作区树指纹不是文件系统快照或外部服务状态；验证记录不能证明任意 Shell/外部系统的正确性，P2 的未知操作仍需人工核对。
- index.json 的提交失败会停止当前操作，日志中已同步的事实不丢失；重载以日志重建。迁移备份含私有会话信息，与 JSONL 一样保持 0600 权限，不进入公开验证证据。
