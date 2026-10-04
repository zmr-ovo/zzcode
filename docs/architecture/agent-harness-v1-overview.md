# Agent Harness v1 — current implementation and P0 contract

This describes the implementation frozen at P0, not the target v2 architecture.

## Architecture map

```text
CLI → ZZCode.ask → ContextManager → ModelClient.complete → ZZCode.parse
                         ↑                                ↓
                   LayeredMemory ← ZZCode.run_tool ← tool proposal
                         ↓                ↓
                   SessionStore     RunStore / task state
```

| Module | Current responsibility | Future extraction boundary |
|---|---|---|
| `runtime.py` | Agent loop, parsing, approval, tool guards, memory updates, checkpoints, task state and reports | Coordinator / Loop / Gateway; preserve `ZZCode.ask` |
| `context_manager.py` | Character budgets, section floors, reduction order and prompt metadata | Token counting and compaction; preserve latest request |
| `tools.py` | Registry, validation and implementations | `tooling/` contracts and Gateway; avoid same-name package collision |
| `models.py` | HTTP provider adapters returning text and usage metadata | Structured messages and native tools |
| `memory.py` | Working, episodic and durable memory, freshness | Retain distinction from checkpoints/compaction |
| `run_store.py` | Per-run state, trace and report | Durable operation ledger remains a separate responsibility |
| `evaluation/` | Isolated inference, patch collection, grading and artifacts | Shared runtime entry and Executor contracts |

## Frozen behavior

- Tool steps count proposed parsed tool calls, including rejected calls; attempts count loop model calls. Provider transport retries are not separate runtime attempts today.
- Unknown tools, invalid arguments, repetition and denied approval return textual errors. Denied tools are not executed.
- Shell nonzero exit with detected file changes is `partial_success`; output is clipped and exit code is parsed from text.
- Model malformed output can recover within `max(max_steps * 3, max_steps + 4)` attempts; exhaustion yields `retry_limit_reached`. Tool budget exhaustion allows one existing final-only model call. If it returns another tool, that tool does not execute and the run yields `step_limit_reached`.
- Non-empty final/plain text yields `final_answer_returned`; this is not independent evidence that a code task is resolved.
- Resume checks checkpoint schema, workspace/runtime identity and file freshness; stale information is re-anchored.
- Trace/report use runtime redaction; ordinary raw session data is not a new durable side-effect ledger.
- Default features: memory, relevant memory, context reduction and prompt cache enabled. No v2 implementation is enabled at P0.

## Known limits

Text tool protocol, 12000-character context budget, linear session JSON, host Shell execution and no persisted tool operation identity. There is no implemented Completion Gate/Verification Profile in this checkout; the earlier implementation was reverted. Filesystem path checks and environment filtering do not sandbox arbitrary Shell commands.

P0 regression evidence lives in `artifacts/p0-baseline/`. The 12 scripted diagnostic tasks prove mechanism behavior, not real-model coding success.
