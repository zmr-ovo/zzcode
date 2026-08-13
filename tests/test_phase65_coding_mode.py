import json
import subprocess

import pytest

from zzcode.intent import TaskIntentClassifier
from zzcode.models import FakeModelClient
from zzcode.runtime import SessionStore, ZZCode
from zzcode.task_state import STOP_REASON_COMPLETION_GATE_FAILED, TaskState
from zzcode.verification import VerificationConfig, validate_selectors
from zzcode.workspace import WorkspaceContext


VERIFY_CONFIG = {
    "required_profile": "test",
    "profiles": {
        "test": {
            "kind": "test",
            "argv": ["python", "-m", "pytest", "-q", "tests"],
            "allow_selectors": True,
            "timeout": 120,
        }
    },
}


def build_agent(tmp_path, outputs, *, task_mode="coding", runner=None, max_steps=10):
    (tmp_path / "README.md").write_text("before\n", encoding="utf-8")
    workspace = WorkspaceContext.build(tmp_path)
    return ZZCode(
        FakeModelClient(outputs),
        workspace,
        SessionStore(tmp_path / ".zzcode" / "sessions"),
        approval_policy="auto",
        max_steps=max_steps,
        task_mode=task_mode,
        verification_config=VERIFY_CONFIG,
        verification_runner=runner,
    )


def passing_runner(argv, timeout):
    assert argv[-1] == "tests"
    assert timeout == 120
    return subprocess.CompletedProcess(argv, 0, "1 passed in 0.01s\n", "")


class SequencedModel:
    supports_prompt_cache = False

    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.prompts = []
        self.last_completion_metadata = {}
        self.model = "test/semantic-classifier"

    def complete(self, prompt, max_new_tokens, **kwargs):
        self.prompts.append(prompt)
        return self.outputs.pop(0)


def test_model_intent_classifier_parses_strict_json_and_retries():
    model = FakeModelClient(
        [
            "not json",
            json.dumps({"mode": "coding", "confidence": "high", "reason": "change required"}),
        ]
    )

    result = TaskIntentClassifier(model).classify("ambiguous request", "previous context")

    assert result.mode == "coding"
    assert len(model.prompts) == 2
    assert "keyword matching" in model.prompts[0]


def test_auto_mode_uses_model_semantics_and_upgrades_before_mutation(tmp_path):
    (tmp_path / "README.md").write_text("before\n", encoding="utf-8")
    model = SequencedModel(
        [
            json.dumps({"mode": "general", "confidence": "medium", "reason": "initially analytical"}),
            '<tool name="patch_file" path="README.md"><old_text>before\n</old_text><new_text>after\n</new_text></tool>',
            '<tool>{"name":"verify","args":{"profile":"test","selectors":[],"timeout":120}}</tool>',
            "<final>Done.</final>",
        ]
    )
    agent = ZZCode(
        model,
        WorkspaceContext.build(tmp_path),
        SessionStore(tmp_path / ".zzcode" / "sessions"),
        approval_policy="auto",
        task_mode="auto",
        verification_config=VERIFY_CONFIG,
        verification_runner=passing_runner,
    )

    assert agent.ask("Handle the request using the appropriate behavior") == "Done."
    assert agent.current_task_state.effective_mode == "coding"
    assert agent.current_task_state.intent["source"] == "write_tool_upgrade"
    assert "keyword matching" in model.prompts[0]


def test_auto_mode_falls_back_to_general_after_two_invalid_classifications(tmp_path):
    (tmp_path / "README.md").write_text("before\n", encoding="utf-8")
    model = SequencedModel(["invalid", "still invalid", "<final>Analysis.</final>"])
    agent = ZZCode(
        model,
        WorkspaceContext.build(tmp_path),
        SessionStore(tmp_path / ".zzcode" / "sessions"),
        approval_policy="auto",
        task_mode="auto",
        verification_config=VERIFY_CONFIG,
        verification_runner=passing_runner,
    )

    assert agent.ask("Analyze the request") == "Analysis."
    assert agent.current_task_state.effective_mode == "general"
    assert agent.current_task_state.intent["source"] == "fallback"
    events = [json.loads(line)["event"] for line in agent.run_store.trace_path(agent.current_task_state).read_text().splitlines()]
    assert "intent_classification_failed" in events


def test_general_mode_accepts_final_without_patch(tmp_path):
    agent = build_agent(tmp_path, ["<final>Analysis only.</final>"], task_mode="general")

    assert agent.ask("Explain the project") == "Analysis only."
    assert agent.current_task_state.status == "completed"


def test_coding_mode_requires_patch_and_verify(tmp_path):
    agent = build_agent(
        tmp_path,
        ["<final>Done.</final>", "<final>Done.</final>", "<final>Done.</final>"],
    )

    answer = agent.ask("Change the project")

    assert answer.startswith("Completion gate failed")
    assert agent.current_task_state.stop_reason == STOP_REASON_COMPLETION_GATE_FAILED
    assert agent.current_task_state.coding_progress.final_rejections == 3


