#!/usr/bin/env python3
"""Render report.md from one existing Evaluation run directory."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from zzcode.evaluation import render_run_directory  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="从已有 Evaluation JSON 产物生成 Markdown 报告。")
    parser.add_argument("run_root", type=Path)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    print(render_run_directory(args.run_root, args.output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
