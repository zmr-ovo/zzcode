# Shell 环境白名单可能重新放行密钥

`ZZCode.shell_env()` 根据 allowlist 构造子进程环境，但如果调用者误把密钥变量加入 allowlist，密钥会被传给 Agent 执行的 shell 命令。密钥识别规则已经存在，shell 边界也应强制使用它。

请修改实现：

- 即使变量出现在 shell allowlist 中，只要被识别为敏感环境变量，也不能传入子进程。
- 自定义 `secret_env_names` 同样必须生效。
- PATH、LANG 等普通允许变量保持原行为。
- `PWD` 必须继续指向仓库根目录。
- 不改变 trace/report 的现有脱敏行为。
