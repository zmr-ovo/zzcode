"""Execute fixed legacy transcripts against the unchanged production loop."""

import json
from pathlib import Path

from zzcode.models import FakeModelClient
from zzcode.run_store import RunStore
from zzcode.runtime import DEFAULT_FEATURE_FLAGS, SessionStore, ZZCode
from zzcode.workspace import WorkspaceContext

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def load_cases():
    return json.loads((PROJECT_ROOT / "tests/fixtures/legacy_golden.json").read_text())["cases"]


def load_profile(name="legacy"):
    config = json.loads((PROJECT_ROOT / "evaluation/configs/migration-profiles.json").read_text())
    profile = config["profiles"].get(name)
    if not profile or not profile["implemented"]:
        raise ValueError(f"migration profile is not implemented: {name}")
    if profile["features"] != DEFAULT_FEATURE_FLAGS:
        raise ValueError("legacy feature profile drifted from the P0 contract")
    return profile["features"]


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
        feature_flags=load_profile(),
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
            raise AssertionError(f"{case['id']}.{key}: expected {expected[key]!r}, got {value!r}")
    for item, needle in zip(tools, expected["tool_contains"], strict=True):
        if needle not in item["content"]:
            raise AssertionError(f"{case['id']}: missing {needle!r} in tool result")
    return {
        "id": case["id"],
        "passed": True,
        "actual": actual,
        "run_dir": str(Path(agent.current_run_dir).relative_to(root)),
        "transcript": {"prompt": case["prompt"], "outputs": case["outputs"]},
        "tool_results": [item["content"] for item in tools],
    }
