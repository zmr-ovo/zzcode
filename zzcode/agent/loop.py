"""唯一同步运行循环；Coordinator 负责依赖和持久化边界。"""
import json
import time
from zzcode.core.messages import Message, ToolResult, ModelResponse, ProviderProtocolError
from zzcode.context.budget import ContextCapacityError, ContextOverflowError
from zzcode.storage.session import SessionError
from zzcode.context.workspace import now, clip
from .state import TaskState
from .policy import CompletionPolicy
from zzcode.execution.gateway import arguments_hash
from .coordinator import CHECKPOINT_NONE_STATUS, CHECKPOINT_PARTIAL_STALE_STATUS, CHECKPOINT_WORKSPACE_MISMATCH_STATUS


def run_loop(agent, request_policy):
    user_message = request_policy.prompt
    run_started_at = time.monotonic()
    task_state = TaskState.create(run_id=agent.new_run_id(), task_id=agent.new_task_id(), user_request=user_message)
    task_state.resume_status = agent.resume_state.get("status", CHECKPOINT_NONE_STATUS)
    task_state.completion = {"task_type": request_policy.task_type, "required_verification": [arguments_hash({"command": command}) for command in request_policy.verification_commands]}
    agent.current_task_state = task_state
    agent.current_run_dir = agent.run_store.start_run(task_state)
    agent._run_deadline = run_started_at + agent.max_run_seconds
    policy = CompletionPolicy(request_policy, agent)
    pending = {}
    for item in agent.session["history"]:
        for block in item.get("message", {}).get("content", []):
            if block["type"] == "tool_call":
                pending[block["call_id"]] = block
            elif block["type"] == "tool_result":
                pending.pop(block["call_id"], None)
    for call_id, call in pending.items():
        row = agent.gateway.ledger.get(agent.session["id"], call_id)
        result = agent.gateway.ledger.result(row) if row else None
        if result is None:
            result = ToolResult(call_id, call["name"], "Execution outcome unknown after interruption; inspect workspace before repeating.", "unknown")
        agent.record({"role": "tool", "name": call["name"], "args": call["arguments"], "content": result.content,
                     "message": Message("tool", (result,)).to_dict(), "created_at": now()})
    agent.session["protocol_version"] = 2
    agent._turn_start_index = len(agent.session["history"])
    agent.memory.set_task_summary(user_message)
    agent.record({"role": "user", "content": user_message, "created_at": now()})

    agent.emit_trace(
        task_state,
        "run_started",
        {
            "task_id": task_state.task_id,
            "user_request": clip(user_message, 300),
        },
    )

    agent.current_transport_retries = 0
    agent.current_model_requests = 0
    overflow_rebuilt = False
    tool_steps = 0
    attempts = 0
    max_attempts = max(agent.max_steps * 3, agent.max_steps + 4)
    finalization_attempted = False

    yield from agent.drain_events()

    # 这是 agent 的主循环，可以按“感知 -> 决策 -> 行动 -> 记录”来理解：
    # 1. 感知：重新组 prompt，把当前状态整理给模型看
    # 2. 决策：让模型返回一个工具调用，或一个最终答案
    # 3. 行动：如果是工具调用，就执行工具
    # 4. 记录：把结果写回 history / task_state / trace / memory
    # 然后进入下一轮，直到停机条件满足
    while attempts < max_attempts or (tool_steps >= agent.max_steps and not finalization_attempted):
        if time.monotonic() - run_started_at >= agent.max_run_seconds:
            task_state.stop("time_limit", final_answer="Stopped after reaching the run time limit.")
            break
        attempts += 1
        task_state.record_attempt()
        agent.run_store.write_task_state(task_state)
        prompt_started_at = time.monotonic()
        prompt, prompt_metadata = agent._build_prompt_and_metadata(user_message)
        agent.emit_trace(
            task_state,
            "prompt_built",
            {
                "prompt_metadata": prompt_metadata,
                "duration_ms": int((time.monotonic() - prompt_started_at) * 1000),
            },
        )
        if prompt_metadata.get("resume_status") == CHECKPOINT_PARTIAL_STALE_STATUS:
            checkpoint = agent.create_checkpoint(task_state, user_message, trigger="freshness_mismatch")
            agent.run_store.write_task_state(task_state)
            agent.emit_trace(
                task_state,
                "checkpoint_created",
                {
                    "checkpoint_id": checkpoint["checkpoint_id"],
                    "trigger": "freshness_mismatch",
                },
            )
        elif prompt_metadata.get("resume_status") == CHECKPOINT_WORKSPACE_MISMATCH_STATUS:
            agent.emit_trace(
                task_state,
                "runtime_identity_mismatch",
                {
                    "fields": list(prompt_metadata.get("runtime_identity_mismatch_fields", [])),
                },
            )
            checkpoint = agent.create_checkpoint(task_state, user_message, trigger="workspace_mismatch")
            agent.run_store.write_task_state(task_state)
            agent.emit_trace(
                task_state,
                "checkpoint_created",
                {
                    "checkpoint_id": checkpoint["checkpoint_id"],
                    "trigger": "workspace_mismatch",
                },
            )
        if prompt_metadata.get("budget_reductions"):
            checkpoint = agent.create_checkpoint(task_state, user_message, trigger="context_reduction")
            agent.run_store.write_task_state(task_state)
            agent.emit_trace(
                task_state,
                "checkpoint_created",
                {
                    "checkpoint_id": checkpoint["checkpoint_id"],
                    "trigger": "context_reduction",
                },
            )
        finalization_only = tool_steps >= agent.max_steps
        finalization_attempted = finalization_attempted or finalization_only
        model_started_at = time.monotonic()
        try:
            request = agent.context_manager.build_request(user_message, prompt, prompt_metadata, finalization_only, conservative=overflow_rebuilt)
            request.validate()
            agent.emit_trace(
                task_state,
                "model_requested",
                {
                    "finalization_only": tool_steps >= agent.max_steps,
                    "attempts": task_state.attempts,
                    "tool_steps": task_state.tool_steps,
                    "prompt_cache_key": prompt_metadata.get("prompt_cache_key"),
                },
            )
            yield from agent.drain_events()
            if time.monotonic() >= agent._run_deadline:
                task_state.stop("time_limit", final_answer="Stopped after reaching the run time limit.")
                agent.emit_trace(task_state, "model_rejected", {"reason": "time_limit"})
                break
            prompt_metadata["native_request_chars"] = len(request.system) + sum(len(json.dumps(message.to_dict())) for message in request.messages) + sum(len(json.dumps(spec.input_schema)) for spec in request.tools)
            prompt_metadata["native_request_over_budget"] = prompt_metadata["native_request_chars"] > agent.context_manager.total_budget
            prompt_metadata["native_history_messages"] = len(request.messages)
            try:
                agent.current_model_requests += 1
                response = agent.model_client.complete(request)
            finally:
                agent.current_transport_retries += getattr(agent.model_client, "transport_retries", 0)
            if not isinstance(response, ModelResponse):
                raise ProviderProtocolError("provider must return ModelResponse")
            response.validate()
            previous_ids = {block["call_id"] for item in agent.session["history"]
                            for block in item.get("message", {}).get("content", []) if block.get("type") == "tool_call"}
            if any(call.call_id in previous_ids for call in response.tool_calls):
                raise ProviderProtocolError("provider reused a tool call ID")
        except ContextCapacityError as exc:
            agent.emit_trace(task_state, "model_rejected", {"reason": "context_capacity_exceeded"})
            task_state.stop("context_capacity_exceeded", final_answer=str(exc))
            break
        except ContextOverflowError as exc:
            agent.emit_trace(task_state, "model_failed", {"reason": "context_overflow"})
            if overflow_rebuilt:
                task_state.stop("context_capacity_exceeded", final_answer=str(exc))
                break
            overflow_rebuilt = True
            continue
        except SessionError as exc:
            task_state.stop("session_persistence_error", status="failed", final_answer=str(exc))
            agent.run_store.write_task_state(task_state)
            raise
        except ProviderProtocolError as exc:
            agent.emit_trace(task_state, "model_failed", {"reason": "provider_protocol_error"})
            agent.record({"role": "user", "content": agent.retry_notice(str(exc)), "created_at": now()})
            if finalization_only:
                break
            continue
        except RuntimeError as exc:
            agent.emit_trace(task_state, "model_failed", {"reason": "provider_error"})
            task_state.stop_model_error(str(exc))
            agent.run_store.write_task_state(task_state)
            agent.publish_report(task_state, agent.redact_artifact(agent.build_report(task_state)))
            raise
        agent.token_budget.observe(prompt_metadata["estimated_input_tokens"], response.usage)
        public_fields = {"input_tokens", "output_tokens", "cached_tokens", "total_tokens", "cache_hit",
                         "prompt_cache_supported", "prompt_cache_key", "prompt_cache_retention"}
        completion_metadata = {key: value for key, value in dict(getattr(agent.model_client, "last_completion_metadata", {}) or {}).items() if key in public_fields}
        completion_metadata.update(response.usage.metadata())
        completion_metadata.update({key: value for key, value in response.metadata.items() if key in {"input_tokens", "output_tokens", "cached_tokens", "total_tokens", "cache_hit", "prompt_cache_supported"}})
        if completion_metadata:
            # 把后端返回的 usage/cache 统计并回 prompt_metadata，
            # 方便统一写入 report 和 trace。
            prompt_metadata.update(completion_metadata)
        agent.last_completion_metadata = completion_metadata
        agent.last_prompt_metadata = prompt_metadata
        agent.record({"role": "assistant", "content": response.text, "message": response.message.to_dict(), "created_at": now()})
        kind = "tool" if response.tool_calls else "final"
        if response.stop_reason not in {"end_turn", "tool_call"}:
            kind = "stopped"
        elif not response.tool_calls and not response.text.strip():
            kind = "retry"
        if kind == "final":
            agent._model_final = True
            task_state.completion["model_final"] = True
            agent.run_store.write_task_state(task_state)
        agent.emit_trace(
            task_state,
            "model_parsed",
            {
                "kind": kind,
                "stop_reason": response.stop_reason,
                "finalization_only": finalization_only,
                "completion_metadata": completion_metadata,
                "duration_ms": int((time.monotonic() - model_started_at) * 1000),
            },
        )

        yield from agent.drain_events()

        if time.monotonic() - run_started_at >= agent.max_run_seconds:
            kind = "stopped"
            task_state.stop("time_limit", final_answer="Stopped after reaching the run time limit.")
        for call in response.tool_calls:
            name, args = call.name, call.arguments
            expired = time.monotonic() - run_started_at >= agent.max_run_seconds
            if expired:
                kind = "stopped"
                task_state.stop("time_limit", final_answer="Stopped after reaching the run time limit.")
            if kind == "stopped" or tool_steps >= agent.max_steps:
                cancelled = agent.run_tool(name, args, call_id=call.call_id, argument_error=call.argument_error, cancel_reason="time limit" if expired else response.stop_reason if kind == "stopped" else "tool budget exhausted")
                agent.record({"role": "tool", "name": name, "args": args, "content": cancelled.content, "created_at": now(),
                             "message": Message("tool", (cancelled,)).to_dict()})
                agent.emit_trace(task_state, "tool_cancelled", {"call_id": call.call_id, "name": name, "reason": cancelled.content})
                yield from agent.drain_events()
                continue
            tool_steps += 1
            task_state.record_tool(name)
            tool_started_at = time.monotonic()
            result = agent.run_tool(name, args, call_id=call.call_id, argument_error=call.argument_error)
            agent.record({"role": "tool", "name": name, "args": args, "content": result.content,
                         "message": Message("tool", (result,)).to_dict(), "created_at": now()})
            agent.run_store.write_task_state(task_state)
            agent.emit_trace(
                task_state,
                "tool_executed",
                {
                    "call_id": call.call_id,
                    "name": name,
                    "args": args,
                    "result": clip(result.content, 500),
                    "duration_ms": int((time.monotonic() - tool_started_at) * 1000),
                    **agent.tool_metadata(result),
                },
            )
            checkpoint = agent.create_checkpoint(task_state, user_message, trigger="tool_executed")
            agent.run_store.write_task_state(task_state)
            agent.emit_trace(
                task_state,
                "checkpoint_created",
                {
                    "checkpoint_id": checkpoint["checkpoint_id"],
                    "trigger": "tool_executed",
                },
            )

            yield from agent.drain_events()

        if task_state.stop_reason == "time_limit":
            break
        if kind == "stopped":
            final = response.text.strip() or f"Stopped: model returned {response.stop_reason}."
            task_state.stop(response.stop_reason, final_answer=final)
            break
        if kind == "tool":
            if finalization_only:
                break
            continue

        if kind == "retry":
            if finalization_only:
                break
            agent.record({"role": "user", "content": agent.retry_notice("empty response"), "created_at": now()})
            agent.run_store.write_task_state(task_state)
            continue

        final = response.text.strip()
        decision = policy.assess(final, agent)
        tool_steps = task_state.tool_steps
        task_state.completion.update({"accepted": decision.accepted, "reason": decision.reason,
                                      "verification": agent.redact_artifact(agent._verification)})
        agent.run_store.write_task_state(task_state)
        if not decision.accepted:
            agent.emit_trace(task_state, "completion_rejected", {"reason": decision.reason})
            yield from agent.drain_events()
            if not decision.retryable or finalization_only or policy.rejections >= request_policy.max_final_rejections:
                task_state.stop(decision.reason, final_answer=final)
                break
            agent.record({"role": "user", "content": "Completion rejected: " + decision.reason, "created_at": now()})
            continue
        task_state.finish_success(final)
        agent.promote_durable_memory(user_message, final)
        checkpoint = agent.create_checkpoint(task_state, user_message, trigger="run_finished")
        agent.run_store.write_task_state(task_state)
        agent.emit_trace(
            task_state,
            "checkpoint_created",
            {
                "checkpoint_id": checkpoint["checkpoint_id"],
                "trigger": "run_finished",
            },
        )
        agent.emit_trace(
            task_state,
            "run_finished",
            {
                "status": task_state.status,
                "stop_reason": task_state.stop_reason,
                "final_answer": final,
                "run_duration_ms": int((time.monotonic() - run_started_at) * 1000),
            },
        )
        agent.publish_report(task_state, agent.redact_artifact(agent.build_report(task_state)))
        yield from agent.drain_events()
        return final

    if task_state.status == "stopped":
        final = task_state.final_answer
    elif attempts >= max_attempts and tool_steps < agent.max_steps:
        final = "Stopped after too many malformed model responses without a valid tool call or final answer."
        task_state.stop_retry_limit(final)
    else:
        final = "Stopped after reaching the step limit without a final answer."
        task_state.stop_step_limit(final)
    agent.record({"role": "assistant", "content": final, "created_at": now()})
    agent.promote_durable_memory(user_message, final)
    agent.run_store.write_task_state(task_state)
    checkpoint = agent.create_checkpoint(task_state, user_message, trigger=task_state.stop_reason or "run_stopped")
    agent.emit_trace(
        task_state,
        "checkpoint_created",
        {
            "checkpoint_id": checkpoint["checkpoint_id"],
            "trigger": task_state.stop_reason or "run_stopped",
        },
    )
    agent.emit_trace(
        task_state,
        "run_finished",
        {
            "status": task_state.status,
            "stop_reason": task_state.stop_reason,
            "final_answer": final,
            "run_duration_ms": int((time.monotonic() - run_started_at) * 1000),
        },
    )
    agent.publish_report(task_state, agent.redact_artifact(agent.build_report(task_state)))
    yield from agent.drain_events()
    return final

