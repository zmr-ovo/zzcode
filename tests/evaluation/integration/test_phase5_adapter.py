import json
import subprocess
import sys

from zzcode.evaluation import (
    AgentRunConfig,
    AgentRunStatus,
    FailureType,
    Prediction,
    TaskInstance,
    ZZCodeAgentAdapter,
)


def _task():
    return TaskInstance(
        instance_id="ZZCODE-PHASE5-001",
        repo="local/fixture",
        base_commit="1" * 40,
        problem_statement="Update value.py so VALUE equals 2.",
        environment_id="zzcode-py313",
    )


def _workspace(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    subprocess.run(["git", "init", "--quiet"], cwd=workspace, check=True)
    subprocess.run(["git", "config", "user.name", "Phase 5 Test"], cwd=workspace, check=True)
    subprocess.run(
        ["git", "config", "user.email", "phase5@example.invalid"], cwd=workspace, check=True
    )
    (workspace / "value.py").write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=workspace, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "base"], cwd=workspace, check=True)
    return workspace


def _write_worker(script, body):
    script.write_text(
        """import json, pathlib, sys, time
request_path = pathlib.Path(sys.argv[sys.argv.index('--request') + 1])
response_path = pathlib.Path(sys.argv[sys.argv.index('--response') + 1])
request = json.loads(request_path.read_text(encoding='utf-8'))
workspace = pathlib.Path(request['workspace'])
"""
        + body,
        encoding="utf-8",
    )


def _adapter_with_worker(monkeypatch, script):
    monkeypatch.setenv("OPENAI_API_KEY", "phase5-secret-must-not-leak")
    wrapper = script.parent / "python-wrapper"
    wrapper.write_text(
        f"#!/bin/sh\nexec {sys.executable} {script} \"$@\"\n", encoding="utf-8"
    )
    wrapper.chmod(0o755)
    return ZZCodeAgentAdapter(python_executable=wrapper)


def _config(timeout=10):
    return AgentRunConfig(provider="openai", model="real-provider/model", timeout_seconds=timeout)


def test_adapter_collects_patch_and_creates_swebench_prediction(tmp_path, monkeypatch):
    workspace = _workspace(tmp_path)
    script = tmp_path / "success_worker.py"
    _write_worker(
        script,
        """(workspace / 'value.py').write_text('VALUE = 2\\n', encoding='utf-8')
response_path.write_text(json.dumps({
    'kind': 'completed', 'final_answer': 'done', 'tool_steps': 2, 'attempts': 3,
    'stop_reason': 'final_answer_returned', 'runtime_status': 'completed',
    'runtime_run_id': 'run-real', 'token_usage': {'total_tokens': 42}
}), encoding='utf-8')
""",
    )
    adapter = _adapter_with_worker(monkeypatch, script)

    outcome = adapter.run(_task(), workspace, _config(), tmp_path / "artifacts")

    assert outcome.agent_result.status == AgentRunStatus.COMPLETED
    assert outcome.agent_result.patch_generated
    assert outcome.agent_result.tool_steps == 2
    assert outcome.agent_result.token_usage == {"total_tokens": 42}
    assert isinstance(outcome.prediction, Prediction)
    assert outcome.prediction.model_name_or_path == "real-provider/model"
    assert "+VALUE = 2" in outcome.prediction.model_patch
    request = json.loads((tmp_path / "artifacts" / "agent_request.json").read_text())
    assert "phase5-secret-must-not-leak" not in repr(request)


def test_adapter_classifies_empty_patch(tmp_path, monkeypatch):
    workspace = _workspace(tmp_path)
    script = tmp_path / "empty_worker.py"
    _write_worker(
        script,
        """response_path.write_text(json.dumps({
    'kind': 'completed', 'final_answer': 'no change', 'tool_steps': 0, 'attempts': 1,
    'stop_reason': 'final_answer_returned', 'runtime_status': 'completed',
    'runtime_run_id': 'run-empty', 'token_usage': {}
}), encoding='utf-8')
""",
    )
    adapter = _adapter_with_worker(monkeypatch, script)

    outcome = adapter.run(_task(), workspace, _config(), tmp_path / "artifacts")

    assert outcome.agent_result.status == AgentRunStatus.FAILED
    assert outcome.agent_result.failure.failure_type == FailureType.EMPTY_PATCH
    assert outcome.patch == ""
    assert outcome.prediction is None


def test_adapter_classifies_provider_error_and_redacts_secret(tmp_path, monkeypatch):
    workspace = _workspace(tmp_path)
    script = tmp_path / "provider_worker.py"
    _write_worker(
        script,
        """response_path.write_text(json.dumps({
    'kind': 'provider_error', 'message': 'request failed for phase5-secret-must-not-leak'
}), encoding='utf-8')
raise SystemExit(20)
""",
    )
    adapter = _adapter_with_worker(monkeypatch, script)

    outcome = adapter.run(_task(), workspace, _config(), tmp_path / "artifacts")

    assert outcome.agent_result.failure.failure_type == FailureType.PROVIDER_UNAVAILABLE
    assert "phase5-secret-must-not-leak" not in outcome.agent_result.failure.message
    assert "<redacted>" in outcome.agent_result.failure.message


def test_adapter_classifies_agent_runtime_error(tmp_path, monkeypatch):
    workspace = _workspace(tmp_path)
    script = tmp_path / "agent_error_worker.py"
    _write_worker(
        script,
        """response_path.write_text(json.dumps({
    'kind': 'agent_error', 'message': 'tool sandbox unavailable',
    'exception_type': 'ArtifactError'
}), encoding='utf-8')
raise SystemExit(21)
""",
    )
    adapter = _adapter_with_worker(monkeypatch, script)

    outcome = adapter.run(_task(), workspace, _config(), tmp_path / "artifacts")

    assert outcome.agent_result.failure.failure_type == FailureType.TOOL_FAILURE
    assert outcome.agent_result.failure.details["exception_type"] == "ArtifactError"


def test_adapter_timeout_kills_worker_process_group(tmp_path, monkeypatch):
    workspace = _workspace(tmp_path)
    script = tmp_path / "timeout_worker.py"
    marker = tmp_path / "child-survived"
    _write_worker(
        script,
        f"""import subprocess
subprocess.Popen([{sys.executable!r}, '-c', "import time, pathlib; time.sleep(1); pathlib.Path({str(marker)!r}).write_text('bad')"])
time.sleep(30)
""",
    )
    adapter = _adapter_with_worker(monkeypatch, script)

    outcome = adapter.run(_task(), workspace, _config(timeout=0.2), tmp_path / "artifacts")

    assert outcome.agent_result.status == AgentRunStatus.INTERRUPTED
    assert outcome.agent_result.failure.failure_type == FailureType.AGENT_TIMEOUT
    import time

    time.sleep(1.2)
    assert not marker.exists()


def test_adapter_missing_credentials_does_not_start_worker(tmp_path, monkeypatch):
    workspace = _workspace(tmp_path)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    adapter = ZZCodeAgentAdapter(python_executable=tmp_path / "does-not-exist")

    outcome = adapter.run(_task(), workspace, _config(), tmp_path / "artifacts")

    assert outcome.agent_result.failure.failure_type == FailureType.PROVIDER_UNAVAILABLE
    assert outcome.prediction is None
    assert not (tmp_path / "artifacts" / "agent_request.json").exists()
