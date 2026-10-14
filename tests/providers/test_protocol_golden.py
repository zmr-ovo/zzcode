"""原生工具协议的固定用例，不依赖开发脚本。"""

import json
import pytest
from pathlib import Path

from zzcode.models import FakeModelClient
from zzcode.storage.runs import RunStore
from zzcode.agent.coordinator import DEFAULT_FEATURE_FLAGS, SessionStore, ZZCode
from zzcode.context.workspace import WorkspaceContext

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def load_cases():
    return json.loads((PROJECT_ROOT / "tests/fixtures/native_golden.json").read_text())[
        "cases"
    ]


def run_case(case, root):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=False)
    for name, content in case["files"].items():
        (root / name).write_text(content, encoding="utf-8")
    model = FakeModelClient(case["outputs"])
    agent = ZZCode(
        model_client=model,
        workspace=WorkspaceContext.build(root, repo_root_override=root),
        session_store=SessionStore(root / ".zzcode/sessions"),
        run_store=RunStore(root / ".zzcode/runs"),
        approval_policy=case.get("approval_policy", "auto"),
        max_steps=case.get("max_steps", 5),
        feature_flags=DEFAULT_FEATURE_FLAGS,
    )
    answer = agent.ask(case["prompt"])
    tools = [item for item in agent.session["history"] if item["role"] == "tool"]
    state = agent.current_task_state
    actual = {
        "answer": answer,
        "stop_reason": state.stop_reason,
        "attempts": state.attempts,
        "tool_steps": state.tool_steps,
        "tools": [item["name"] for item in tools],
        "files": {name: (root / name).read_text() for name in case["files"]},
    }
    expected = case["expected"]
    for key, value in actual.items():
        if value != expected[key]:
            raise AssertionError(
                f"{case['id']}.{key}: expected {expected[key]!r}, got {value!r}"
            )
    for item, needle in zip(tools, expected["tool_contains"], strict=True):
        if needle not in item["content"]:
            raise AssertionError(f"{case['id']}: missing {needle!r} in tool result")


@pytest.mark.parametrize("case", load_cases(), ids=lambda case: case["id"])
def test_native_transcript(case, tmp_path):
    run_case(case, tmp_path / case["id"])
