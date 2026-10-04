# zzcode P0 review pack

## Project pitch

zzcode is a local Coding Agent Harness with explicit tool boundaries, context and layered memory, checkpoint/resume and executable evaluation. The v2 upgrade prioritizes protocol reliability, side-effect recovery and measurable context improvements.

## Architecture map

See [current Agent Harness v1 overview](../architecture/agent-harness-v1-overview.md) and [upgrade plan v2](../zzcode-upgrade-plan-v2.md). The overview describes existing contracts, including missing completion verification, rather than claiming planned capabilities already exist.

## Benchmark evidence

See [P0 result](../testing/p0-result.md) and `artifacts/p0-baseline/verified/manifest.json`. Product and harness tests are separate from the 12 deterministic scripted mechanism tasks. Real Repo Smoke uses two development tasks; held-out test tasks are not used for migration tuning.

## Sample run artifact list

- `artifacts/p0-baseline/verified/product-tests.txt` and `product-tests.xml`
- `artifacts/p0-baseline/verified/harness-tests.txt` and `harness-tests.xml`
- `artifacts/p0-baseline/verified/regression.json`
- `artifacts/p0-baseline/verified/regression-runs/<task-id>/`: trace, report, task state
- `artifacts/p0-baseline/verified/golden-runs/<case-id>/`: controlled legacy transcripts and execution artifacts
- `artifacts/p0-baseline/verified/manifest.json`: environment, hashes and verification status

Regenerate in the clean environment described in the P0 result, with `python scripts/freeze_p0_baseline.py --output artifacts/p0-baseline/<new-run>`. Each output directory must be new or empty; previous evidence is not overwritten.
