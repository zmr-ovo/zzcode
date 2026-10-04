# P0 实施结果：基线与迁移护栏

> 日期：2026-10-04（Asia/Shanghai）  
> 状态：**P0 本地基线和护栏完成；真实模型/Docker Smoke 未执行。**  
> HEAD：`d3db1e0329da9f408d8f1f8d171aeb5387ee74e2`  
> 正式冻结目录：`artifacts/p0-baseline/verified/`  
> 提交状态：P0 冻结时保存到工作区；后续提交状态以 Git 历史为准。

## 1. 验收结果

| 检查 | 结果 | 保存证据 |
|---|---|---|
| 产品测试 | **113 passed**，6 条已有 deprecation warnings | `product-tests.txt/xml` |
| 非 Docker/真实模型 Harness | **115 passed，6 deselected** | `harness-tests.txt/xml` |
| Legacy Golden | **7/7** | `golden.json`、`golden-runs/` |
| 原 12 条机制回归 | 两轮均 **12/12** | `regression.json`、`regression-repeat.json` |
| 两轮语义一致性 | 一致 | `manifest.json` |
| pytest 脚本入口 | 收集成功 | `pytest-entry.txt` |
| 隔离模式导入安装包 | 成功 | `installed-package.txt` |
| 安装后的 CLI | `zzcode --help` 成功 | `cli-entry.txt` |
| 新增/修改辅助脚本 lint | 通过 | `lint.txt` |
| 两条真实 Repo Smoke 预检 | 数据锁、base commit、任务选择通过 | `repo-smoke-preflight.txt` |
| Smoke 非法/重复 ID 拒绝 | 通过 | `smoke-selection-guards.json` |
| Docker 和真实模型推理 | **未运行**：Docker daemon 不可用 | `manifest.json.real_repo_smoke` |

这些机制回归使用 FakeModelClient，不是模型编码能力成绩。两个真实任务只作为后续迁移 Smoke，未产生 resolved、真实 Token、成本或 Provider latency 基线。

## 2. 修复与新增内容

### 原有失败

1. 欢迎界面实现已采用 `zzcode / local coding agent / Start your creation!`，旧测试仍要求已移除的图案。将该断言更新为当前欢迎语，保留长路径裁剪、等宽边框及名称断言，没有改变界面实现。
2. 补齐测试要求的 `docs/review-pack/README.md` 和 `docs/architecture/agent-harness-v1-overview.md`。文档基于实际源码说明责任边界及限制，不把目标 v2 功能写成已有能力。

### 测试与开发环境

- pytest 配置加入 `pythonpath = ["."]`，明确从当前源码树测试。
- 将 `uv.lock` 纳入可保存的版本文件，锁定本次开发依赖。
- 旧 `.venv` 的 `.pth` 文件带有 macOS `hidden` 标志，Python 3.13 日志显示会跳过它们；重新安装及清除标志未能持续解决旧环境导入问题。
- 没有删除旧环境。创建 `/private/tmp/zzcode-p0-venv`，按锁文件安装，确认 editable 包路径指向当前 zzcode；从其他目录和 `python -I` 均可导入，CLI 可运行。
- 正式完整验证以该干净环境为准。根目录下先前的冻结结果保留为初次证据，不能替代 `verified/` 的环境与入口检查。

环境版本：Python **3.13.13**、pytest **9.0.3**、python-dotenv **1.2.2**、ruff **0.15.11**、uv **0.11.7**。生产项目仍声明 Python 3.10+；P0 没有验证所有受支持 Python 版本。

### Golden 与迁移配置

新增 `tests/fixtures/legacy_golden.json`、`tests/test_p0_golden.py` 和 `scripts/p0_golden.py`，覆盖：

1. JSON 工具调用后 Final。
2. XML Patch 后 Final。
3. 非法 JSON 后恢复。
4. 审批拒绝后文件不变。
5. 工具预算耗尽后的现有 final-only turn。
6. final-only turn 提出额外工具时禁止执行并停止。
7. 空响应耗尽重试预算。

比较答案、停止原因、attempts、tool_steps、工具序列、文件结果和工具输出关键证据。7 条 Golden 已纳入产品测试，因此不能把 113 项产品测试与 7 条 Golden 相加成独立测试数。

`evaluation/configs/migration-profiles.json` 仅提供已实现的 `legacy`，保持四个现有特性默认开启。其他名称仅预留，验收工具对未实现配置明确拒绝；没有启用不存在的 v2 Runtime，也没有改变现有 ablation feature flags 接口。

## 3. 回归冻结

两轮从独立 fixture 副本运行，不复用 Session/Memory。比较 task ID、通过状态、stop reason、tool steps、attempts 和最终文件 digest；不比较随机 Run ID、时间戳和路径。

