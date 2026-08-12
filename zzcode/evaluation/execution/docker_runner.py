"""Docker lifecycle with deny-by-default networking and mount policy."""

from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path, PurePosixPath
from uuid import uuid4

from ..errors import ArtifactError
from .models import CommandResult, ContainerHandle, MountSpec, ResourceLimits


_CONTAINER_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_MOUNT_TARGET_MODES = {"/workspace": True, "/artifacts": False}


class DockerRunner:
    def __init__(
        self,
        *,
        allowed_mount_roots: tuple[Path, ...],
        docker_binary: str = "docker",
        name_prefix: str = "zzcode-eval",
    ) -> None:
        if not allowed_mount_roots:
            raise ArtifactError("DockerRunner requires at least one allowed mount root")
        self.allowed_mount_roots = tuple(Path(path).resolve() for path in allowed_mount_roots)
        self.docker_binary = docker_binary
        self.name_prefix = name_prefix

    def image_digest(self, image: str) -> str:
        result = self._docker(["image", "inspect", image, "--format", "{{json .}}"], timeout=30)
        if result.returncode != 0:
            raise ArtifactError(f"cannot inspect Docker image {image}: {result.stderr.strip()}")
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise ArtifactError(f"Docker image inspect returned invalid JSON for {image}") from exc
        digest = data.get("Id")
        if not isinstance(digest, str) or not digest.startswith("sha256:"):
            raise ArtifactError(f"Docker image {image} has no immutable sha256 image id")
        return digest

    def build(
        self,
        image: str,
        context: Path,
        dockerfile: Path,
        *,
        build_network: str = "default",
    ) -> str:
        context = Path(context).resolve()
        dockerfile = Path(dockerfile).resolve()
        if not context.is_dir() or not dockerfile.is_file() or not dockerfile.is_relative_to(context):
            raise ArtifactError("Docker build context or Dockerfile is invalid")
        if build_network not in {"default", "none"}:
            raise ArtifactError("Docker build network must be 'default' or 'none'")
        result = self._docker(
            [
                "build",
                "--pull=false",
                "--network",
                build_network,
                "--file",
                str(dockerfile),
                "--tag",
                image,
                str(context),
            ],
            timeout=600,
        )
        if result.returncode != 0:
            raise ArtifactError(f"Docker image build failed: {result.stderr.strip()}")
        return self.image_digest(image)

    def create(
        self,
        image: str,
        *,
        mounts: tuple[MountSpec, ...],
        limits: ResourceLimits,
        command: tuple[str, ...],
        name: str | None = None,
        network: str = "none",
        entrypoint: str | None = None,
    ) -> ContainerHandle:
        if network != "none":
            raise ArtifactError("grading containers must use network=none")
        if not command or not all(isinstance(item, str) and item for item in command):
            raise ArtifactError("container command must be a non-empty string tuple")
        name = name or f"{self.name_prefix}-{uuid4().hex[:12]}"
        if not _CONTAINER_NAME_RE.fullmatch(name):
            raise ArtifactError("container name contains unsafe characters")
        arguments = [
            "create",
            "--name",
            name,
            "--network",
            "none",
            "--init",
            "--cpus",
            str(limits.cpus),
            "--memory",
            f"{limits.memory_mb}m",
            "--memory-swap",
            f"{limits.memory_mb}m",
            "--pids-limit",
            str(limits.pids_limit),
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--user",
            "65532:65532",
            "--tmpfs",
            f"/tmp:rw,noexec,nosuid,nodev,size={limits.tmpfs_mb}m,mode=1777",
            "--workdir",
            "/workspace",
            "--env",
            "HOME=/tmp/home",
            "--env",
            "PYTHONDONTWRITEBYTECODE=1",
            "--env",
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD=1",
            "--env",
            "PYTHONPATH=/workspace",
        ]
        targets: set[str] = set()
        for mount in mounts:
            source, target = self._validate_mount(mount)
            if target in targets:
                raise ArtifactError(f"duplicate container mount target: {target}")
            targets.add(target)
            mount_value = f"type=bind,src={source},dst={target}"
            if mount.read_only:
                mount_value += ",readonly"
            arguments.extend(["--mount", mount_value])
        image_digest = self.image_digest(image)
        if entrypoint is not None:
            if not entrypoint.startswith("/") or any(char in entrypoint for char in "\n\r\x00"):
                raise ArtifactError("container entrypoint must be an absolute safe path")
            arguments.extend(["--entrypoint", entrypoint])
        # Create from the inspected immutable image ID, so a concurrent tag
        # change cannot make the recorded digest differ from the executed image.
        arguments.extend([image_digest, *command])
        result = self._docker(arguments, timeout=60)
        if result.returncode != 0:
            raise ArtifactError(f"Docker container create failed: {result.stderr.strip()}")
        container_id = result.stdout.strip()
        if not container_id:
            raise ArtifactError("Docker create returned no container id")
        return ContainerHandle(container_id, name, image, image_digest)

    def start_and_wait(self, handle: ContainerHandle, timeout_seconds: float) -> CommandResult:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        started = time.monotonic()
        try:
            result = subprocess.run(
                [self.docker_binary, "start", "--attach", handle.container_id],
                text=True,
                capture_output=True,
                timeout=timeout_seconds,
                check=False,
            )
            return CommandResult(
                command=(self.docker_binary, "start", "--attach", handle.container_id),
                returncode=result.returncode,
                stdout=result.stdout,
                stderr=result.stderr,
                duration_seconds=time.monotonic() - started,
                timed_out=False,
                container_id=handle.container_id,
            )
        except subprocess.TimeoutExpired as exc:
            self.cleanup(handle)
            return CommandResult(
                command=(self.docker_binary, "start", "--attach", handle.container_id),
                returncode=None,
                stdout=exc.stdout or "",
                stderr=exc.stderr or "",
                duration_seconds=time.monotonic() - started,
                timed_out=True,
                container_id=handle.container_id,
            )
        except OSError as exc:
            self.cleanup(handle)
            raise ArtifactError(f"Docker container could not start: {exc}") from exc

    def inspect(self, handle: ContainerHandle) -> dict:
        result = self._docker(["inspect", handle.container_id], timeout=30)
        if result.returncode != 0:
            raise ArtifactError(f"cannot inspect container {handle.container_id}: {result.stderr.strip()}")
        try:
            rows = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise ArtifactError("Docker container inspect returned invalid JSON") from exc
        if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
            raise ArtifactError("Docker container inspect returned unexpected data")
        return rows[0]

    def assert_isolated(
        self,
        inspection: dict,
        *,
        mounts: tuple[MountSpec, ...],
        limits: ResourceLimits,
    ) -> None:
        """Fail closed if Docker did not retain the requested grading policy."""
        host = inspection.get("HostConfig")
        config = inspection.get("Config")
        if not isinstance(host, dict) or not isinstance(config, dict):
            raise ArtifactError("Docker inspect data has no HostConfig/Config")
        expected = {
            "network=none": host.get("NetworkMode") == "none",
            "read-only rootfs": host.get("ReadonlyRootfs") is True,
            "memory limit": host.get("Memory") == limits.memory_mb * 1024 * 1024,
            "memory swap disabled": host.get("MemorySwap") == limits.memory_mb * 1024 * 1024,
            "CPU limit": host.get("NanoCpus") == int(limits.cpus * 1_000_000_000),
            "PID limit": host.get("PidsLimit") == limits.pids_limit,
            "all capabilities dropped": "ALL" in (host.get("CapDrop") or []),
            "no-new-privileges": any(
                str(item).startswith("no-new-privileges")
                for item in (host.get("SecurityOpt") or [])
            ),
            "non-root user": config.get("User") not in {None, "", "0", "0:0", "root"},
            "limited /tmp tmpfs": "/tmp" in (host.get("Tmpfs") or {}),
        }
        observed_mounts = {
            row.get("Destination"): row
            for row in inspection.get("Mounts", [])
            if isinstance(row, dict)
        }
        for mount in mounts:
            row = observed_mounts.get(str(PurePosixPath(mount.target)))
            expected[f"mount {mount.target}"] = bool(
                row
                and Path(str(row.get("Source", ""))).resolve() == Path(mount.source).resolve()
                and row.get("RW") is (not mount.read_only)
            )
        violations = [name for name, valid in expected.items() if not valid]
        if violations:
            raise ArtifactError("container isolation policy mismatch: " + ", ".join(violations))

    def cleanup(self, handle: ContainerHandle) -> None:
        result = self._docker(["rm", "--force", "--volumes", handle.container_id], timeout=30)
        if result.returncode != 0 and "No such container" not in result.stderr:
            raise ArtifactError(
                f"failed to remove container {handle.container_id}: {result.stderr.strip()}"
            )

    def exists(self, handle: ContainerHandle) -> bool:
        result = self._docker(["inspect", handle.container_id], timeout=30)
        return result.returncode == 0

    def _validate_mount(self, mount: MountSpec) -> tuple[Path, str]:
        source = Path(mount.source).resolve()
        if not source.exists():
            raise ArtifactError(f"mount source does not exist: {source}")
        if not any(source == root or source.is_relative_to(root) for root in self.allowed_mount_roots):
            raise ArtifactError(f"mount source is outside the allowlist: {source}")
        target = mount.target
        if not isinstance(target, str) or not target.startswith("/") or "\\" in target:
            raise ArtifactError(f"mount target must be an absolute POSIX path: {target!r}")
        pure = PurePosixPath(target)
        normalized_target = str(pure)
        if ".." in pure.parts or normalized_target not in _MOUNT_TARGET_MODES:
            raise ArtifactError(f"unsafe container mount target: {target}")
        required_read_only = _MOUNT_TARGET_MODES[normalized_target]
        if mount.read_only is not required_read_only:
            required = "read-only" if required_read_only else "writable"
            raise ArtifactError(f"container mount {normalized_target} must be {required}")
        return source, normalized_target

    def _docker(self, args: list[str], *, timeout: float) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                [self.docker_binary, *args],
                text=True,
                capture_output=True,
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ArtifactError(f"Docker command failed: {exc}") from exc
