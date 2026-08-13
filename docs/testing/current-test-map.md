# zzcode 当前测试地图（Phase 0）

> 基线 commit：`446ba3273c6513d5a1589daf81dea86ad851ebd0`  
> 采集时间：`2026-08-12T14:30:39.593978+08:00`  
> 范围：当前 pytest 的 105 个测试；本文件只做盘点和迁移设计，不改变测试或产品行为。

## 1. 基线结论

- 总计：105；通过：103；失败：2；跳过：0；耗时：34.251 秒。
- 当前目录只有一个扁平的 `tests/`，其中 `tests/test_zzcode.py` 单文件包含 60 项，混合了 unit、integration、security、contract 等职责。
- `FakeModelClient` 同时承担确定性测试桩和旧 benchmark 的“预写答案”。迁移时两者必须分开：普通 unit/integration 可使用不包含任务答案的 `DeterministicModelStub`；正式 Repo Task 评测不得使用 scripted/FakeModel 输出。
- 机器可读的逐项结果、耗时和失败信息见 `artifacts/test-baseline.json`。

> Phase 6.5 更新：旧欢迎页断言已按当前 CLI 合同更新；引用未入库 reviewer skeleton 的测试已迁为 `evaluation/tests/` 下的现行 Evaluation 文档检查。当前 `tests/` 为 Agent 公开 verify 范围，已达到 116/116 通过。Phase 0 数据仍保留为历史基线。

### 已知失败

| 测试 | 当前现象 | Phase 0 处理 |
|---|---|---|
| `tests/test_zzcode.py::test_welcome_screen_keeps_box_shape_for_long_paths` | 断言仍要求旧欢迎界面的 `(  o o  )` 行，当前输出不再包含 | 记录，不在评测重构中擅自改 UI 或断言 |
| `tests/test_zzcode.py::test_reviewer_skeleton_docs_exist` | 缺少 `docs/review-pack/README.md` 等 reviewer 文档 | 记录，不用无关补文件掩盖基线失败 |

## 2. 文件级盘点

| 当前文件 | 数量 | 主要职责 | 主要依赖/测试替身 | 主要问题 |
|---|---:|---|---|---|
| `tests/test_context_manager.py` | 7 | ContextManager 上下文组装与裁剪 | FakeModelClient、WorkspaceContext、SessionStore、临时目录 | 主体可保留，调整目录 |
| `tests/test_evaluator.py` | 8 | 旧 fixed benchmark loader/runner/summary | 旧 benchmark JSON、fixture copy、FakeModelClient、临时目录 | 绑定旧 schema 与 scripted benchmark |
| `tests/test_memory.py` | 5 | LayeredMemory 数据结构与检索 | 临时目录、文件系统 | 主体可保留，调整目录 |
| `tests/test_metrics.py` | 4 | 旧 benchmark 消融与报告 | 旧 evaluator、benchmark fixtures、临时目录 | 慢且依赖旧 evaluator，不应留在快速门禁 |
| `tests/test_run_store.py` | 4 | 运行状态、trace 和 report 持久化 | 临时目录、JSONL/JSON 文件 | 主体可保留，调整目录 |
| `tests/test_safety_invariants.py` | 11 | 路径、secret、shell、delegation 安全不变量 | FakeModelClient、mock、临时目录、进程环境 | 安全与普通集成测试边界不清 |
| `tests/test_task_state.py` | 6 | TaskState 状态机与快照 | 纯内存对象 | 主体可保留，调整目录 |
| `tests/test_zzcode.py` | 60 | Agent、CLI、provider、恢复、记忆和工具的混合集成测试 | FakeModelClient、mock HTTP、subprocess、临时目录、环境变量 | 职责过多，必须拆分 |

## 3. 逐项测试迁移表

“验证内容”描述该测试真正锁定的合同；“建议层级”决定未来默认门禁、执行环境和失败含义。迁移动作不是立即执行指令，而是 Phase 1–7 的工作清单。

### `tests/test_context_manager.py`（7 项）

