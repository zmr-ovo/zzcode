"""Collect a complete SWE-bench-style Git patch from an inference workspace."""

from __future__ import annotations

import subprocess
from pathlib import Path

from ..errors import ArtifactError


DEFAULT_MAX_PATCH_BYTES = 2 * 1024 * 1024


def _git(workspace: Path, args: list[str], *, text: bool = True):
    try:
        return subprocess.run(
            ["git", *args],
            cwd=workspace,
            text=text,
            capture_output=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ArtifactError(f"patch collection Git command failed: {exc}") from exc


def collect_patch(workspace: Path, *, max_bytes: int = DEFAULT_MAX_PATCH_BYTES) -> str:
    """Include staged, unstaged, and untracked files while excluding runtime artifacts."""
    workspace = Path(workspace).resolve()
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
        raise ValueError("max_bytes must be a positive integer")
    inside = _git(workspace, ["rev-parse", "--is-inside-work-tree"])
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        raise ArtifactError(f"inference workspace is not a Git worktree: {workspace}")

    untracked = _git(workspace, ["ls-files", "--others", "--exclude-standard", "-z"], text=False)
    if untracked.returncode != 0:
        stderr = untracked.stderr.decode("utf-8", errors="replace")
        raise ArtifactError(f"cannot enumerate untracked inference files: {stderr.strip()}")
    paths = [
        raw.decode("utf-8", errors="surrogateescape")
        for raw in untracked.stdout.split(b"\x00")
        if raw and raw != b".zzcode" and not raw.startswith(b".zzcode/")
    ]
    if paths:
        intent = _git(workspace, ["add", "--intent-to-add", "--", *paths])
        if intent.returncode != 0:
            raise ArtifactError(f"cannot include untracked files in patch: {intent.stderr.strip()}")

    diff = _git(
        workspace,
        [
            "diff",
            "HEAD",
            "--binary",
            "--no-ext-diff",
            "--",
            ".",
            ":(exclude).zzcode",
            ":(exclude).zzcode/**",
        ],
    )
    if diff.returncode != 0:
        raise ArtifactError(f"cannot collect model patch: {diff.stderr.strip()}")
    encoded = diff.stdout.encode("utf-8")
    if len(encoded) > max_bytes:
        raise ArtifactError(f"model patch exceeds {max_bytes} bytes")
    return diff.stdout
