import os
from pathlib import Path

import pytest

from zzcode.evaluation import (
    DockerRunner,
    DockerTestExecutor,
    EvaluationDataset,
    LocalGradingHarness,
    ResolvedStatus,
    ResourceLimits,
    WorkspaceManager,
)


@pytest.mark.docker
@pytest.mark.slow
@pytest.mark.skipif(
    os.environ.get("RUN_DOCKER_TESTS") != "1",
    reason="set RUN_DOCKER_TESTS=1 to validate Phase 6 Repo Tasks",
)
def test_phase6_repo_tasks_pass_null_and_gold_stability_gates(tmp_path):
    repo = Path(__file__).resolve().parents[3]
    private_root = repo / "evaluation" / "private"
    if not private_root.is_dir():
        pytest.skip("Phase 6 private bundle is intentionally distributed separately")
    dataset = EvaluationDataset.load(
        repo / "evaluation" / "datasets" / "zzcode-bench-v1",
        private_root,
        "dev",
    )
    docker = DockerRunner(allowed_mount_roots=(tmp_path,))
    executor = DockerTestExecutor(
        docker,
        image=os.environ.get("ZZCODE_EVAL_IMAGE", "zzcode-eval-py313:phase4"),
        limits=ResourceLimits(cpus=1.0, memory_mb=1024, pids_limit=128, tmpfs_mb=256),
    )

    for task in dataset.tasks():
        private = dataset.private_spec(task.instance_id)
        null_harness = LocalGradingHarness(
            WorkspaceManager(
                tmp_path / task.instance_id / "null-workspace",
                {task.repo: repo},
            ),
            test_executor=executor,
        )
        null = null_harness.run(
            task,
            private,
            None,
            tmp_path / task.instance_id / "null-artifacts",
            timeout_seconds=120,
        )
        assert null.decision.resolved_status in {ResolvedStatus.NO, ResolvedStatus.PARTIAL}
        assert null.decision.fail_to_pass_rate < 1.0
        assert null.decision.pass_to_pass_rate == 1.0

        gold_patch = private.gold_patch_path.read_text(encoding="utf-8")
        gold_statuses = []
        gold_digests = set()
        for repetition in range(1, 4):
            harness = LocalGradingHarness(
                WorkspaceManager(
                    tmp_path / task.instance_id / f"gold-{repetition}-workspace",
                    {task.repo: repo},
                ),
                test_executor=executor,
            )
            gold = harness.run(
                task,
                private,
                gold_patch,
                tmp_path / task.instance_id / f"gold-{repetition}-artifacts",
                timeout_seconds=120,
            )
            gold_statuses.append(gold.decision.resolved_status)
            gold_digests.update(
                run.image_digest for run in (gold.fail_to_pass, gold.pass_to_pass)
            )

        assert gold_statuses == [ResolvedStatus.FULL] * 3
        assert len(gold_digests) == 1
        assert next(iter(gold_digests)).startswith("sha256:")