| 当前测试 | 状态 | 验证内容 | 建议层级 | 建议新位置 | 迁移动作 |
|---|---|---|---|---|---|
| `test_context_manager_assembles_sections_in_expected_order` | PASS | 上下文组装、预算裁剪与记忆选择：context manager assembles sections in expected order | `unit` | `tests/unit/test_context_manager.py` | 迁移；FakeModelClient 改为 DeterministicModelStub |
| `test_context_manager_reduces_relevant_memory_before_history_and_preserves_newer_context` | PASS | 上下文组装、预算裁剪与记忆选择：context manager reduces relevant memory before history and preserves newer context | `unit` | `tests/unit/test_context_manager.py` | 迁移；FakeModelClient 改为 DeterministicModelStub |
| `test_context_manager_renders_top_three_episodic_notes_per_note_under_budget` | PASS | 上下文组装、预算裁剪与记忆选择：context manager renders top three episodic notes per note under budget | `unit` | `tests/unit/test_context_manager.py` | 迁移；FakeModelClient 改为 DeterministicModelStub |
| `test_context_manager_preserves_current_request_when_over_budget` | PASS | 上下文组装、预算裁剪与记忆选择：context manager preserves current request when over budget | `unit` | `tests/unit/test_context_manager.py` | 迁移；FakeModelClient 改为 DeterministicModelStub |
| `test_context_manager_collapses_older_duplicate_reads_into_one_summary_line` | PASS | 上下文组装、预算裁剪与记忆选择：context manager collapses older duplicate reads into one summary line | `unit` | `tests/unit/test_context_manager.py` | 迁移；FakeModelClient 改为 DeterministicModelStub |
| `test_context_manager_summarizes_older_tool_output_into_one_line` | PASS | 上下文组装、预算裁剪与记忆选择：context manager summarizes older tool output into one line | `unit` | `tests/unit/test_context_manager.py` | 迁移；FakeModelClient 改为 DeterministicModelStub |
| `test_context_manager_relevant_memory_can_mix_durable_notes` | PASS | 上下文组装、预算裁剪与记忆选择：context manager relevant memory can mix durable notes | `unit` | `tests/unit/test_context_manager.py` | 迁移；FakeModelClient 改为 DeterministicModelStub |

### `tests/test_evaluator.py`（8 项）

| 当前测试 | 状态 | 验证内容 | 建议层级 | 建议新位置 | 迁移动作 |
|---|---|---|---|---|---|
| `test_load_benchmark_validates_fixed_schema` | PASS | 新数据集 schema 与 loader：load benchmark validates fixed schema | `eval-unit` | `evaluation/tests/unit/test_dataset_loader.py` | 按 Repo Task manifest 重写，旧 schema 作为迁移输入 |
| `test_load_benchmark_rejects_missing_required_task_fields` | PASS | 新数据集 schema 与 loader：load benchmark rejects missing required task fields | `eval-unit` | `evaluation/tests/unit/test_dataset_loader.py` | 按 Repo Task manifest 重写，旧 schema 作为迁移输入 |
| `test_run_fixed_benchmark_uses_fresh_fixture_copy_and_fresh_run_directory` | PASS | 旧 fixed benchmark 执行与产物：run fixed benchmark uses fresh fixture copy and fresh run directory | `legacy-eval-integration` | `evaluation/tests/integration/test_legacy_migration.py` | 暂保留为 legacy regression；新执行链覆盖后删除 |
| `test_run_fixed_benchmark_reports_metadata_and_success_definition` | PASS | 旧 fixed benchmark 执行与产物：run fixed benchmark reports metadata and success definition | `legacy-eval-integration` | `evaluation/tests/integration/test_legacy_migration.py` | 暂保留为 legacy regression；新执行链覆盖后删除 |
| `test_run_fixed_benchmark_covers_recovery_and_durable_contract_rows` | PASS | 旧 fixed benchmark 执行与产物：run fixed benchmark covers recovery and durable contract rows | `legacy-eval-integration` | `evaluation/tests/integration/test_legacy_migration.py` | 暂保留为 legacy regression；新执行链覆盖后删除 |
| `test_run_harness_regression_v2_writes_named_artifact` | PASS | 旧 fixed benchmark 执行与产物：run harness regression v2 writes named artifact | `legacy-eval-integration` | `evaluation/tests/integration/test_legacy_migration.py` | 暂保留为 legacy regression；新执行链覆盖后删除 |
| `test_run_task_anchors_paths_to_fixture_copy_even_inside_repo_workspace` | PASS | 任务工作区路径锚定：run task anchors paths to fixture copy even inside repo workspace | `eval-security` | `evaluation/tests/security/test_workspace_isolation.py` | 改写为 WorkspaceManager/Sandbox 合同测试 |
| `test_summarize_rows_counts_failure_categories` | PASS | 聚合统计与失败分类：summarize rows counts failure categories | `eval-unit` | `evaluation/tests/unit/test_aggregate.py` | 改用 TaskResult/RunSummary 新结果模型 |

