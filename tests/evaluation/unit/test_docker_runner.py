import json
import subprocess

import pytest

from zzcode.evaluation import ArtifactError, DockerRunner, MountSpec, ResourceLimits


def _completed(args, returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args, returncode, stdout, stderr)


def test_resource_limits_reject_zero_or_negative_values():
    for kwargs in (
        {"cpus": 0},
        {"memory_mb": 0},
        {"pids_limit": -1},
        {"tmpfs_mb": 0},
    ):
        with pytest.raises(ValueError):
            ResourceLimits(**kwargs)


def test_create_uses_isolation_flags_and_argv_without_shell(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    artifacts = tmp_path / "artifacts"
    workspace.mkdir()
    artifacts.mkdir()
    runner = DockerRunner(allowed_mount_roots=(tmp_path,))
    calls = []

    def fake_docker(args, *, timeout):
        calls.append((args, timeout))
        if args[:2] == ["image", "inspect"]:
            return _completed(args, stdout=json.dumps({"Id": "sha256:" + "1" * 64}))
        return _completed(args, stdout="container-id\n")

    monkeypatch.setattr(runner, "_docker", fake_docker)
    handle = runner.create(
        "zzcode-eval:test",
        mounts=(
            MountSpec(workspace, "/workspace"),
            MountSpec(artifacts, "/artifacts", read_only=False),
        ),
        limits=ResourceLimits(cpus=0.5, memory_mb=512, pids_limit=64, tmpfs_mb=32),
        command=("f2p", "tests/test_value.py::test_value; echo unsafe"),
        name="zzcode-eval-test",
    )

    create = next(args for args, _timeout in calls if args[0] == "create")
    assert handle.container_id == "container-id"
    assert create[:3] == ["create", "--name", "zzcode-eval-test"]
    assert create[create.index("--network") + 1] == "none"
    assert create[create.index("--user") + 1] == "65532:65532"
    assert "--read-only" in create
    assert "--init" in create
    assert "ALL" in create
    assert "no-new-privileges:true" in create
    assert create[-3:] == [
        "sha256:" + "1" * 64,
        "f2p",
        "tests/test_value.py::test_value; echo unsafe",
    ]


def test_mount_outside_allowlist_is_rejected_before_docker(tmp_path, monkeypatch):
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()
    runner = DockerRunner(allowed_mount_roots=(allowed,))
    monkeypatch.setattr(
        runner,
        "_docker",
        lambda *args, **kwargs: pytest.fail("Docker must not run for a rejected mount"),
    )

    with pytest.raises(ArtifactError, match="outside the allowlist"):
        runner.create(
            "image:test",
            mounts=(MountSpec(outside, "/workspace"),),
            limits=ResourceLimits(),
            command=("f2p", "tests/test_x.py"),
        )


def test_mount_target_and_mode_are_fixed_by_policy(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    runner = DockerRunner(allowed_mount_roots=(tmp_path,))
    monkeypatch.setattr(
        runner,
        "_docker",
        lambda *args, **kwargs: pytest.fail("Docker must not run for a rejected mount"),
    )

    with pytest.raises(ArtifactError, match="unsafe container mount target"):
        runner.create(
            "image:test",
            mounts=(MountSpec(source, "/etc/config"),),
            limits=ResourceLimits(),
            command=("f2p", "tests/test_x.py"),
        )
    with pytest.raises(ArtifactError, match="must be read-only"):
        runner.create(
            "image:test",
            mounts=(MountSpec(source, "/workspace", read_only=False),),
            limits=ResourceLimits(),
            command=("f2p", "tests/test_x.py"),
        )


def test_non_none_network_is_rejected_before_docker(tmp_path, monkeypatch):
    runner = DockerRunner(allowed_mount_roots=(tmp_path,))
    monkeypatch.setattr(
        runner,
        "_docker",
        lambda *args, **kwargs: pytest.fail("Docker must not run for network access"),
    )

    with pytest.raises(ArtifactError, match="network=none"):
        runner.create(
            "image:test",
            mounts=(),
            limits=ResourceLimits(),
            command=("f2p", "tests/test_x.py"),
            network="bridge",
        )


def test_assert_isolated_accepts_exact_inspected_policy(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    limits = ResourceLimits(cpus=0.5, memory_mb=256, pids_limit=32, tmpfs_mb=16)
    mount = MountSpec(workspace, "/workspace")
    inspection = {
        "Config": {"User": "65532:65532"},
        "HostConfig": {
            "NetworkMode": "none",
            "ReadonlyRootfs": True,
            "Memory": 256 * 1024 * 1024,
            "MemorySwap": 256 * 1024 * 1024,
            "NanoCpus": 500_000_000,
            "PidsLimit": 32,
            "CapDrop": ["ALL"],
            "SecurityOpt": ["no-new-privileges:true"],
            "Tmpfs": {"/tmp": "rw,size=16m"},
        },
        "Mounts": [{"Source": str(workspace), "Destination": "/workspace", "RW": False}],
    }
    runner = DockerRunner(allowed_mount_roots=(tmp_path,))

    runner.assert_isolated(inspection, mounts=(mount,), limits=limits)


def test_assert_isolated_fails_closed_on_policy_drift(tmp_path):
    runner = DockerRunner(allowed_mount_roots=(tmp_path,))
    with pytest.raises(ArtifactError, match="network=none"):
        runner.assert_isolated(
            {"Config": {"User": "root"}, "HostConfig": {"NetworkMode": "bridge"}},
            mounts=(),
            limits=ResourceLimits(),
        )
