"""Execute Agent shell commands in short-lived, networkless Docker containers."""

from __future__ import annotations

import os
import re
import subprocess
import textwrap
from pathlib import Path
from uuid import uuid4

from ..errors import ArtifactError


_IMAGE_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


class DockerToolSandbox:
    """Tool-plane isolation; deliberately separate from the read-only grading policy."""

    def __init__(
        self,
        workspace: Path,
        *,
        image: str,
        docker_binary: str = "docker",
        cpus: float = 1.0,
        memory_mb: int = 1024,
        pids_limit: int = 128,
        tmpfs_mb: int = 256,
    ) -> None:
        self.workspace = Path(workspace).resolve()
        if not self.workspace.is_dir():
            raise ArtifactError(f"tool workspace is not a directory: {self.workspace}")
        if not image.strip():
            raise ValueError("tool image must not be empty")
        self.image = image
        self.docker_binary = docker_binary
        self.cpus = cpus
        self.memory_mb = memory_mb
        self.pids_limit = pids_limit
        self.tmpfs_mb = tmpfs_mb

    def run_args(self, args: dict) -> str:
        command = str(args.get("command", "")).strip()
        if not command:
            raise ValueError("command must not be empty")
        timeout = int(args.get("timeout", 20))
        if timeout < 1 or timeout > 120:
            raise ValueError("timeout must be in [1, 120]")
        result = self.run(command, timeout_seconds=timeout)
        return textwrap.dedent(
            f"""\
            exit_code: {result.returncode}
            stdout:
            {result.stdout.strip() or "(empty)"}
            stderr:
            {result.stderr.strip() or "(empty)"}
            """
        ).strip()

    def run(self, command: str, *, timeout_seconds: float) -> subprocess.CompletedProcess[str]:
        return self._run_container("/bin/sh", ["-lc", command], timeout_seconds=timeout_seconds)

    def run_argv(self, argv: list[str], timeout_seconds: int) -> subprocess.CompletedProcess[str]:
        if not argv or not all(isinstance(item, str) and item for item in argv):
            raise ValueError("verification argv must be a non-empty list of strings")
        return self._run_container(argv[0], argv[1:], timeout_seconds=timeout_seconds)

    def _run_container(
        self, entrypoint: str, arguments: list[str], *, timeout_seconds: float
    ) -> subprocess.CompletedProcess[str]:
        image_id = self._image_id()
        name = f"zzcode-agent-tool-{uuid4().hex[:12]}"
        uid = os.getuid() if hasattr(os, "getuid") else 65532
        gid = os.getgid() if hasattr(os, "getgid") else 65532
        create = self._docker(
            [
                "create",
                "--name",
                name,
                "--network",
                "none",
                "--init",
                "--cpus",
                str(self.cpus),
                "--memory",
                f"{self.memory_mb}m",
                "--memory-swap",
                f"{self.memory_mb}m",
                "--pids-limit",
                str(self.pids_limit),
                "--read-only",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges:true",
                "--user",
                f"{uid}:{gid}",
                "--tmpfs",
                f"/tmp:rw,noexec,nosuid,nodev,size={self.tmpfs_mb}m,mode=1777",
                "--workdir",
                "/workspace",
                "--env",
                "HOME=/tmp/home",
                "--env",
                "PYTHONDONTWRITEBYTECODE=1",
                "--env",
                "PYTEST_DISABLE_PLUGIN_AUTOLOAD=1",
                "--mount",
                f"type=bind,src={self.workspace},dst=/workspace",
                "--entrypoint",
                entrypoint,
                image_id,
                *arguments,
            ],
            timeout=60,
        )
        if create.returncode != 0 or not create.stdout.strip():
            raise ArtifactError(f"Agent tool container create failed: {create.stderr.strip()}")
        container_id = create.stdout.strip()
        try:
            result = subprocess.run(
                [self.docker_binary, "start", "--attach", container_id],
                text=True,
                capture_output=True,
                timeout=timeout_seconds,
                check=False,
            )
            inspection = self._docker(["inspect", container_id], timeout=30)
            if inspection.returncode != 0:
                raise ArtifactError(
                    f"cannot inspect completed Agent tool container: {inspection.stderr.strip()}"
                )
            import json

            rows = json.loads(inspection.stdout)
            if not isinstance(rows, list) or len(rows) != 1:
                raise ArtifactError("Docker returned invalid tool container inspection data")
            state = rows[0].get("State") or {}
            if state.get("OOMKilled") is True:
                raise ArtifactError("Agent tool container exceeded its memory limit")
            return result
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError(f"Agent shell command exceeded {timeout_seconds} seconds") from exc
        except OSError as exc:
            raise ArtifactError(f"Agent tool container could not start: {exc}") from exc
        finally:
            self._docker(["rm", "--force", "--volumes", container_id], timeout=30)

    def _image_id(self) -> str:
        inspect = self._docker(
            ["image", "inspect", self.image, "--format", "{{.Id}}"],
            timeout=30,
        )
        image_id = inspect.stdout.strip()
        if inspect.returncode != 0 or not _IMAGE_ID_RE.fullmatch(image_id):
            raise ArtifactError(f"cannot resolve immutable tool image ID: {inspect.stderr.strip()}")
        return image_id

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
            raise ArtifactError(f"Docker tool command failed: {exc}") from exc
