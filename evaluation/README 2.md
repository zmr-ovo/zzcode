# zzcode Evaluation

This directory contains evaluation data, harness self-tests, environment
definitions, run artifacts, and reports. Reusable Python code lives in
`zzcode/evaluation/`.

Phase 1 implements only the data boundary:

- versioned public `TaskInstance` values;
- separately loaded private grading specs;
- deterministic dataset digests;
- SWE-bench-compatible `predictions.jsonl` I/O;
- schema and private-data leakage validation.

It does not yet run an Agent, collect a Git patch, create a grading workspace,
inject hidden tests, or assign `FULL / PARTIAL / NO`. Those are later phases.

## Public dataset layout

```text
evaluation/datasets/<dataset-name>/
├── manifest.jsonl
├── dataset-card.md
├── splits/
│   ├── dev.txt
│   └── test.txt
└── instances/<instance-id>/
    ├── problem_statement.md
    └── task.json
```

The public tree must not contain `gold.patch`, `test.patch`, hidden test ids,
or `FAIL_TO_PASS` / `PASS_TO_PASS`.

## Private grading layout

Set `ZZCODE_EVAL_PRIVATE_ROOT` to a directory outside the public dataset tree.
The loader accepts either the dataset-specific directory directly or a parent
containing a directory with the public dataset's name:

```text
$ZZCODE_EVAL_PRIVATE_ROOT/<dataset-name>/<instance-id>/
├── grading.json
├── gold.patch
└── test.patch
```

The private root is intentionally not created or committed by this repository.

## Validate a split

```bash
python -m zzcode.evaluation.cli validate-dataset \
  --public-root evaluation/datasets/zzcode-bench-v1 \
  --private-root "$ZZCODE_EVAL_PRIVATE_ROOT" \
  --split dev
```

The command prints a task count and `sha256:` dataset digest. The digest changes
when selected public task data, F2P/P2P lists, `gold.patch`, or `test.patch`
changes.

Validate a prediction file with:

```bash
python -m zzcode.evaluation.cli validate-predictions \
  --public-root evaluation/datasets/zzcode-bench-v1 \
  --private-root "$ZZCODE_EVAL_PRIVATE_ROOT" \
  --split dev \
  --predictions evaluation/runs/<run-id>/predictions.jsonl
```

`evaluation/runs/` and `evaluation/reports/` are ignored because they are
generated artifacts.
