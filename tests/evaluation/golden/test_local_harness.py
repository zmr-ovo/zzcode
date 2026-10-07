import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from zzcode.evaluation import (
    FailureType,
    ContainerHandle,
    DockerRunner,
    DockerTestExecutor,
    LocalGradingHarness,
    PrivateTestSpec,
    ResolvedStatus,
    ResourceLimits,
    TaskInstance,
    WorkspaceManager,
)


BASE_SOURCE = """def normalize(value):
    return value


def clamp_non_negative(value):
    return value


def legacy_increment(value):
    return value + 1
"""

GOLD_SOURCE = """def normalize(value):
    return abs(value)


def clamp_non_negative(value):
    return max(0, value)


def legacy_increment(value):
    return value + 1
"""

PARTIAL_SOURCE = """def normalize(value):
    return abs(value)


def clamp_non_negative(value):
    return value


def legacy_increment(value):
    return value + 1
"""

REGRESSION_SOURCE = """def normalize(value):
    return abs(value)


def clamp_non_negative(value):
    return max(0, value)


def legacy_increment(value):
    return value - 1
"""

PUBLIC_TESTS = """from calc import legacy_increment, normalize


def test_normalize_positive():
    assert normalize(3) == 3


def test_legacy_increment():
    assert legacy_increment(2) == 3
"""

HIDDEN_TESTS = """from calc import clamp_non_negative, normalize


def test_normalize_negative():
    assert normalize(-3) == 3


def test_clamp_negative():
    assert clamp_non_negative(-2) == 0
"""


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        text=True,
        capture_output=True,
        check=True,
    )
    return result.stdout.strip()


def _commit_source(repo: Path, base_commit: str, source: str, message: str) -> str:
    _git(repo, "checkout", "--quiet", "--detach", base_commit)
    (repo / "calc.py").write_text(source, encoding="utf-8")
    _git(repo, "add", "calc.py")
    _git(repo, "commit", "--quiet", "-m", message)
    return _git(repo, "diff", "--binary", "--no-ext-diff", base_commit, "HEAD") + "\n"


@pytest.fixture
def golden_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "--quiet")
    _git(repo, "config", "user.name", "Evaluation Test")
    _git(repo, "config", "user.email", "evaluation@example.invalid")
    (repo / "calc.py").write_text(BASE_SOURCE, encoding="utf-8")
    tests = repo / "tests"
    tests.mkdir()
    (tests / "test_calc.py").write_text(PUBLIC_TESTS, encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "--quiet", "-m", "base")
    base_commit = _git(repo, "rev-parse", "HEAD")

    hidden = repo / "hidden_tests"
    hidden.mkdir()
    (hidden / "test_calc_hidden.py").write_text(HIDDEN_TESTS, encoding="utf-8")
    _git(repo, "add", "hidden_tests/test_calc_hidden.py")
    _git(repo, "commit", "--quiet", "-m", "private tests")
    test_patch = _git(repo, "diff", "--binary", "--no-ext-diff", base_commit, "HEAD") + "\n"
    gold_patch = _commit_source(repo, base_commit, GOLD_SOURCE, "gold")
    partial_patch = _commit_source(repo, base_commit, PARTIAL_SOURCE, "partial")
    regression_patch = _commit_source(repo, base_commit, REGRESSION_SOURCE, "regression")
    _git(repo, "checkout", "--quiet", "--detach", base_commit)

    private = tmp_path / "private" / "ZZCODE-BUG-001"
    private.mkdir(parents=True)
    (private / "gold.patch").write_text(gold_patch, encoding="utf-8")
    (private / "test.patch").write_text(test_patch, encoding="utf-8")
    (private / "grading.json").write_text(
        json.dumps(
            {
                "instance_id": "ZZCODE-BUG-001",
                "gold_patch": "gold.patch",
                "test_patch": "test.patch",
                "FAIL_TO_PASS": [
                    "hidden_tests/test_calc_hidden.py::test_normalize_negative",
                    "hidden_tests/test_calc_hidden.py::test_clamp_negative",
                ],
                "PASS_TO_PASS": ["tests/test_calc.py"],
            }
        ),
        encoding="utf-8",
    )
    task = TaskInstance(
        instance_id="ZZCODE-BUG-001",
        repo="local/golden",
        base_commit=base_commit,
        problem_statement="Normalize negative values and clamp negative inputs without regressions.",
        environment_id="local-pytest",
    )
    spec = PrivateTestSpec(
        instance_id=task.instance_id,
        gold_patch_path=private / "gold.patch",
        test_patch_path=private / "test.patch",
        fail_to_pass=(
            "hidden_tests/test_calc_hidden.py::test_normalize_negative",
            "hidden_tests/test_calc_hidden.py::test_clamp_negative",
        ),
        pass_to_pass=("tests/test_calc.py",),
    )
    return {
        "repo": repo,
        "task": task,
        "spec": spec,
        "gold": gold_patch,
        "partial": partial_patch,
        "regression": regression_patch,
    }


