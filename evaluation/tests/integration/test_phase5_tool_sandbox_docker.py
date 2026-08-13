import os

import pytest

from zzcode.evaluation import DockerToolSandbox


pytestmark = [
    pytest.mark.docker,
    pytest.mark.skipif(
        os.environ.get("RUN_DOCKER_TESTS") != "1",
        reason="set RUN_DOCKER_TESTS=1 to run Docker tool-plane validation",
    ),
]


def test_agent_tool_container_can_edit_workspace_but_has_no_network(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = workspace / "value.py"
    source.write_text("VALUE = 1\n", encoding="utf-8")
    sandbox = DockerToolSandbox(
        workspace,
        image=os.environ.get("ZZCODE_EVAL_IMAGE", "zzcode-eval-py313:phase4"),
    )

    edit = sandbox.run(
        "python -c \"from pathlib import Path; Path('value.py').write_text('VALUE = 2\\n')\"",
        timeout_seconds=10,
    )
    network = sandbox.run(
        "python -c \"import socket; s=socket.socket(); s.settimeout(.5); s.connect(('1.1.1.1',53))\"",
        timeout_seconds=10,
    )

    assert edit.returncode == 0
    assert source.read_text(encoding="utf-8") == "VALUE = 2\n"
    assert network.returncode != 0


def test_agent_verify_argv_runs_without_shell(tmp_path):
    workspace = tmp_path / "workspace"
    tests = workspace / "tests"
    tests.mkdir(parents=True)
    (tests / "test_public.py").write_text("def test_public():\n    assert True\n", encoding="utf-8")
    sandbox = DockerToolSandbox(
        workspace,
        image=os.environ.get("ZZCODE_EVAL_IMAGE", "zzcode-eval-py313:phase4"),
    )

    result = sandbox.run_argv(
        ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests"],
        30,
    )

    assert result.returncode == 0
    assert "1 passed" in result.stdout
