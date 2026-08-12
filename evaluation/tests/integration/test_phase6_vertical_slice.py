import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from zzcode.evaluation import (
    AgentRunConfig,
    AgentRunResult,
    AgentRunStatus,
    EvaluationDataset,
    InferenceOutcome,
    Prediction,
    ResourceLimits,
    ResolvedStatus,
    TestExecutor,
    VerticalSliceRunner,
)


def now():
    return datetime.now(timezone.utc).isoformat()


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        text=True,
        capture_output=True,
        check=True,
    )
    return result.stdout.strip()


@pytest.fixture
def golden_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "--quiet")
    git(repo, "config", "user.name", "Evaluation Test")
    git(repo, "config", "user.email", "evaluation@example.invalid")
    (repo / "calc.py").write_text("def normalize(value):\n    return value\n", encoding="utf-8")
    tests = repo / "tests"
    tests.mkdir()
    (tests / "test_calc.py").write_text(
        "from calc import normalize\n\n\ndef test_positive():\n    assert normalize(3) == 3\n",
        encoding="utf-8",
    )
    git(repo, "add", ".")
    git(repo, "commit", "--quiet", "-m", "base")
    base_commit = git(repo, "rev-parse", "HEAD")

    hidden = repo / "hidden_tests"
    hidden.mkdir()
    (hidden / "test_hidden.py").write_text(
        "from calc import normalize\n\n\ndef test_negative():\n    assert normalize(-3) == 3\n",
        encoding="utf-8",
    )
    git(repo, "add", "hidden_tests/test_hidden.py")
    git(repo, "commit", "--quiet", "-m", "private-test")
    test_patch = git(repo, "diff", "--binary", "--no-ext-diff", base_commit, "HEAD") + "\n"

    git(repo, "checkout", "--quiet", "--detach", base_commit)
    (repo / "calc.py").write_text("def normalize(value):\n    return abs(value)\n", encoding="utf-8")
    git(repo, "add", "calc.py")
    git(repo, "commit", "--quiet", "-m", "gold")
    gold_patch = git(repo, "diff", "--binary", "--no-ext-diff", base_commit, "HEAD") + "\n"
    git(repo, "checkout", "--quiet", "--detach", base_commit)

    private = tmp_path / "private" / "phase6-fixture" / "ZZCODE-BUG-001"
    private.mkdir(parents=True)
    (private / "gold.patch").write_text(gold_patch, encoding="utf-8")
    (private / "test.patch").write_text(test_patch, encoding="utf-8")
    (private / "grading.json").write_text(
        json.dumps(
            {
                "instance_id": "ZZCODE-BUG-001",
                "gold_patch": "gold.patch",
                "test_patch": "test.patch",
                "FAIL_TO_PASS": ["hidden_tests/test_hidden.py::test_negative"],
                "PASS_TO_PASS": ["tests/test_calc.py::test_positive"],
            }
        ),
        encoding="utf-8",
    )
    # Keep the source repository clean so its HEAD is a reproducible Agent
    # commit. Private grading data lives outside it.
    from zzcode.evaluation import TaskInstance

    task = TaskInstance(
        instance_id="ZZCODE-BUG-001",
        repo="local/golden",
        base_commit=base_commit,
        problem_statement="Normalize negative numbers.",
        environment_id="local-pytest",
    )
    return {"repo": repo, "task": task, "gold": gold_patch}


class GoldPatchAdapter:
    """Harness self-test adapter; formal runs use ZZCodeAgentAdapter."""

    def __init__(self, patch):
        self.patch = patch

    def run(self, task, workspace, config, artifact_dir):
        del artifact_dir
        timestamp = now()
        result = AgentRunResult(
            instance_id=task.instance_id,
            status=AgentRunStatus.COMPLETED,
            started_at=timestamp,
            completed_at=timestamp,
            patch_generated=True,
            tool_steps=1,
            metadata={"provider": config.provider, "model": config.model},
        )
        prediction = Prediction(task.instance_id, config.model, self.patch)
        return InferenceOutcome(task, workspace, result, self.patch, prediction)


class CrashingAdapter:
    def run(self, task, workspace, config, artifact_dir):
        del task, workspace, config, artifact_dir
        raise RuntimeError("adapter exploded with private diagnostics")


