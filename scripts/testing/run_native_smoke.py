"""Read-only native tool round trip against the configured provider."""

import argparse
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from zzcode.cli import _build_model_client, _load_env_files, build_arg_parser  # noqa: E402
from zzcode.agent.coordinator import SessionStore, ZZCode  # noqa: E402
from zzcode.context.workspace import WorkspaceContext  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--provider", choices=("openai", "anthropic", "ollama"), default="openai"
    )
    parser.add_argument("--model")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    _load_env_files(ROOT)
    settings = build_arg_parser().parse_args(
        ["--provider", args.provider, "--max-new-tokens", "2048"]
    )
    if args.model:
        settings.model = args.model
    result = {"provider": args.provider, "passed": False, "real_model": True}
    try:
        model = _build_model_client(settings)
        with tempfile.TemporaryDirectory(prefix="zzcode-native-smoke-") as directory:
            root = Path(directory)
            (root / "probe.txt").write_text("native-roundtrip-ok\n")
            agent = ZZCode(
                model,
                WorkspaceContext.build(root, repo_root_override=root),
                SessionStore(root / ".zzcode/sessions"),
                approval_policy="never",
                read_only=True,
                max_steps=1,
                max_new_tokens=2048,
            )
            agent.tools = {"read_file": agent.tools["read_file"]}
            agent.refresh_prefix(force=True)
            answer = agent.ask(
                "Call read_file to inspect probe.txt. After receiving the result, repeat its exact contents as your final answer."
            )
            calls = [
                block
                for item in agent.session["history"]
                for block in item.get("message", {}).get("content", [])
                if block["type"] == "tool_call"
            ]
            results = [
                block
                for item in agent.session["history"]
                for block in item.get("message", {}).get("content", [])
                if block["type"] == "tool_result"
            ]
            matched = bool(calls) and [call["call_id"] for call in calls] == [
                item["call_id"] for item in results
            ]
            result.update(
                status=agent.current_task_state.status,
                stop_reason=agent.current_task_state.stop_reason,
                tool_steps=agent.current_task_state.tool_steps,
                call_result_ids_match=matched,
                final_completion_usage=agent.last_completion_metadata,
                passed=matched
                and "native-roundtrip-ok" in answer
                and agent.current_task_state.status == "completed",
            )
    except RuntimeError as exc:
        result.update(
            status="provider_error", error_type=type(exc).__name__, error=str(exc)
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