def _run_case(tmp_path, golden_repo, name, patch, test_executor=None):
    manager = WorkspaceManager(
        tmp_path / f"workspaces-{name}",
        {"local/golden": golden_repo["repo"]},
    )
    harness = LocalGradingHarness(
        manager,
        python_executable=sys.executable if test_executor is None else None,
        test_executor=test_executor,
    )
    return harness.run(
        golden_repo["task"],
        golden_repo["spec"],
        patch,
        tmp_path / f"artifacts-{name}",
        timeout_seconds=30,
    )


def test_null_patch_is_no_and_proves_base_bug_exists(tmp_path, golden_repo):
    result = _run_case(tmp_path, golden_repo, "null", None)

    assert result.validation_kind == "null"
    assert result.evaluation_result is None
    assert result.decision.resolved_status == ResolvedStatus.NO
    assert result.decision.fail_to_pass_rate == 0.0
    assert result.decision.pass_to_pass_rate == 1.0
    assert result.fail_to_pass.junit_path.is_file()
    assert result.pass_to_pass.junit_path.is_file()


def test_gold_patch_is_full(tmp_path, golden_repo):
    result = _run_case(tmp_path, golden_repo, "gold", golden_repo["gold"])

    assert result.safety.passed
    assert result.patch_apply.applied
    assert result.decision.resolved_status == ResolvedStatus.FULL
    assert result.evaluation_result.resolved_status == ResolvedStatus.FULL
    assert result.evaluation_result.fail_to_pass_rate == 1.0
    assert result.evaluation_result.pass_to_pass_rate == 1.0


def test_partial_patch_is_partial(tmp_path, golden_repo):
    result = _run_case(tmp_path, golden_repo, "partial", golden_repo["partial"])

    assert result.decision.resolved_status == ResolvedStatus.PARTIAL
    assert result.decision.fail_to_pass_rate == 0.5
    assert result.decision.pass_to_pass_rate == 1.0
    assert result.decision.failure.failure_type == FailureType.TEST_FAILURE


def test_regression_patch_is_no_even_when_f2p_passes(tmp_path, golden_repo):
    result = _run_case(tmp_path, golden_repo, "regression", golden_repo["regression"])

    assert result.decision.resolved_status == ResolvedStatus.NO
    assert result.decision.fail_to_pass_rate == 1.0
    assert result.decision.pass_to_pass_rate == 0.5


def test_conflict_patch_is_explicit_patch_apply_failure(tmp_path, golden_repo):
    conflict = golden_repo["gold"].replace("-    return value", "-    return missing", 1)
    result = _run_case(tmp_path, golden_repo, "conflict", conflict)

    assert not result.patch_apply.applied
    assert result.decision.resolved_status == ResolvedStatus.NO
    assert result.decision.failure.failure_type == FailureType.PATCH_APPLY_FAILURE
    assert result.fail_to_pass is None
    assert result.pass_to_pass is None


def test_empty_patch_is_agent_error_without_workspace_or_tests(tmp_path, golden_repo):
    result = _run_case(tmp_path, golden_repo, "empty", "  \n")

    assert result.decision.resolved_status == ResolvedStatus.AGENT_ERROR
    assert result.decision.failure.failure_type == FailureType.EMPTY_PATCH
    assert not result.evaluation_result.patch_generated
    assert not result.evaluation_result.patch_applied
    assert result.workspace is None


def test_model_patch_cannot_modify_tests(tmp_path, golden_repo):
    unsafe_patch = golden_repo["gold"].replace("calc.py", "tests/test_calc.py")
    result = _run_case(tmp_path, golden_repo, "unsafe", unsafe_patch)

    assert not result.safety.passed
    assert result.decision.resolved_status == ResolvedStatus.NO
    assert result.decision.failure.failure_type == FailureType.SAFETY_VIOLATION
    assert result.patch_apply is None


@pytest.mark.docker
@pytest.mark.skipif(
    os.environ.get("RUN_DOCKER_TESTS") != "1",
    reason="set RUN_DOCKER_TESTS=1 to run Docker golden validation",
)
def test_docker_gold_is_full_three_times_with_stable_digest_and_cleanup(tmp_path, golden_repo):
    image = os.environ.get("ZZCODE_EVAL_IMAGE", "zzcode-eval-py313:phase4")
    runner = DockerRunner(allowed_mount_roots=(tmp_path,))
    executor = DockerTestExecutor(
        runner,
        image=image,
        limits=ResourceLimits(cpus=1.0, memory_mb=512, pids_limit=64, tmpfs_mb=64),
    )
    results = [
        _run_case(tmp_path, golden_repo, f"docker-gold-{index}", golden_repo["gold"], executor)
        for index in range(3)
    ]

    assert [result.decision.resolved_status for result in results] == [ResolvedStatus.FULL] * 3
    digests = {
        run.image_digest
        for result in results
        for run in (result.fail_to_pass, result.pass_to_pass)
    }
    assert len(digests) == 1
    assert next(iter(digests)).startswith("sha256:")
    assert all(
        result.evaluation_result.metrics["image_digest"] == next(iter(digests))
        for result in results
    )
    assert all(
        not runner.exists(
            ContainerHandle(run.container_id, "removed", image, run.image_digest)
        )
        for result in results
        for run in (result.fail_to_pass, result.pass_to_pass)
    )
