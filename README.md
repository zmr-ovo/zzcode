# zzcode

轻量级本地 Coding Agent，支持仓库读写、Shell 执行、上下文压缩、会话恢复和真实仓库任务评测。

## 快速开始

Python 3.10+，使用 uv 管理依赖。通过 `.env` 或环境变量配置模型、API 地址与密钥；支持 OpenAI-compatible、Anthropic-compatible 和 Ollama 原生工具调用。

```bash
uv sync
uv run zzcode --cwd /path/to/repo

# 单次代码任务，验证通过后才标记完成
uv run zzcode --cwd /path/to/repo --task-type code_change \
  --verify-command "python -m pytest -q" "修复测试失败"

# 恢复最近会话
uv run zzcode --cwd /path/to/repo --resume latest
```

## 核心功能

- **原生 Tool Calling**：文件读取、搜索、修改与 Shell 工具统一经过 Gateway。
- **混合上下文压缩**：旧工具结果替换为引用，必要时调用模型总结；原始历史保留。
- **执行与恢复**：审批、操作账本、原子文件写入、Session 与 Checkpoint。
- **验证与追踪**：代码任务执行配置的验证命令，支持结构化事件和运行报告。
- **Local / Docker**：默认本机执行；Docker 使用禁网、资源受限的工作区副本。

## 项目结构

```text
zzcode/
├── zzcode/                         # 产品实现
│   ├── agent/                      # 运行循环、事件、任务状态与完成策略
│   ├── context/                    # 工作区上下文、预算、压缩与记忆
│   ├── core/                       # 消息、工具协议类型与安全定义
│   ├── providers/                  # OpenAI、Anthropic、Ollama 协议适配
│   ├── execution/                  # 工具、审批、操作账本与 Local/Docker 执行器
│   ├── storage/                    # Session、Checkpoint 与运行工件存储
│   ├── evaluation/                 # SWE-bench 式真实任务评测框架
│   │   ├── inference/              # Agent 推理与 Patch 收集
│   │   ├── execution/              # 独立工作区、测试执行与 Docker 管理
│   │   ├── grading/                # Patch 安全检查与 F2P/P2P 评分
│   │   └── reporting/              # 评测结果存储与报告生成
│   └── cli.py                      # 产品命令行入口
├── tests/                          # 自动化测试
│   ├── agent/                      # 循环、CLI、事件与完成判断
│   ├── context/                    # 预算、压缩与记忆
│   ├── execution/                  # 工具安全、文件操作与执行器
│   ├── storage/                    # 会话存储与 Checkpoint 恢复
│   ├── providers/                  # 原生协议与固定 Golden 用例
│   ├── evaluation/                 # 评测框架的单元、集成、隔离及评分测试
│   └── fixtures/                   # 测试数据与协议录制
├── evaluation/                     # 评测资产
│   ├── configs/                    # Agent 与完整评测配置示例
│   ├── datasets/                   # 公开题面、任务划分与数据锁
│   └── environments/               # Dockerfile 与评分依赖
├── scripts/                        # 两个开发入口
│   ├── evaluation/run_internal_eval.py  # 完整真实仓库评测
│   └── testing/run_native_smoke.py       # 真实模型工具调用检查
├── README.md
├── agent.md                        # 代码风格偏好
├── pyproject.toml                  # 依赖、打包与测试配置
└── uv.lock                         # 依赖锁定
```

本地输出不提交：`.zzcode/` 保存会话、账本与工具输出；`evaluation/runs/` 保存评测结果，`workspaces/` 保存工作副本。`evaluation/private/` 是单独提供的隐藏测试与 Gold Patch，不是生成物。

## 开发与评测

```bash
uv run pytest tests -m "not docker and not real_model"
uv run ruff check zzcode scripts tests

# 需要运行中的 Docker 与评分镜像
RUN_DOCKER_TESTS=1 uv run pytest tests -m docker
```

真实评测：运行 Agent、收集 Patch，在独立环境中执行 F2P/P2P 测试并评分。产品测试通过与真实解题率分别统计。

- [评测使用指南](evaluation/README.md)
- [配置说明](evaluation/configs/README.md)
- [Docker 评分环境](evaluation/environments/zzcode-py313/README.md)
