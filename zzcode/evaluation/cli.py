"""Command-line entry points available in the current evaluation phase."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Sequence

from .dataset import EvaluationDataset
from .errors import EvaluationError
from .prediction import load_predictions, validate_predictions


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
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except EvaluationError as exc:
        parser.exit(2, f"error: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