def _dataset(tmp_path, golden_repo):
    task = golden_repo["task"]
    public = tmp_path / "public" / "phase6-fixture"
    problem = public / "instances" / task.instance_id / "problem_statement.md"
    problem.parent.mkdir(parents=True)
    problem.write_text(task.problem_statement + "\n", encoding="utf-8")
    manifest = {
        "instance_id": task.instance_id,
        "repo": task.repo,
        "base_commit": task.base_commit,
        "problem_statement_path": f"instances/{task.instance_id}/problem_statement.md",
        "environment_id": task.environment_id,
    }
    (public / "manifest.jsonl").write_text(json.dumps(manifest) + "\n", encoding="utf-8")
    splits = public / "splits"
    splits.mkdir()
    (splits / "dev.txt").write_text(task.instance_id + "\n", encoding="utf-8")
    return EvaluationDataset.load(public, tmp_path / "private", "dev")


def test_vertical_slice_runs_validations_agent_grading_and_reports(tmp_path, golden_repo):
    dataset = _dataset(tmp_path, golden_repo)
    runner = VerticalSliceRunner(
        dataset=dataset,
        repositories={golden_repo["task"].repo: golden_repo["repo"]},
        workspace_root=tmp_path / "workspaces",
        artifact_root=tmp_path / "runs",
        test_executor=TestExecutor(),
        adapter=GoldPatchAdapter(golden_repo["gold"]),
        environment_image="local-test-executor",
        image_digest=None,
        resource_limits=ResourceLimits(),
        gold_repetitions=3,
        test_timeout_seconds=30,
    )

    paths = runner.run(
        AgentRunConfig(provider="openai", model="test/model"),
        run_id="phase6-test-run",
    )

    manifest = json.loads(paths.run_manifest.read_text(encoding="utf-8"))
    results = json.loads(paths.results.read_text(encoding="utf-8"))
    instance = paths.instance_dir(golden_repo["task"].instance_id)
    gate = json.loads((instance / "validation" / "gate.json").read_text(encoding="utf-8"))

    assert manifest["status"] == "COMPLETED"
    assert gate["passed"] is True
    assert gate["null"]["resolved_status"] == ResolvedStatus.NO.value
    assert [row["resolved_status"] for row in gate["gold"]] == [
        ResolvedStatus.FULL.value
    ] * 3
    assert results["summary"]["resolved"] == 1
    assert results["summary"]["resolution_rate"] == 1.0
    assert (instance / "agent_result.json").is_file()
    assert (instance / "patch.diff").is_file()
    assert (instance / "grading" / "f2p.result.json").is_file()
    assert (instance / "report.json").is_file()
    assert paths.predictions.read_text(encoding="utf-8").count("\n") == 1


def test_vertical_slice_persists_artifacts_when_adapter_raises(tmp_path, golden_repo):
    dataset = _dataset(tmp_path, golden_repo)
    runner = VerticalSliceRunner(
        dataset=dataset,
        repositories={golden_repo["task"].repo: golden_repo["repo"]},
        workspace_root=tmp_path / "workspaces",
        artifact_root=tmp_path / "runs",
        test_executor=TestExecutor(),
        adapter=CrashingAdapter(),
        environment_image="local-test-executor",
        image_digest=None,
        resource_limits=ResourceLimits(),
        gold_repetitions=1,
        test_timeout_seconds=30,
    )

    paths = runner.run(
        AgentRunConfig(provider="openai", model="test/model"),
        run_id="phase6-adapter-error",
    )

    instance = paths.instance_dir(golden_repo["task"].instance_id)
    manifest = json.loads(paths.run_manifest.read_text(encoding="utf-8"))
    result = json.loads((instance / "report.json").read_text(encoding="utf-8"))
    agent = json.loads((instance / "agent_result.json").read_text(encoding="utf-8"))

    assert manifest["status"] == "COMPLETED"
    assert agent["status"] == AgentRunStatus.FAILED.value
    assert result["resolved_status"] == ResolvedStatus.INFRA_ERROR.value
    assert "private diagnostics" not in json.dumps(agent)
