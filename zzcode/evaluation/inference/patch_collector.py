"""Collect a complete SWE-bench-style Git patch from an inference workspace."""

from __future__ import annotations

from pathlib import Path

from ...git_patch import DEFAULT_MAX_PATCH_BYTES, GitPatchError, collect_worktree_patch
from ..errors import ArtifactError


def collect_patch(workspace: Path, *, max_bytes: int = DEFAULT_MAX_PATCH_BYTES) -> str:
    """Include staged, unstaged, and untracked files while excluding runtime artifacts."""
    try:
        return collect_worktree_patch(Path(workspace), max_bytes=max_bytes)
    except GitPatchError as exc:
        raise ArtifactError(str(exc)) from exc
