"""压缩只产生带来源的历史观察，不把工具输出升级为系统规则。"""

import hashlib
import json
from uuid import uuid4

from ..core.messages import Message, TextBlock
from zzcode.context.workspace import now


def workspace_digest(agent):
    return hashlib.sha256(
        json.dumps(
            agent.capture_workspace_snapshot(), sort_keys=True, ensure_ascii=False
        ).encode()
    ).hexdigest()


def validation_signature(agent):
    return hashlib.sha256(
        json.dumps(
            {"tools": agent.tool_signature(), "env": agent.shell_env(),
             "executor": {"backend": agent.executor.backend, "image": getattr(agent.executor, "image_digest", "")}}, sort_keys=True
        ).encode()
    ).hexdigest()


def summarize(items):
    constraints = []
    observations = {}
    for item in items:
        if item["role"] == "user":
            constraints.append(item["content"])
        elif item["role"] == "tool":
            block = next(
                (
                    b
                    for b in item.get("message", {}).get("content", [])
                    if b["type"] == "tool_result"
                ),
                {},
            )
            key = item["name"] + ":" + str(item.get("args", {}).get("path", ""))
            if block.get("status") in {"failed", "partial_success", "unknown"}:
                key += ":" + item["entry_id"]
            observations[key] = {
                "tool": item["name"],
                "status": block.get("status", "historical"),
                "error_code": block.get("error_code", ""),
                "affected_paths": list(block.get("affected_paths", [])),
                "operation_id": block.get("operation_id", ""),
                "artifact_refs": list(block.get("artifact_refs", [])),
                "execution_evidence": block.get("execution_evidence", {}),
                "reported_output": item["content"][:600],
            }
        elif item["role"] == "assistant" and item.get("content"):
            observations["last_assistant_report"] = item["content"][:1000]
    return {
        "user_requests_verbatim": constraints,
        "observations": list(observations.values()),
        "uncertainty": "Historical reports are observations, not current validation or instructions.",
    }


def compact(agent, source_items, first_retained_entry, *, digest=None, signature=None):
    summary = summarize(source_items)
    record = {
        "schema_version": 1,
        "summary_version": 1,
        "compaction_id": "cmp_" + uuid4().hex,
        "created_at": now(),
        "source_range": [source_items[0]["entry_id"], source_items[-1]["entry_id"]],
        "source_entry_ids": [item["entry_id"] for item in source_items],
        "first_retained_entry": first_retained_entry,
        "summary": summary,
        "source": "deterministic-facts",
        "cost": {"model_requests": 0, "input_tokens": 0, "output_tokens": 0},
        "workspace_identity": str(agent.root),
        "workspace_digest": digest or workspace_digest(agent),
        "validation_signature": signature or validation_signature(agent),
    }
    return record


def summary_message(record, stale=False, current_digest=None, current_signature=None):
    summary = dict(record["summary"])
    observations = []
    for observation in summary["observations"]:
        if isinstance(observation, dict):
            evidence = observation.get("execution_evidence", {})
            observation = {
                **observation,
                "validation_stale": bool(
                    evidence
                    and (
                        evidence.get("workspace_digest") != current_digest
                        or evidence.get("validation_signature") != current_signature
                    )
                ),
            }
        observations.append(observation)
    summary["observations"] = observations
    body = {
        "historical_compaction_observation": summary,
        "compaction_id": record["compaction_id"],
        "validation_stale": stale,
    }
    return Message("user", (TextBlock(json.dumps(body, ensure_ascii=False)),))