| Task | Tool Steps | Attempts |
|---|---:|---:|
| readme_intro_locked | 1 | 2 |
| readme_schema_note | 1 | 2 |
| sample_beta_locked | 1 | 2 |
| sample_gamma_locked | 1 | 2 |
| invalid_patch_recovery | 2 | 3 |
| path_escape_recovery | 2 | 3 |
| repeated_read_recovery | 4 | 5 |
| context_reduction_checkpoint | 0 | 1 |
| freshness_reanchor_resume | 0 | 1 |
| workspace_mismatch_resume | 0 | 1 |
| durable_promotion_accept | 0 | 1 |
| durable_promotion_reject | 0 | 1 |

总计 14 Tool Steps、24 Attempts。当前 Tool Steps 包含已解析但被拒绝的提案，不能与未来的 executed_tool_calls 直接比较。两轮总耗时约 2.46 秒和 2.78 秒，仅为本地机制运行时间。

原 Run 的 `trace.jsonl`、`task_state.json`、`report.json` 已复制到 `regression-runs/<task-id>/`，Golden 保存到 `golden-runs/<case-id>/`。FakeModel 未提供实际 usage，Token/成本使用 `null`，没有填 0。

## 4. 真实 Repo Smoke 与数据划分

固定配置：`evaluation/configs/p0-smoke.json`。

- `ZZCODE-BUG-001`：OpenAI-compatible reasoning-only 响应诊断。
- `ZZCODE-BUG-002`：`.env` 工作区与工具隔离。
- 两项均属于 `dev`，base commit 为 `b0f9c84b9fcec9901684c70d6c29edad5357761b`。
- `dev` 的完整数据锁 digest 为 `sha256:5ae6985d5e64f56c9275b27796887ca5a4862878cde85059dff0ed6ef76d1ff5`。
- 003/004 保持开发诊断用途，005–008 的 `test` split 保留为固定候选后的验证集。
- `run_internal_eval.py` 增加重复 `--instance-id` 选择；先核对完整来源 split 的 lock，再构造子集。真实 Run Manifest 保存子集自身 digest 和 task count，不能冒充完整 dev 成绩。

固定模型配置为 OpenAI-compatible / `gpt-5.4`，temperature 0、30 steps、8192 output tokens、Gold 验证 3 次、Docker image `zzcode-eval-py313:phase4`。真实执行前需要 Provider 凭据、对应 Endpoint 和可用镜像；实际镜像 digest 与 Provider 身份由真实运行记录核对。当前 image digest、推理结果均未知。

预检不调用真实模型、不读取 Gold 内容给 Agent，不运行 Docker。尚未生成真实模型 Baseline，后续原生工具纵向切片或 Executor 切换前需要补跑。

## 5. 重现入口

在项目根目录创建隔离环境，不依赖原 `.venv` 的文件标志：

```bash
UV_PROJECT_ENVIRONMENT=/private/tmp/zzcode-p0-venv UV_CACHE_DIR=/private/tmp/zzcode-p0-uv-cache uv sync --locked --python 3.13.13
```

生成新基线（输出目录必须新建或为空，避免覆盖历史证据）：

```bash
UV_PROJECT_ENVIRONMENT=/private/tmp/zzcode-p0-venv UV_CACHE_DIR=/private/tmp/zzcode-p0-uv-cache uv run --locked python scripts/freeze_p0_baseline.py --output artifacts/p0-baseline/<new-run>
```

只做 Smoke 预检：

```bash
UV_PROJECT_ENVIRONMENT=/private/tmp/zzcode-p0-venv UV_CACHE_DIR=/private/tmp/zzcode-p0-uv-cache uv run --locked python scripts/run_p0_smoke.py --dry-run
```

Docker、镜像和凭据就绪后，去掉 `--dry-run` 运行真实 Null → Gold ×3 → Agent → Grader。默认只选上述两条开发任务；输出保存到 `evaluation/runs/`，工作区保存到 `evaluation/workspaces/`。

临时目录可能被系统清理；可用上述锁定命令重新创建。P0 源码快照保留于 `verified/source.zip`，但真实 Repo 任务仍需要具有指定 base commit 的 Git 历史及独立私有评分包；源码 ZIP 不包含私有评分数据或凭据。

## 6. 保存与回滚边界

`manifest.json` 保存 HEAD、工作区状态、Python/依赖、源文件哈希、其他未跟踪用户文件哈希、命令、结果和 Artifact 哈希。`source.zip` 保存公开源码及此次新增的护栏文件，`uv.lock` 固定依赖，`changes.patch` 保存原跟踪文件的修改。

既有用户未跟踪文件保持原位，不复制其正文。没有提交、清理、重置用户工作区，没有安装 Docker，没有改变 Agent 默认行为。结果报告是冻结完成后新增的说明文档，不属于冻结时执行的源码快照。

旧路径目前就是唯一已实现的 Runtime。后续候选从独立工作区运行；回滚切换到 `legacy` 并使用兼容 Session，不能把未来 v2 状态强行当作 v1 读取。

## 7. 下一阶段准入

可以开始 P1 的 Message 类型和 Legacy Adapter。进入真实 Provider 纵向切片前，补跑两条 Smoke 并保存真实模型 Baseline。保留已确认的 final-only 行为；Completion Gate/Verification Profile 仍属待新增能力。