### `tests/test_memory.py`（5 项）

| 当前测试 | 状态 | 验证内容 | 建议层级 | 建议新位置 | 迁移动作 |
|---|---|---|---|---|---|
| `test_working_memory_tracks_summary_and_recent_files` | PASS | 分层记忆存取与新鲜度：working memory tracks summary and recent files | `unit` | `tests/unit/test_memory.py` | 直接迁移并保持现有行为断言 |
| `test_episodic_notes_append_and_retrieve_deterministically` | PASS | 分层记忆存取与新鲜度：episodic notes append and retrieve deterministically | `unit` | `tests/unit/test_memory.py` | 直接迁移并保持现有行为断言 |
| `test_file_summaries_use_canonical_paths_and_freshness` | PASS | 分层记忆存取与新鲜度：file summaries use canonical paths and freshness | `unit` | `tests/unit/test_memory.py` | 直接迁移并保持现有行为断言 |
| `test_process_notes_keep_kind_and_latest_duplicate_wins` | PASS | 分层记忆存取与新鲜度：process notes keep kind and latest duplicate wins | `unit` | `tests/unit/test_memory.py` | 直接迁移并保持现有行为断言 |
| `test_durable_memory_index_and_topic_notes_are_loaded_and_retrieved` | PASS | 分层记忆存取与新鲜度：durable memory index and topic notes are loaded and retrieved | `unit` | `tests/unit/test_memory.py` | 直接迁移并保持现有行为断言 |

### `tests/test_metrics.py`（4 项）

| 当前测试 | 状态 | 验证内容 | 建议层级 | 建议新位置 | 迁移动作 |
|---|---|---|---|---|---|
| `test_run_context_ablation_v2_writes_expected_artifact` | PASS | 旧评测消融诊断：run context ablation v2 writes expected artifact | `diagnostic` | `evaluation/tests/diagnostics/test_ablations.py` | 移出默认门禁；统一数据模型可用后迁移 |
| `test_run_memory_ablation_v2_writes_expected_artifact` | PASS | 旧评测消融诊断：run memory ablation v2 writes expected artifact | `diagnostic` | `evaluation/tests/diagnostics/test_ablations.py` | 移出默认门禁；统一数据模型可用后迁移 |
| `test_run_recovery_ablation_v2_writes_expected_artifact` | PASS | 旧评测消融诊断：run recovery ablation v2 writes expected artifact | `diagnostic` | `evaluation/tests/diagnostics/test_ablations.py` | 移出默认门禁；统一数据模型可用后迁移 |
| `test_write_benchmark_core_report_marks_resume_safe_metrics` | PASS | 评测报告与 resume-safe 指标：write benchmark core report marks resume safe metrics | `eval-reporting` | `evaluation/tests/unit/test_reporting.py` | 改为读取统一 RunSummary；旧报告保留作对照 |

### `tests/test_run_store.py`（4 项）

| 当前测试 | 状态 | 验证内容 | 建议层级 | 建议新位置 | 迁移动作 |
|---|---|---|---|---|---|
| `test_run_store_creates_run_directory_and_state_file` | PASS | 运行产物与状态持久化：run store creates run directory and state file | `unit` | `tests/unit/test_run_store.py` | 直接迁移并保持现有行为断言 |
| `test_run_store_appends_trace_jsonl` | PASS | 运行产物与状态持久化：run store appends trace jsonl | `unit` | `tests/unit/test_run_store.py` | 直接迁移并保持现有行为断言 |
| `test_run_store_writes_report_json` | PASS | 运行产物与状态持久化：run store writes report json | `unit` | `tests/unit/test_run_store.py` | 直接迁移并保持现有行为断言 |
| `test_run_store_tolerates_missing_final_report` | PASS | 运行产物与状态持久化：run store tolerates missing final report | `unit` | `tests/unit/test_run_store.py` | 直接迁移并保持现有行为断言 |

### `tests/test_safety_invariants.py`（11 项）

