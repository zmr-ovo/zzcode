"""Command-line entry points available in the current evaluation phase."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Sequence

from .dataset import EvaluationDataset
from .errors import EvaluationError
from .prediction import append_prediction, load_predictions, validate_predictions
from .inference import AgentRunConfig, InferenceRunner, ZZCodeAgentAdapter
from .execution import WorkspaceManager
from .serialization import write_json_atomic, write_text_atomic


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m zzcode.evaluation.cli",
        description="Validate zzcode evaluation datasets and predictions.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_dataset = subparsers.add_parser(
        "validate-dataset",
        help="validate one public/private dataset split",
    )
    validate_dataset.add_argument("--public-root", type=Path, required=True)
    validate_dataset.add_argument(
        "--private-root",
        type=Path,
        default=None,
        help="private grading root; defaults to ZZCODE_EVAL_PRIVATE_ROOT",
    )
    validate_dataset.add_argument("--split", default="dev")

    validate_prediction_file = subparsers.add_parser(
        "validate-predictions",
        help="validate predictions.jsonl against one dataset split",
    )
    validate_prediction_file.add_argument("--public-root", type=Path, required=True)
    validate_prediction_file.add_argument("--private-root", type=Path, default=None)
    validate_prediction_file.add_argument("--split", default="dev")
    validate_prediction_file.add_argument("--predictions", type=Path, required=True)

    run_inference = subparsers.add_parser(
        "run-inference",
        help="run one public Repo Task with the real zzcode Agent",
    )
    run_inference.add_argument("--public-root", type=Path, required=True)
    run_inference.add_argument("--private-root", type=Path, default=None)
    run_inference.add_argument("--split", default="dev")
    run_inference.add_argument("--instance-id", required=True)
    run_inference.add_argument("--repo-path", type=Path, required=True)
    run_inference.add_argument("--workspace-root", type=Path, required=True)
    run_inference.add_argument("--artifact-dir", type=Path, required=True)
    run_inference.add_argument("--provider", choices=("openai", "anthropic", "ollama"), required=True)
    run_inference.add_argument("--model", required=True)
    run_inference.add_argument("--base-url", default=None)
    run_inference.add_argument("--host", default=None)
    run_inference.add_argument("--temperature", type=float, default=0.0)
    run_inference.add_argument("--top-p", type=float, default=0.9)
    run_inference.add_argument("--max-steps", type=int, default=30)
    run_inference.add_argument("--max-new-tokens", type=int, default=8192)
    run_inference.add_argument("--timeout-seconds", type=float, default=900)
    run_inference.add_argument("--provider-timeout-seconds", type=float, default=300)
    run_inference.add_argument("--tool-image", default="zzcode-eval-py313:phase4")
    return parser


def _private_root(argument: Path | None) -> Path:
    if argument is not None:
        return argument
    configured = os.environ.get("ZZCODE_EVAL_PRIVATE_ROOT")
    if not configured:
        raise EvaluationError(
            "private root is required via --private-root or ZZCODE_EVAL_PRIVATE_ROOT"
        )
    return Path(configured)


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    try:
        dataset = EvaluationDataset.load(
            public_root=args.public_root,
            private_root=_private_root(args.private_root),
            split=args.split,
        )
        result: dict[str, object] = {
            "status": "valid",
            "split": dataset.split,
            "task_count": len(dataset.tasks()),
            "dataset_digest": dataset.digest(),
        }
        if args.command == "validate-predictions":
            predictions = load_predictions(args.predictions)
            validate_predictions(dataset.tasks(), predictions)
            result["prediction_count"] = len(predictions)
        elif args.command == "run-inference":
            tasks = {task.instance_id: task for task in dataset.tasks()}
            try:
                task = tasks[args.instance_id]
            except KeyError as exc:
                raise EvaluationError(
                    f"instance_id is not in split {dataset.split}: {args.instance_id}"
                ) from exc
            config = AgentRunConfig(
                provider=args.provider,
                model=args.model,
                temperature=args.temperature,
                top_p=args.top_p,
                max_steps=args.max_steps,
                max_new_tokens=args.max_new_tokens,
                timeout_seconds=args.timeout_seconds,
                provider_timeout_seconds=args.provider_timeout_seconds,
                base_url=args.base_url,
                host=args.host,
                tool_image=args.tool_image,
            )
            workspace_manager = WorkspaceManager(
                args.workspace_root,
                {task.repo: args.repo_path},
            )
            inference = InferenceRunner(workspace_manager, ZZCodeAgentAdapter()).run(
                task,
                config,
                args.artifact_dir,
            )
            write_json_atomic(
                args.artifact_dir / "agent_result.json",
                inference.agent_result.to_dict(),
                overwrite=False,
            )
            if inference.prediction is not None:
                write_text_atomic(
                    args.artifact_dir / "patch.diff",
                    inference.prediction.model_patch,
                    overwrite=False,
                )
                append_prediction(
                    args.artifact_dir / "predictions.jsonl",
                    inference.prediction,
                )
            result.update(
                {
                    "instance_id": task.instance_id,
                    "agent_status": inference.agent_result.status.value,
                    "patch_generated": inference.agent_result.patch_generated,
                    "failure_type": (
                        inference.agent_result.failure.failure_type.value
                        if inference.agent_result.failure
                        else None
                    ),
                    "workspace": str(inference.workspace),
                }
            )
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except EvaluationError as exc:
        parser.exit(2, f"error: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
