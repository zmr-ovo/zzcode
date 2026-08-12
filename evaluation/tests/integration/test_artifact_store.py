import json

import pytest

from zzcode.evaluation import (
    AgentRunResult,
    AgentRunStatus,
    ArtifactError,
    ArtifactStore,
    EvaluationResult,
    EvaluationStage,
    FailureType,
    Prediction,
    ResolvedStatus,
    RunAlreadyExistsError,
    RunManifest,
    RunPaths,
    RunStatus,
    generate_run_id,
    make_failure,
)


STARTED = "2026-08-12T08:00:00+00:00"
COMPLETED = "2026-08-12T08:01:00+00:00"


def _manifest(run_id="phase2-run"):
    return RunManifest(
        run_id=run_id,
        dataset_name="zzcode-bench-v1",
        dataset_digest="sha256:" + "b" * 64,
        split="dev",
        agent_commit="abcdef1234567",
        provider="openai-compatible",
        model_name_or_path="provider/model",
        environment_id="zzcode-py313-v1",
        started_at=STARTED,
        task_count=2,
    )


def _start_running(store, manifest=None):
    paths = store.start_run(manifest or _manifest())
    store.update_manifest(paths, store.load_manifest(paths).with_status(RunStatus.RUNNING))
    return paths


def test_generate_run_id_is_unique_and_path_safe():
    run_ids = {generate_run_id() for _ in range(100)}

    assert len(run_ids) == 100
    assert all("/" not in run_id and "\\" not in run_id for run_id in run_ids)


def test_start_run_creates_contract_and_never_overwrites(tmp_path):
    store = ArtifactStore(tmp_path / "runs")
    manifest = _manifest()
    paths = store.start_run(manifest)

    assert paths.run_manifest.is_file()
    assert paths.instances.is_dir()
    assert store.load_manifest(paths) == manifest

    with pytest.raises(RunAlreadyExistsError, match="will not be overwritten"):
        store.start_run(manifest)


def test_open_run_rejects_path_escape(tmp_path):
    store = ArtifactStore(tmp_path / "runs")

    with pytest.raises(ArtifactError, match="safe directory"):
        store.open_run("../outside")


def test_instance_directory_rejects_replaced_symlink(tmp_path):
    store = ArtifactStore(tmp_path / "runs")
    paths = store.start_run(_manifest())
    paths.instances.rmdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    paths.instances.symlink_to(outside, target_is_directory=True)

    with pytest.raises(ArtifactError, match="escapes"):
        paths.instance_dir("ZZCODE-BUG-001")


def test_store_rejects_run_paths_from_another_root(tmp_path):
    store = ArtifactStore(tmp_path / "runs")
    foreign = RunPaths.from_root(tmp_path / "foreign" / "run")

    with pytest.raises(ArtifactError, match="do not belong"):
        store.load_manifest(foreign)


def test_manifest_lifecycle_allows_forward_only_transitions(tmp_path):
    store = ArtifactStore(tmp_path / "runs")
    paths = store.start_run(_manifest())

    running = store.load_manifest(paths).with_status(RunStatus.RUNNING)
    store.update_manifest(paths, running)
    completed = running.with_status(RunStatus.COMPLETED, COMPLETED)
    store.update_manifest(paths, completed)

    assert store.load_manifest(paths).status == RunStatus.COMPLETED
    with pytest.raises(ArtifactError, match="terminal run"):
        store.update_manifest(paths, completed.with_status(RunStatus.RUNNING))

    with pytest.raises(ArtifactError, match="terminal run"):
        store.update_manifest(paths, completed)


def test_agent_result_lifecycle_and_terminal_result_are_not_overwritten(tmp_path):
    store = ArtifactStore(tmp_path / "runs")
    paths = _start_running(store)
    running = AgentRunResult(
        instance_id="ZZCODE-BUG-001",
        status=AgentRunStatus.RUNNING,
        started_at=STARTED,
    )
    store.write_agent_result(paths, running)
    completed = AgentRunResult(
        instance_id="ZZCODE-BUG-001",
        status=AgentRunStatus.COMPLETED,
        started_at=STARTED,
        completed_at=COMPLETED,
        duration_seconds=60,
        patch_generated=True,
    )
    store.write_agent_result(paths, completed)

    with pytest.raises(ArtifactError, match="will not be overwritten"):
        store.write_agent_result(paths, completed)


def test_completed_artifacts_survive_later_interruption(tmp_path):
    store = ArtifactStore(tmp_path / "runs")
    paths = _start_running(store)
    prediction = Prediction("ZZCODE-BUG-001", "provider/model", "diff --git a/a b/a\n")
    store.write_patch(paths, prediction.instance_id, prediction.model_patch)
    store.append_prediction(paths, prediction)
    result = EvaluationResult(
        instance_id="ZZCODE-BUG-001",
        resolved_status=ResolvedStatus.NOT_GRADED,
        agent_completed=True,
        patch_generated=True,
        patch_applied=False,
        tests_completed=False,
        completed_at=COMPLETED,
    )
    store.write_instance_result(paths, result)
    interruption = make_failure(
        FailureType.HARNESS_INTERRUPTED,
        EvaluationStage.HARNESS,
        "operator interrupted the run",
        retryable=True,
    )
    store.write_run_failure(paths, interruption, recorded_at=COMPLETED)
    interrupted = store.load_manifest(paths).with_status(RunStatus.INTERRUPTED, COMPLETED)
    store.update_manifest(paths, interrupted)

    assert json.loads((paths.instance_dir("ZZCODE-BUG-001") / "report.json").read_text())["instance_id"] == "ZZCODE-BUG-001"
    assert store.load_predictions(paths)["ZZCODE-BUG-001"] == prediction
    assert store.load_manifest(paths).status == RunStatus.INTERRUPTED
    assert paths.run_failure.is_file()


def test_duplicate_prediction_and_instance_result_are_rejected(tmp_path):
    store = ArtifactStore(tmp_path / "runs")
    paths = _start_running(store)
    prediction = Prediction("ZZCODE-BUG-001", "model", "patch")
    store.append_prediction(paths, prediction)
    with pytest.raises(ArtifactError, match="duplicate"):
        store.append_prediction(paths, prediction)

    result = EvaluationResult(
        instance_id="ZZCODE-BUG-001",
        resolved_status=ResolvedStatus.NOT_GRADED,
        agent_completed=False,
        patch_generated=False,
        patch_applied=False,
        tests_completed=False,
    )
    store.write_instance_result(paths, result)
    with pytest.raises(ArtifactError, match="will not be overwritten"):
        store.write_instance_result(paths, result)


def test_terminal_run_rejects_all_artifact_writes(tmp_path):
    store = ArtifactStore(tmp_path / "runs")
    paths = _start_running(store)
    completed = store.load_manifest(paths).with_status(RunStatus.COMPLETED, COMPLETED)
    store.update_manifest(paths, completed)

    with pytest.raises(ArtifactError, match="only be written while RUNNING"):
        store.append_prediction(paths, Prediction("ZZCODE-BUG-001", "model", "patch"))
    with pytest.raises(ArtifactError, match="cannot be written after COMPLETED"):
        store.write_run_failure(
            paths,
            make_failure(
                FailureType.INFRASTRUCTURE_ERROR,
                EvaluationStage.HARNESS,
                "too late",
            ),
            recorded_at=COMPLETED,
        )
