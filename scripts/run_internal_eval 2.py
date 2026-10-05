#!/usr/bin/env python3
"""Run real zzcode inference for one verified internal Repo Task."""

from __future__ import annotations

import sys

from zzcode.evaluation.cli import main


if __name__ == "__main__":
    raise SystemExit(main(["run-inference", *sys.argv[1:]]))