def test_coding_mode_accepts_latest_patch_after_full_verify(tmp_path):
    agent = build_agent(
        tmp_path,
        [
            '<tool name="patch_file" path="README.md"><old_text>before\n</old_text><new_text>after\n</new_text></tool>',
            '<tool>{"name":"verify","args":{"profile":"test","selectors":[],"timeout":120}}</tool>',
            "<final>Changed and verified.</final>",
        ],
        runner=passing_runner,
    )

    assert agent.ask("Change README") == "Changed and verified."
    progress = agent.current_task_state.coding_progress
    assert progress.phase == "READY"
    assert progress.verified_patch_digest == progress.current_patch_digest
    assert progress.last_verification["tests_collected"] == 1


def test_new_mutation_invalidates_previous_verification(tmp_path):
    agent = build_agent(tmp_path, [], runner=passing_runner)
    agent.current_task_state = TaskState.create("task", "request", "run")
    agent.current_task_state.effective_mode = "coding"
    agent._task_baseline_snapshot = agent.capture_workspace_snapshot()
    agent.run_tool("patch_file", {"path": "README.md", "old_text": "before\n", "new_text": "after\n"})
    agent.current_task_state.record_tool("patch_file")
    digest, changed = agent.current_patch_state()
    progress = agent.current_task_state.coding_progress
    progress.current_patch_digest = digest
    progress.changed_paths = changed
    progress.last_mutation_step = 1
    agent.current_task_state.record_tool("verify")
    agent.run_tool("verify", {"profile": "test", "selectors": [], "timeout": 120})
    assert not agent.completion_gate_failures()

    (tmp_path / "README.md").write_text("changed again\n", encoding="utf-8")

    assert "the current patch has not been successfully verified" in agent.completion_gate_failures()


def test_run_shell_success_cannot_satisfy_gate(tmp_path):
    agent = build_agent(tmp_path, [])
    agent.current_task_state = TaskState.create("task", "request", "run")
    agent.current_task_state.effective_mode = "coding"
    agent._task_baseline_snapshot = agent.capture_workspace_snapshot()
    (tmp_path / "README.md").write_text("after\n", encoding="utf-8")

    failures = agent.completion_gate_failures()

    assert any("full verify" in failure for failure in failures)


def test_targeted_verify_cannot_satisfy_final_gate(tmp_path):
    agent = build_agent(
        tmp_path,
        [],
        runner=lambda argv, timeout: subprocess.CompletedProcess(argv, 0, "1 passed\n", ""),
    )
    agent.current_task_state = TaskState.create("task", "request", "run")
    agent.current_task_state.effective_mode = "coding"
    agent._task_baseline_snapshot = agent.capture_workspace_snapshot()
    (tmp_path / "README.md").write_text("after\n", encoding="utf-8")
    agent.current_task_state.record_tool("verify")

    agent.execute_verification(
        {"profile": "test", "selectors": ["tests/test_zzcode.py", "-k", "welcome"], "timeout": 120}
    )

    assert any("full verify" in failure for failure in agent.completion_gate_failures())


def test_verify_with_zero_collected_tests_fails(tmp_path):
    agent = build_agent(
        tmp_path,
        [],
        runner=lambda argv, timeout: subprocess.CompletedProcess(argv, 0, "no tests ran in 0.01s\n", ""),
    )
    agent.current_task_state = TaskState.create("task", "request", "run")
    agent.current_task_state.effective_mode = "coding"
    agent._task_baseline_snapshot = agent.capture_workspace_snapshot()
    (tmp_path / "README.md").write_text("after\n", encoding="utf-8")
    agent.current_task_state.record_tool("verify")

    result = json.loads(agent.execute_verification({"profile": "test", "selectors": [], "timeout": 120}))

    assert result["exit_code"] == 0
    assert result["tests_collected"] == 0
    assert "the full verification collected no tests" in agent.completion_gate_failures()


def test_verify_timeout_fails(tmp_path):
    def timeout_runner(argv, timeout):
        raise subprocess.TimeoutExpired(argv, timeout)

    agent = build_agent(tmp_path, [], runner=timeout_runner)
    agent.current_task_state = TaskState.create("task", "request", "run")
    agent.current_task_state.effective_mode = "coding"
    agent._task_baseline_snapshot = agent.capture_workspace_snapshot()
    (tmp_path / "README.md").write_text("after\n", encoding="utf-8")
    agent.current_task_state.record_tool("verify")

    result = json.loads(agent.execute_verification({"profile": "test", "selectors": [], "timeout": 120}))

    assert result["timed_out"] is True
    assert result["exit_code"] == 124
    assert "the full verification timed out" in agent.completion_gate_failures()


@pytest.mark.parametrize(
    "selectors",
    [["../private/test_hidden.py"], ["evaluation/private/test_hidden.py"], ["/tmp/test.py"], ["-x"]],
)
def test_verify_rejects_non_public_or_unsupported_selectors(selectors):
    with pytest.raises(ValueError):
        validate_selectors(selectors)


def test_verification_config_requires_declared_profile():
    with pytest.raises(ValueError):
        VerificationConfig.from_dict({"required_profile": "missing", "profiles": {"test": {"argv": ["pytest"]}}})