| 当前测试 | 状态 | 验证内容 | 建议层级 | 建议新位置 | 迁移动作 |
|---|---|---|---|---|---|
| `test_workspace_escape_is_rejected` | PASS | 工作区边界与路径穿越防护：workspace escape is rejected | `security` | `tests/security/test_path_boundaries.py` | 直接迁移并保持现有行为断言 |
| `test_symlink_path_traversal_is_rejected` | PASS | 工作区边界与路径穿越防护：symlink path traversal is rejected | `security` | `tests/security/test_path_boundaries.py` | 直接迁移并保持现有行为断言 |
| `test_risky_tool_deny_behavior` | PASS | 绑定工具的模块边界：risky tool deny behavior | `integration` | `tests/integration/test_tool_protocol.py` | 直接迁移并保持现有行为断言 |
| `test_cli_build_agent_wires_secret_env_names_from_parser` | PASS | secret 隔离、脱敏与 shell 环境：cli build agent wires secret env names from parser | `security` | `tests/security/test_secrets_and_shell.py` | 直接迁移并保持现有行为断言 |
| `test_cli_build_agent_uses_default_configured_secret_names` | PASS | secret 隔离、脱敏与 shell 环境：cli build agent uses default configured secret names | `security` | `tests/security/test_secrets_and_shell.py` | 直接迁移并保持现有行为断言 |
| `test_cli_build_agent_reads_secret_names_from_environment_config` | PASS | secret 隔离、脱敏与 shell 环境：cli build agent reads secret names from environment config | `security` | `tests/security/test_secrets_and_shell.py` | 直接迁移并保持现有行为断言 |
| `test_run_shell_uses_allowlisted_environment_only` | PASS | secret 隔离、脱敏与 shell 环境：run shell uses allowlisted environment only | `security` | `tests/security/test_secrets_and_shell.py` | 直接迁移并保持现有行为断言 |
| `test_bound_tool_methods_delegate_into_tools_module` | PASS | 子 Agent 深度与只读权限：bound tool methods delegate into tools module | `security` | `tests/security/test_delegation.py` | 迁移；FakeModelClient 改为 DeterministicModelStub |
| `test_delegate_depth_limit_is_enforced` | PASS | 子 Agent 深度与只读权限：delegate depth limit is enforced | `security` | `tests/security/test_delegation.py` | 迁移；FakeModelClient 改为 DeterministicModelStub |
| `test_delegate_child_is_read_only` | PASS | 子 Agent 深度与只读权限：delegate child is read only | `security` | `tests/security/test_delegation.py` | 迁移；FakeModelClient 改为 DeterministicModelStub |
| `test_configured_secret_env_names_are_redacted_in_trace_and_report` | PASS | secret 隔离、脱敏与 shell 环境：configured secret env names are redacted in trace and report | `security` | `tests/security/test_secrets_and_shell.py` | 直接迁移并保持现有行为断言 |

### `tests/test_task_state.py`（6 项）

| 当前测试 | 状态 | 验证内容 | 建议层级 | 建议新位置 | 迁移动作 |
|---|---|---|---|---|---|
| `test_task_state_starts_running_with_empty_progress` | PASS | 任务状态机、停止原因与快照：task state starts running with empty progress | `unit` | `tests/unit/test_task_state.py` | 直接迁移并保持现有行为断言 |
| `test_task_state_records_success_and_final_answer` | PASS | 任务状态机、停止原因与快照：task state records success and final answer | `unit` | `tests/unit/test_task_state.py` | 直接迁移并保持现有行为断言 |
| `test_task_state_records_step_limit_stop_reason` | PASS | 任务状态机、停止原因与快照：task state records step limit stop reason | `unit` | `tests/unit/test_task_state.py` | 直接迁移并保持现有行为断言 |
| `test_task_state_records_retry_limit_stop_reason` | PASS | 任务状态机、停止原因与快照：task state records retry limit stop reason | `unit` | `tests/unit/test_task_state.py` | 直接迁移并保持现有行为断言 |
| `test_task_state_snapshot_keeps_final_answer` | PASS | 任务状态机、停止原因与快照：task state snapshot keeps final answer | `unit` | `tests/unit/test_task_state.py` | 直接迁移并保持现有行为断言 |
| `test_task_state_snapshot_keeps_checkpoint_reference_without_body` | PASS | 任务状态机、停止原因与快照：task state snapshot keeps checkpoint reference without body | `unit` | `tests/unit/test_task_state.py` | 直接迁移并保持现有行为断言 |

### `tests/test_zzcode.py`（60 项）

