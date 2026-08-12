"""Subprocess entrypoint for one real zzcode Agent request."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from ...run_store import RunStore
from ...runtime import DEFAULT_SHELL_ENV_ALLOWLIST, SessionStore, ZZCode
from ...workspace import WorkspaceContext
from ..schema import TaskInstance
from ..serialization import write_json_atomic
from .models import AgentRunConfig
from .providers import build_real_model_client
from .tool_sandbox import DockerToolSandbox


_PROVIDER_SECRET_NAMES = (
    "OPENAI_API_KEY",
    "OPENAI_API_TOKEN",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "RIGHT_CODES_API_KEY",
    "GITHUB_PAT",
    "GH_PAT",
)


def _token_usage(trace_path: Path) -> dict[str, int]:
    totals = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "cached_tokens": 0}
    observed: set[str] = set()
    if not trace_path.is_file():
        return {}
    for line in trace_path.read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("event") != "model_parsed":
            continue
        metadata = (event.get("payload") or {}).get("completion_metadata") or event.get(
            "completion_metadata"
        ) or {}
        for name in totals:
            value = metadata.get(name)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                totals[name] += value
                observed.add(name)
    return {name: totals[name] for name in totals if name in observed}


def run_request(request_path: Path, response_path: Path) -> int:
    request = json.loads(request_path.read_text(encoding="utf-8"))
    task = TaskInstance.from_dict(request["task"])
    config = AgentRunConfig.from_worker_dict(request["config"])
    workspace = Path(request["workspace"]).resolve()
    artifact_dir = Path(request["artifact_dir"]).resolve()
    artifact_dir.mkdir(parents=True, exist_ok=True)
    if Path.cwd().resolve() != workspace:
        raise RuntimeError("inference worker cwd does not match its workspace")

    model = build_real_model_client(config)
    workspace_context = WorkspaceContext.build(workspace)
    runtime_root = artifact_dir / "runtime"
    agent = ZZCode(
        model_client=model,
        workspace=workspace_context,
        session_store=SessionStore(runtime_root / "sessions"),
        run_store=RunStore(runtime_root / "runs"),
        approval_policy="auto",
        max_steps=config.max_steps,
        max_new_tokens=config.max_new_tokens,
        secret_env_names=_PROVIDER_SECRET_NAMES,
        shell_env_allowlist=DEFAULT_SHELL_ENV_ALLOWLIST,
    )
    tool_sandbox = DockerToolSandbox(workspace, image=config.tool_image)
    agent.tools["run_shell"]["run"] = tool_sandbox.run_args
    started = time.monotonic()
    try:
        final_answer = agent.ask(task.problem_statement)
    except RuntimeError as exc:
        write_json_atomic(
            response_path,
            {
                "kind": "provider_error",
                "message": agent.redact_text(str(exc)),
                "duration_seconds": time.monotonic() - started,
            },
            overwrite=False,
        )
        return 20
    except Exception as exc:
        write_json_atomic(
            response_path,
            {
                "kind": "agent_error",
                "message": agent.redact_text(str(exc)),
                "exception_type": type(exc).__name__,
                "duration_seconds": time.monotonic() - started,
            },
            overwrite=False,
        )
        return 21

    task_state = agent.current_task_state
    trace_path = agent.run_store.trace_path(task_state)
    write_json_atomic(
        response_path,
        {
            "kind": "completed",
            "final_answer": agent.redact_text(final_answer),
            "duration_seconds": time.monotonic() - started,
            "tool_steps": task_state.tool_steps,
            "attempts": task_state.attempts,
            "stop_reason": task_state.stop_reason,
            "runtime_status": task_state.status,
            "runtime_run_id": task_state.run_id,
            "token_usage": _token_usage(trace_path),
        },
        overwrite=False,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--response", type=Path, required=True)
    args = parser.parse_args(argv)
    return run_request(args.request, args.response)


if __name__ == "__main__":
    sys.exit(main())
