"""Repository-level verification profiles used by Coding mode."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Callable


_FORBIDDEN_SELECTOR_CHARS = set("|;&><`$\n\r\x00")
_PYTEST_SUMMARY = re.compile(r"(?P<count>\d+)\s+(?P<kind>passed|failed|error|errors|skipped|xfailed|xpassed)")


@dataclass(frozen=True)
class VerificationProfile:
    name: str
    kind: str
    argv: tuple[str, ...]
    allow_selectors: bool = False
    timeout: int = 120


@dataclass(frozen=True)
class VerificationConfig:
    required_profile: str
    profiles: dict[str, VerificationProfile]

    @classmethod
    def from_dict(cls, value: dict) -> "VerificationConfig":
        if not isinstance(value, dict):
            raise ValueError("verification config must be an object")
        required = str(value.get("required_profile", "")).strip()
        rows = value.get("profiles")
        if not required or not isinstance(rows, dict) or not rows:
            raise ValueError("verification config requires required_profile and profiles")
        profiles: dict[str, VerificationProfile] = {}
        for name, row in rows.items():
            if not isinstance(row, dict):
                raise ValueError(f"verification profile {name!r} must be an object")
            argv = row.get("argv")
            if not isinstance(argv, list) or not argv or not all(isinstance(item, str) and item for item in argv):
                raise ValueError(f"verification profile {name!r} requires a non-empty argv list")
            timeout = int(row.get("timeout", 120))
            if timeout < 1 or timeout > 120:
                raise ValueError("verification profile timeout must be in [1, 120]")
            profiles[str(name)] = VerificationProfile(
                name=str(name),
                kind=str(row.get("kind", "test")),
                argv=tuple(argv),
                allow_selectors=bool(row.get("allow_selectors", False)),
                timeout=timeout,
            )
        if required not in profiles:
            raise ValueError("required_profile must name a configured profile")
        return cls(required_profile=required, profiles=profiles)

    @classmethod
    def from_file(cls, path: Path) -> "VerificationConfig":
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))


@dataclass
class VerificationResult:
    profile: str
    scope: str
    selectors: list[str]
    exit_code: int
    timed_out: bool
    tests_collected: int
    tests_passed: int
    tests_failed: int
    patch_digest: str
    step: int
    stdout: str = ""
    stderr: str = ""

    @property
    def passed(self) -> bool:
        return not self.timed_out and self.exit_code == 0 and self.tests_collected > 0

    def to_dict(self) -> dict:
        return asdict(self)


Runner = Callable[[list[str], int], subprocess.CompletedProcess[str]]


def local_runner(root: Path, env: dict[str, str]) -> Runner:
    def run(argv: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
        local_argv = [sys.executable, *argv[1:]] if argv and argv[0] == "python" else list(argv)
        return subprocess.run(
            local_argv,
            cwd=root,
            env=env,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    return run


def validate_selectors(selectors: object) -> list[str]:
    if selectors is None:
        return []
    if not isinstance(selectors, list) or not all(isinstance(item, str) for item in selectors):
        raise ValueError("selectors must be a list of strings")
    result: list[str] = []
    expect_k_expression = False
    for raw in selectors:
        item = raw.strip()
        if not item or any(character in _FORBIDDEN_SELECTOR_CHARS for character in item):
            raise ValueError(f"unsafe verification selector: {raw!r}")
        if expect_k_expression:
            expect_k_expression = False
            result.append(item)
            continue
        if item == "-k":
            expect_k_expression = True
            result.append(item)
            continue
        if item.startswith("-"):
            raise ValueError(f"unsupported verification option: {item!r}")
        path_text = item.split("::", 1)[0]
        pure = PurePosixPath(path_text)
        if pure.is_absolute() or ".." in pure.parts or not pure.parts or pure.parts[0] != "tests":
            raise ValueError("verification selectors must stay inside public tests/")
        result.append(item)
    if expect_k_expression:
        raise ValueError("-k requires an expression")
    return result


def parse_test_counts(stdout: str, stderr: str) -> tuple[int, int, int]:
    totals: dict[str, int] = {}
    for match in _PYTEST_SUMMARY.finditer(f"{stdout}\n{stderr}"):
        totals[match.group("kind")] = max(totals.get(match.group("kind"), 0), int(match.group("count")))
    passed = totals.get("passed", 0)
    failed = totals.get("failed", 0) + totals.get("error", 0) + totals.get("errors", 0)
    collected = passed + failed + totals.get("skipped", 0) + totals.get("xfailed", 0) + totals.get("xpassed", 0)
    return collected, passed, failed