| 当前测试 | 状态 | 验证内容 | 建议层级 | 建议新位置 | 迁移动作 |
|---|---|---|---|---|---|
| `test_agent_runs_tool_then_final` | PASS | Agent 主循环、重试与最终输出：agent runs tool then final | `integration` | `tests/integration/test_agent_loop.py` | 迁移；FakeModelClient 改为 DeterministicModelStub |
| `test_agent_updates_task_summary_on_each_request` | PASS | Agent 与分层记忆的集成行为：agent updates task summary on each request | `integration` | `tests/integration/test_memory_integration.py` | 迁移；FakeModelClient 改为 DeterministicModelStub |
| `test_agent_only_stores_reusable_epistemic_notes` | PASS | Agent 与分层记忆的集成行为：agent only stores reusable epistemic notes | `integration` | `tests/integration/test_memory_integration.py` | 迁移；FakeModelClient 改为 DeterministicModelStub |
| `test_file_summary_cache_is_invalidated_on_out_of_band_edit_and_path_spelling` | PASS | Agent 与分层记忆的集成行为：file summary cache is invalidated on out of band edit and path spelling | `integration` | `tests/integration/test_memory_integration.py` | 迁移；FakeModelClient 改为 DeterministicModelStub |
| `test_agent_retries_after_empty_model_output` | PASS | Agent 主循环、重试与最终输出：agent retries after empty model output | `integration` | `tests/integration/test_agent_loop.py` | 迁移；FakeModelClient 改为 DeterministicModelStub |
| `test_agent_retries_after_malformed_tool_payload` | PASS | Agent 主循环、重试与最终输出：agent retries after malformed tool payload | `integration` | `tests/integration/test_agent_loop.py` | 迁移；FakeModelClient 改为 DeterministicModelStub |
| `test_agent_accepts_xml_write_file_tool` | PASS | Agent 主循环、重试与最终输出：agent accepts xml write file tool | `integration` | `tests/integration/test_agent_loop.py` | 迁移；FakeModelClient 改为 DeterministicModelStub |
| `test_retries_do_not_consume_the_whole_budget` | PASS | Agent 主循环、重试与最终输出：retries do not consume the whole budget | `integration` | `tests/integration/test_agent_loop.py` | 迁移；FakeModelClient 改为 DeterministicModelStub |
| `test_agent_gets_finalization_turn_after_tool_budget_is_exhausted` | PASS | Agent 主循环、重试与最终输出：agent gets finalization turn after tool budget is exhausted | `integration` | `tests/integration/test_agent_loop.py` | 迁移；FakeModelClient 改为 DeterministicModelStub |
| `test_agent_saves_and_resumes_session` | PASS | checkpoint、恢复与运行身份一致性：agent saves and resumes session | `integration` | `tests/integration/test_resume_and_checkpoint.py` | 迁移；FakeModelClient 改为 DeterministicModelStub |
| `test_delegate_uses_child_agent` | PASS | 工具协议、恢复与安全边界：delegate uses child agent | `integration` | `tests/integration/test_tool_protocol.py` | 迁移；FakeModelClient 改为 DeterministicModelStub |
| `test_patch_file_replaces_exact_match` | PASS | 工具协议、恢复与安全边界：patch file replaces exact match | `integration` | `tests/integration/test_tool_protocol.py` | 迁移；FakeModelClient 改为 DeterministicModelStub |
| `test_invalid_risky_tool_does_not_prompt_for_approval` | PASS | 工具协议、恢复与安全边界：invalid risky tool does not prompt for approval | `security` | `tests/security/test_tool_boundaries.py` | 迁移；FakeModelClient 改为 DeterministicModelStub |
| `test_list_files_hides_internal_agent_state` | PASS | 工具协议、恢复与安全边界：list files hides internal agent state | `security` | `tests/security/test_tool_boundaries.py` | 迁移；FakeModelClient 改为 DeterministicModelStub |
| `test_repeated_identical_tool_call_is_rejected` | PASS | 工具协议、恢复与安全边界：repeated identical tool call is rejected | `integration` | `tests/integration/test_tool_protocol.py` | 迁移；FakeModelClient 改为 DeterministicModelStub |
| `test_welcome_screen_keeps_box_shape_for_long_paths` | FAIL | CLI/包公开接口合同：welcome screen keeps box shape for long paths | `contract` | `tests/contract/test_package_surface.py` | 先确认产品合同与断言谁应更新；在决议前保持为已知失败 |
| `test_ollama_client_posts_expected_payload` | PASS | 模型 provider 请求与响应解析：ollama client posts expected payload | `unit/integration` | `tests/integration/test_provider_clients.py` | 保留 HTTP mock，按 provider 拆分文件 |
| `test_openai_compatible_client_posts_expected_responses_payload` | PASS | 模型 provider 请求与响应解析：openai compatible client posts expected responses payload | `unit/integration` | `tests/integration/test_provider_clients.py` | 保留 HTTP mock，按 provider 拆分文件 |
| `test_openai_compatible_client_reports_reasoning_only_response` | PASS | 模型 provider 请求与响应解析：openai compatible client reports reasoning only response | `unit/integration` | `tests/integration/test_provider_clients.py` | 保留 HTTP mock，按 provider 拆分文件 |
| `test_openai_compatible_client_sends_prompt_cache_fields_and_records_usage` | PASS | 模型 provider 请求与响应解析：openai compatible client sends prompt cache fields and records usage | `unit/integration` | `tests/integration/test_provider_clients.py` | 保留 HTTP mock，按 provider 拆分文件 |
| `test_openai_compatible_client_extracts_text_from_event_stream` | PASS | 模型 provider 请求与响应解析：openai compatible client extracts text from event stream | `unit/integration` | `tests/integration/test_provider_clients.py` | 保留 HTTP mock，按 provider 拆分文件 |
| `test_openai_compatible_client_extracts_text_from_event_stream_deltas` | PASS | 模型 provider 请求与响应解析：openai compatible client extracts text from event stream deltas | `unit/integration` | `tests/integration/test_provider_clients.py` | 保留 HTTP mock，按 provider 拆分文件 |
| `test_anthropic_compatible_client_posts_expected_messages_payload` | PASS | 模型 provider 请求与响应解析：anthropic compatible client posts expected messages payload | `unit/integration` | `tests/integration/test_provider_clients.py` | 保留 HTTP mock，按 provider 拆分文件 |
| `test_anthropic_compatible_client_extracts_first_text_block` | PASS | 模型 provider 请求与响应解析：anthropic compatible client extracts first text block | `unit/integration` | `tests/integration/test_provider_clients.py` | 保留 HTTP mock，按 provider 拆分文件 |
| `test_build_agent_uses_openai_provider_and_model_override` | PASS | CLI 参数、provider 与环境配置：build agent uses openai provider and model override | `integration` | `tests/integration/test_cli_config.py` | 直接迁移并保持现有行为断言 |
| `test_build_agent_loads_global_dotenv_from_another_workspace` | PASS | CLI 参数、provider 与环境配置：build agent loads global dotenv from another workspace | `integration` | `tests/integration/test_cli_config.py` | 直接迁移并保持现有行为断言 |
| `test_shell_env_overrides_global_and_workspace_dotenv` | PASS | CLI 参数、provider 与环境配置：shell env overrides global and workspace dotenv | `integration` | `tests/integration/test_cli_config.py` | 直接迁移并保持现有行为断言 |
| `test_dotenv_is_hidden_and_cannot_be_read_by_agent` | PASS | 配置文件不可被 Agent 读取：dotenv is hidden and cannot be read by agent | `security` | `tests/security/test_secret_files.py` | 直接迁移并保持现有行为断言 |
| `test_build_arg_parser_defaults_provider_to_openai` | PASS | CLI 参数、provider 与环境配置：build arg parser defaults provider to openai | `integration` | `tests/integration/test_cli_config.py` | 直接迁移并保持现有行为断言 |
| `test_build_arg_parser_accepts_anthropic_provider` | PASS | CLI 参数、provider 与环境配置：build arg parser accepts anthropic provider | `integration` | `tests/integration/test_cli_config.py` | 直接迁移并保持现有行为断言 |
| `test_build_agent_uses_anthropic_provider_and_openai_key_fallback` | PASS | CLI 参数、provider 与环境配置：build agent uses anthropic provider and openai key fallback | `integration` | `tests/integration/test_cli_config.py` | 直接迁移并保持现有行为断言 |
| `test_build_agent_uses_anthropic_default_model_when_env_is_missing` | PASS | CLI 参数、provider 与环境配置：build agent uses anthropic default model when env is missing | `integration` | `tests/integration/test_cli_config.py` | 直接迁移并保持现有行为断言 |
| `test_build_agent_uses_openai_provider_by_default` | PASS | CLI 参数、provider 与环境配置：build agent uses openai provider by default | `integration` | `tests/integration/test_cli_config.py` | 直接迁移并保持现有行为断言 |
| `test_successful_run_persists_run_artifacts_and_stop_reason` | PASS | 运行可观测性、trace 与 artifact 合同：successful run persists run artifacts and stop reason | `integration` | `tests/integration/test_run_artifacts.py` | 迁移；统一字段后补 schema 断言 |
| `test_trace_and_report_redact_secret_env_values` | PASS | 运行可观测性、trace 与 artifact 合同：trace and report redact secret env values | `integration` | `tests/integration/test_run_artifacts.py` | 迁移；统一字段后补 schema 断言 |
| `test_prompt_budget_metadata_records_budget_decisions` | PASS | 运行可观测性、trace 与 artifact 合同：prompt budget metadata records budget decisions | `integration` | `tests/integration/test_run_artifacts.py` | 迁移；统一字段后补 schema 断言 |
| `test_prompt_metadata_refreshes_prefix_when_workspace_changes` | PASS | 运行可观测性、trace 与 artifact 合同：prompt metadata refreshes prefix when workspace changes | `integration` | `tests/integration/test_run_artifacts.py` | 迁移；统一字段后补 schema 断言 |
| `test_agent_creates_checkpoint_when_context_reduction_happens_and_artifacts_only_reference_it` | PASS | checkpoint、恢复与运行身份一致性：agent creates checkpoint when context reduction happens and artifacts only reference it | `integration` | `tests/integration/test_resume_and_checkpoint.py` | 迁移；FakeModelClient 改为 DeterministicModelStub |
| `test_resume_prompt_uses_checkpoint_state_not_just_history` | PASS | checkpoint、恢复与运行身份一致性：resume prompt uses checkpoint state not just history | `integration` | `tests/integration/test_resume_and_checkpoint.py` | 迁移；FakeModelClient 改为 DeterministicModelStub |
| `test_resume_invalidates_stale_file_summaries_and_marks_partial_stale` | PASS | checkpoint、恢复与运行身份一致性：resume invalidates stale file summaries and marks partial stale | `integration` | `tests/integration/test_resume_and_checkpoint.py` | 迁移；FakeModelClient 改为 DeterministicModelStub |
| `test_run_shell_nonzero_with_workspace_change_is_recorded_as_partial_success` | PASS | 工具协议、恢复与安全边界：run shell nonzero with workspace change is recorded as partial success | `integration` | `tests/integration/test_tool_protocol.py` | 迁移；FakeModelClient 改为 DeterministicModelStub |
| `test_resume_marks_workspace_mismatch_when_checkpoint_runtime_identity_is_stale` | PASS | checkpoint、恢复与运行身份一致性：resume marks workspace mismatch when checkpoint runtime identity is stale | `integration` | `tests/integration/test_resume_and_checkpoint.py` | 迁移；FakeModelClient 改为 DeterministicModelStub |
| `test_write_file_trace_records_minimum_tool_contract_fields` | PASS | 运行可观测性、trace 与 artifact 合同：write file trace records minimum tool contract fields | `integration` | `tests/integration/test_run_artifacts.py` | 迁移；统一字段后补 schema 断言 |
| `test_resume_marks_schema_mismatch_when_checkpoint_version_is_incompatible` | PASS | checkpoint、恢复与运行身份一致性：resume marks schema mismatch when checkpoint version is incompatible | `integration` | `tests/integration/test_resume_and_checkpoint.py` | 迁移；FakeModelClient 改为 DeterministicModelStub |
| `test_resume_marks_no_checkpoint_when_session_has_no_checkpoint_state` | PASS | checkpoint、恢复与运行身份一致性：resume marks no checkpoint when session has no checkpoint state | `integration` | `tests/integration/test_resume_and_checkpoint.py` | 迁移；FakeModelClient 改为 DeterministicModelStub |
| `test_freshness_mismatch_creates_checkpoint_before_model_completion` | PASS | checkpoint、恢复与运行身份一致性：freshness mismatch creates checkpoint before model completion | `integration` | `tests/integration/test_resume_and_checkpoint.py` | 迁移；FakeModelClient 改为 DeterministicModelStub |
| `test_runtime_identity_persists_key_execution_metadata` | PASS | checkpoint、恢复与运行身份一致性：runtime identity persists key execution metadata | `integration` | `tests/integration/test_resume_and_checkpoint.py` | 迁移；FakeModelClient 改为 DeterministicModelStub |
| `test_resume_records_runtime_identity_mismatch_fields_in_metadata_and_trace` | PASS | checkpoint、恢复与运行身份一致性：resume records runtime identity mismatch fields in metadata and trace | `integration` | `tests/integration/test_resume_and_checkpoint.py` | 迁移；FakeModelClient 改为 DeterministicModelStub |
| `test_partial_success_creates_process_note_for_exploration_history` | PASS | Agent 与分层记忆的集成行为：partial success creates process note for exploration history | `integration` | `tests/integration/test_memory_integration.py` | 迁移；FakeModelClient 改为 DeterministicModelStub |
| `test_explicit_memory_promotion_persists_durable_memory_topics` | PASS | Agent 与分层记忆的集成行为：explicit memory promotion persists durable memory topics | `integration` | `tests/integration/test_memory_integration.py` | 迁移；FakeModelClient 改为 DeterministicModelStub |
| `test_explicit_memory_promotion_supports_chinese_intent_and_labels` | PASS | Agent 与分层记忆的集成行为：explicit memory promotion supports chinese intent and labels | `integration` | `tests/integration/test_memory_integration.py` | 迁移；FakeModelClient 改为 DeterministicModelStub |
| `test_explicit_memory_promotion_rejects_secret_shaped_and_transient_lines` | PASS | Agent 与分层记忆的集成行为：explicit memory promotion rejects secret shaped and transient lines | `integration` | `tests/integration/test_memory_integration.py` | 迁移；FakeModelClient 改为 DeterministicModelStub |
| `test_explicit_memory_promotion_supersedes_matching_durable_fact` | PASS | Agent 与分层记忆的集成行为：explicit memory promotion supersedes matching durable fact | `integration` | `tests/integration/test_memory_integration.py` | 迁移；FakeModelClient 改为 DeterministicModelStub |
| `test_explicit_memory_promotion_dedupes_duplicate_durable_note` | PASS | Agent 与分层记忆的集成行为：explicit memory promotion dedupes duplicate durable note | `integration` | `tests/integration/test_memory_integration.py` | 迁移；FakeModelClient 改为 DeterministicModelStub |
| `test_agent_records_model_cache_metadata_in_last_prompt_metadata` | PASS | 运行可观测性、trace 与 artifact 合同：agent records model cache metadata in last prompt metadata | `integration` | `tests/integration/test_run_artifacts.py` | 迁移；统一字段后补 schema 断言 |
| `test_recent_transcript_entries_stay_richer_than_older_ones` | PASS | Agent 与分层记忆的集成行为：recent transcript entries stay richer than older ones | `integration` | `tests/integration/test_memory_integration.py` | 迁移；FakeModelClient 改为 DeterministicModelStub |
| `test_public_api_exports_resolve_through_package_path` | PASS | CLI/包公开接口合同：public api exports resolve through package path | `contract` | `tests/contract/test_package_surface.py` | 直接迁移并保持现有行为断言 |
| `test_reviewer_skeleton_docs_exist` | FAIL | 文档交付合同：reviewer skeleton docs exist | `contract` | `tests/contract/test_documentation.py` | 先确认产品合同与断言谁应更新；在决议前保持为已知失败 |
| `test_package_import_surface_includes_cli_entrypoints` | PASS | CLI/包公开接口合同：package import surface includes cli entrypoints | `contract` | `tests/contract/test_package_surface.py` | 直接迁移并保持现有行为断言 |
| `test_module_execution_help_works` | PASS | CLI/包公开接口合同：module execution help works | `contract` | `tests/contract/test_package_surface.py` | 直接迁移并保持现有行为断言 |

## 4. 迁移约束

1. 迁移前后必须能通过 node id 或映射表追踪每个旧测试，不能静默丢失。
2. unit/integration 中允许确定性测试替身，但替身只模拟协议和错误，不得内置 benchmark 的正确 patch。
3. `evaluation/` 的 Repo Task 主路径只接受真实 coding agent 输出的 `model_patch`，评分只来自隔离环境内的可执行测试。
4. 已知失败必须先做合同决议，再修产品或修测试；不能在搬目录时顺手改变语义。
5. diagnostics 与 live-provider 测试不进入默认快速门禁，security 和核心 integration 进入 CI 门禁。
