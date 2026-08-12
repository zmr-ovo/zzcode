"""Static safety checks for an untrusted model patch."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath

from ..failures import make_failure
from ..status import EvaluationStage, FailureType
from ..execution.models import SafetyResult


_DIFF_HEADER = re.compile(r"^diff --git a/(.+) b/(.+)$")
_FILE_HEADER = re.compile(r"^(?:--- a/|\+\+\+ b/|rename from |rename to |copy from |copy to )(.+?)(?:\t.*)?$")


@dataclass(frozen=True)
class SafetyPolicy:
    protected_prefixes: tuple[str, ...] = (
        ".git",
        ".env",
        ".zzcode",
        "evaluation",
        "hidden_tests",
        "private",
        "tests",
    )
    max_files: int = 20
    max_changed_lines: int = 1000
    allow_binary: bool = False
    allow_symlinks: bool = False


def _unsafe_path(path: str) -> str | None:
    if not path or path == "/dev/null" or "\x00" in path:
        return "empty, null, or device path"
    if path.startswith("/") or "\\" in path:
        return "absolute or backslash path"
    parts = PurePosixPath(path).parts
    if any(part in {"", ".", ".."} for part in parts):
        return "path traversal or ambiguous segment"
    return None


def _protected(path: str, policy: SafetyPolicy) -> bool:
    return any(path == prefix or path.startswith(prefix.rstrip("/") + "/") for prefix in policy.protected_prefixes)


def inspect_patch(patch: str, policy: SafetyPolicy | None = None) -> SafetyResult:
    policy = policy or SafetyPolicy()
    violations: list[str] = []
    paths: list[str] = []
    added = 0
    deleted = 0
    if not isinstance(patch, str) or not patch.strip():
        violations.append("patch is empty")
    for line in patch.splitlines():
        match = _DIFF_HEADER.fullmatch(line)
        if match:
            old_path, new_path = match.groups()
            if old_path != new_path:
                violations.append(f"rename or path mismatch is not allowed: {old_path} -> {new_path}")
            reason = _unsafe_path(new_path)
            if reason:
                violations.append(f"unsafe path {new_path!r}: {reason}")
            elif _protected(new_path, policy):
                violations.append(f"protected path modified: {new_path}")
            paths.append(new_path)
        elif line.startswith("diff --git "):
            violations.append("quoted or malformed diff header is not allowed")
        elif (file_match := _FILE_HEADER.fullmatch(line)):
            header_path = file_match.group(1)
            reason = _unsafe_path(header_path)
            if reason:
                violations.append(f"unsafe path {header_path!r}: {reason}")
            elif _protected(header_path, policy):
                violations.append(f"protected path modified: {header_path}")
            if line.startswith(("rename from ", "rename to ", "copy from ", "copy to ")):
                violations.append("rename or copy metadata is not allowed")
        elif line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            deleted += 1
        elif line.startswith("GIT binary patch") or line.startswith("Binary files "):
            if not policy.allow_binary:
                violations.append("binary patch is not allowed")
        elif line in {"new file mode 120000", "new mode 120000"}:
            if not policy.allow_symlinks:
                violations.append("symbolic-link patch is not allowed")
    unique_paths = tuple(dict.fromkeys(paths))
    if patch.strip() and not unique_paths:
        violations.append("patch has no valid diff --git header")
    if len(unique_paths) > policy.max_files:
        violations.append(f"patch changes {len(unique_paths)} files; limit is {policy.max_files}")
    if added + deleted > policy.max_changed_lines:
        violations.append(
            f"patch changes {added + deleted} lines; limit is {policy.max_changed_lines}"
        )
    violations = list(dict.fromkeys(violations))
    failure = None
    if violations:
        failure = make_failure(
            FailureType.SAFETY_VIOLATION,
            EvaluationStage.PATCH_SAFETY,
            "; ".join(violations),
            details={
                "violation_count": len(violations),
                "touched_paths": list(unique_paths),
                "added_lines": added,
                "deleted_lines": deleted,
            },
        )
    return SafetyResult(
        passed=not violations,
        touched_paths=unique_paths,
        added_lines=added,
        deleted_lines=deleted,
        violations=tuple(violations),
        failure=failure,
    )
