import os
from pathlib import Path

import pytest

from zzcode.evaluation import DockerRunner, MountSpec, ResourceLimits


pytestmark = [
    pytest.mark.docker,
    pytest.mark.skipif(
        os.environ.get("RUN_DOCKER_TESTS") != "1",
        reason="set RUN_DOCKER_TESTS=1 to run Docker runtime validation",
    ),
]


def _runner_and_image(tmp_path):
    return (
        DockerRunner(allowed_mount_roots=(tmp_path,)),
        os.environ.get("ZZCODE_EVAL_IMAGE", "zzcode-eval-py313:phase4"),
    )


def test_container_has_no_network_and_enforced_resources(tmp_path):
    runner, image = _runner_and_image(tmp_path)
    limits = ResourceLimits(cpus=0.5, memory_mb=256, pids_limit=32, tmpfs_mb=16)
    code = (
        "import socket; "
        "s=socket.socket(); s.settimeout(0.5); "
        "\ntry: s.connect(('1.1.1.1', 53))\n"
        "except OSError: print('network-blocked')\n"
        "else: raise SystemExit('network unexpectedly available')"
    )
    handle = runner.create(
        image,
        mounts=(),
        limits=limits,
        command=("-c", code),
        entrypoint="/usr/local/bin/python",
    )
    try:
        runner.assert_isolated(runner.inspect(handle), mounts=(), limits=limits)
        result = runner.start_and_wait(handle, 10)
        assert result.returncode == 0
        assert result.stdout.strip() == "network-blocked"
    finally:
        if runner.exists(handle):
            runner.cleanup(handle)
    assert not runner.exists(handle)


def test_timeout_force_removes_container(tmp_path):
    runner, image = _runner_and_image(tmp_path)
    handle = runner.create(
        image,
        mounts=(),
        limits=ResourceLimits(cpus=0.25, memory_mb=128, pids_limit=16, tmpfs_mb=8),
        command=("-c", "import time; time.sleep(30)"),
        entrypoint="/usr/local/bin/python",
    )

    result = runner.start_and_wait(handle, 0.2)

    assert result.timed_out
    assert result.returncode is None
    assert not runner.exists(handle)


def test_workspace_and_rootfs_are_read_only_but_artifacts_are_writable(tmp_path):
    runner, image = _runner_and_image(tmp_path)
    workspace = tmp_path / "workspace"
    artifacts = tmp_path / "artifacts"
    workspace.mkdir()
    artifacts.mkdir()
    artifacts.chmod(0o777)
    (workspace / "source.py").write_text("VALUE = 1\n", encoding="utf-8")
    code = """from pathlib import Path
blocked = []
for path in (Path('/workspace/source.py'), Path('/opt/rootfs-probe')):
    try:
        path.write_text('changed', encoding='utf-8')
    except OSError:
        blocked.append(str(path))
Path('/artifacts/probe.txt').write_text('artifact-write-ok', encoding='utf-8')
if len(blocked) != 2:
    raise SystemExit(f'write unexpectedly allowed: {blocked}')
print('filesystem-policy-ok')
"""
    mounts = (
        MountSpec(workspace, "/workspace", read_only=True),
        MountSpec(artifacts, "/artifacts", read_only=False),
    )
    limits = ResourceLimits(cpus=0.25, memory_mb=128, pids_limit=16, tmpfs_mb=8)
    handle = runner.create(
        image,
        mounts=mounts,
        limits=limits,
        command=("-c", code),
        entrypoint="/usr/local/bin/python",
    )
    try:
        runner.assert_isolated(runner.inspect(handle), mounts=mounts, limits=limits)
        result = runner.start_and_wait(handle, 10)
        assert result.returncode == 0
        assert result.stdout.strip() == "filesystem-policy-ok"
    finally:
        if runner.exists(handle):
            runner.cleanup(handle)

    assert (workspace / "source.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    assert Path(artifacts / "probe.txt").read_text(encoding="utf-8") == "artifact-write-ok"
    assert not runner.exists(handle)
