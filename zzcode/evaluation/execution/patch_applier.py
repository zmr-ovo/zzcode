"""Strictly check and apply unified Git patches without fuzz repair."""

from __future__ import annotations

import subprocess
from pathlib import Path

from ..failures import make_failure
from ..status import EvaluationStage, FailureType
from .models import PatchApplyResult


class PatchApplier:
    def check(self, workspace: Path, patch: str) -> PatchApplyResult:
        return self._run(workspace, patch, apply=False)

    def apply(self, workspace: Path, patch: str) -> PatchApplyResult:
        checked = self.check(workspace, patch)
        if not checked.checked:
            return checked
        return self._run(workspace, patch, apply=True)

    def _run(self, workspace: Path, patch: str, *, apply: bool) -> PatchApplyResult:
        if not isinstance(patch, str) or not patch.strip():
            failure = make_failure(
                FailureType.EMPTY_PATCH,
                EvaluationStage.PATCH_COLLECTION,
                "patch is empty",
            )
            return PatchApplyResult(False, False, 1, failure=failure)
        args = ["git", "apply", "--binary", "--whitespace=error-all"]
        if not apply:
            args.append("--check")
        try:
            result = subprocess.run(
                args,
                cwd=Path(workspace),
                input=patch,
                text=True,
                capture_output=True,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            failure = make_failure(
                FailureType.INFRASTRUCTURE_ERROR,
                EvaluationStage.PATCH_APPLY,
                f"git apply could not execute: {exc}",
                retryable=True,
            )
            return PatchApplyResult(False, False, 1, stderr=str(exc), failure=failure)
        if result.returncode != 0:
            failure = make_failure(
                FailureType.PATCH_APPLY_FAILURE,
                EvaluationStage.PATCH_APPLY,
                result.stderr.strip() or "git apply rejected the patch",
                details={"returncode": result.returncode},
            )
            return PatchApplyResult(
                checked=False if not apply else True,
                applied=False,
                returncode=result.returncode,
                stdout=result.stdout,
                stderr=result.stderr,
                failure=failure,
            )
        return PatchApplyResult(
            checked=True,
            applied=apply,
            returncode=0,
            stdout=result.stdout,
            stderr=result.stderr,
        )
