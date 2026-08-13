import json
import subprocess

import pytest

from zzcode.evaluation import ArtifactError, DockerToolSandbox


def _completed(args, returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args, returncode, stdout, stderr)


def test_tool_sandbox_uses_networkless_restricted_container(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    sandbox = DockerToolSandbox(workspace, image="zzcode-tool:test")
    calls = []

    def fake_docker(args, *, timeout):
        calls.append(args)
        if args[:2] == ["image", "inspect"]:
            return _completed(args, stdout="sha256:" + "1" * 64 + "\n")
        if args[0] == "create":
            return _completed(args, stdout="container-id\n")
        if args[0] == "inspect":
            return _completed(args, stdout=json.dumps([{"State": {"OOMKilled": False}}]))
        return _completed(args)

    def fake_run(args, **kwargs):
        calls.append(args)
        return _completed(args, stdout="ok\n")

    monkeypatch.setattr(sandbox, "_docker", fake_docker)
    monkeypatch.setattr(subprocess, "run", fake_run)

    result = sandbox.run("python -m pytest -q", timeout_seconds=20)

    create = next(args for args in calls if args[0] == "create")
    assert result.stdout == "ok\n"
    assert create[create.index("--network") + 1] == "none"
    assert "--read-only" in create
    assert create[create.index("--cap-drop") + 1] == "ALL"
    assert "no-new-privileges:true" in create
    assert create[create.index("--mount") + 1].endswith("dst=/workspace")
    assert create[create.index("--entrypoint") + 1] == "/bin/sh"
    assert create[-2:] == ["-lc", "python -m pytest -q"]
    assert ["rm", "--force", "--volumes", "container-id"] in calls


def test_tool_sandbox_rejects_unresolved_image_before_container_create(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    sandbox = DockerToolSandbox(workspace, image="missing:test")
    monkeypatch.setattr(
        sandbox,
        "_docker",
        lambda args, timeout: _completed(args, returncode=1, stderr="missing"),
    )

    with pytest.raises(ArtifactError, match="immutable tool image"):
        sandbox.run("true", timeout_seconds=1)


def test_tool_sandbox_run_argv_does_not_use_shell(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    sandbox = DockerToolSandbox(workspace, image="zzcode-tool:test")
    calls = []

    def fake_docker(args, *, timeout):
        calls.append(args)
        if args[:2] == ["image", "inspect"]:
            return _completed(args, stdout="sha256:" + "1" * 64 + "\n")
        if args[0] == "create":
            return _completed(args, stdout="container-id\n")
        if args[0] == "inspect":
            return _completed(args, stdout=json.dumps([{"State": {"OOMKilled": False}}]))
        return _completed(args)

    monkeypatch.setattr(sandbox, "_docker", fake_docker)
    monkeypatch.setattr(subprocess, "run", lambda args, **kwargs: _completed(args, stdout="1 passed\n"))

    sandbox.run_argv(["python", "-m", "pytest", "-q", "tests"], 30)

    create = next(args for args in calls if args[0] == "create")
    assert create[create.index("--entrypoint") + 1] == "python"
    assert create[-4:] == ["-m", "pytest", "-q", "tests"]
