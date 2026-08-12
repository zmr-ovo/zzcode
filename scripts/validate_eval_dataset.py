"""Thin compatibility entry point for Phase 1 dataset validation."""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from zzcode.evaluation.cli import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main(["validate-dataset", *sys.argv[1:]]))
