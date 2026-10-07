"""Save reproducible P4 evidence without touching user files or live models."""

import argparse
import hashlib
import importlib.metadata
import json
import platform
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.testing.protocol_golden import load_cases, load_profile, run_case  # noqa: E402
from zzcode.benchmarks.evaluator import run_harness_regression_v2  # noqa: E402


def digest(path):
    return "sha256:" + hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, data):
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def run_check(command, output, name, timeout=900):
    started = time.monotonic()
    try:
        result = subprocess.run(
            command,
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
        )
        code, log = result.returncode, result.stdout
    except subprocess.TimeoutExpired as exc:
        code = 124
        log = exc.stdout or b""
        if isinstance(log, bytes):
            log = log.decode(errors="replace")
        log += "\nP0 runner timed out.\n"
    (output / f"{name}.txt").write_text(log)
    return {
        "command": command,
        "exit_code": code,
        "duration_seconds": round(time.monotonic() - started, 3),
        "log": f"{name}.txt",
    }


def junit_summary(path):
    suites = list(ET.parse(path).getroot().iter("testsuite"))
    counts = {
        key: sum(int(s.get(key, 0)) for s in suites)
        for key in ("tests", "failures", "errors", "skipped")
    }
    counts["passed"] = (
        counts["tests"] - counts["failures"] - counts["errors"] - counts["skipped"]
    )
    return counts


def source_snapshot(output):
    tracked = git("ls-files", "-z").split("\0")
    untracked = git("ls-files", "--others", "--exclude-standard", "-z").split("\0")
    extra = [
        "zzcode/storage/session.py",
        "tests/context/test_p3_context.py",
        "scripts/testing/run_p3_context_comparison.py",
        *[str(path.relative_to(ROOT)) for path in (ROOT / "zzcode/context").glob("*.py")],
        "agent.md",
        "tests/execution/test_tool_execution.py",
        *[str(path.relative_to(ROOT)) for path in (ROOT / "zzcode/execution").glob("*.py")],
        "uv.lock",
        "scripts/testing/freeze_p4_baseline.py",
        "scripts/testing/protocol_golden.py",
        "scripts/testing/run_native_smoke.py",
        "tests/providers/test_native_protocol.py",
        "zzcode/core/__init__.py",
        "zzcode/core/messages.py",
        "zzcode/providers/__init__.py",
        "zzcode/providers/native.py",
        "docs/testing/p3-result.md",
        "scripts/testing/run_p0_smoke.py",
        "tests/benchmarks/test_protocol_golden.py",
        "tests/fixtures/native_golden.json",
        "evaluation/configs/migration-profiles.json",
        "evaluation/configs/p0-smoke.json",
        "docs/zzcode-upgrade-plan-v2.md",
        "docs/architecture/agent-harness-v1-overview.md",
        "docs/review-pack/README.md",
    ]
    extra += [str(path.relative_to(ROOT)) for folder in ("zzcode", "scripts", "tests", "docs/archive", "docs/evaluation")
              for path in (ROOT / folder).rglob("*") if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"]
    extra += ["docs/architecture/project-layout.md", "docs/architecture/p4-path-migration.json", "docs/testing/p4-result.md", "docs/testing/p5-result.md"]
    hashes = {}
    with zipfile.ZipFile(output / "source.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(set(tracked + extra)):
            path = ROOT / name
            if (
                not name
                or name.startswith("artifacts/")
                or not path.is_file()
                or path.is_symlink()
            ):
                continue
            if path.name == ".env" or (
                path.name.startswith(".env.") and path.name != ".env.example"
            ):
                continue
            if path.resolve().is_relative_to(output):
                continue
            archive.write(path, name)
            hashes[name] = digest(path)
    # Preserve user files in place; their contents are never copied into evidence.
    other = {}
    for name in untracked:
        path = ROOT / name
        if (
            name
            and name not in extra
            and not name.startswith("artifacts/p4-baseline/")
            and path.is_file()
            and not path.resolve().is_relative_to(output)
        ):
            other[name] = digest(path)
    return {"source_hashes": hashes, "other_untracked_hashes": other}


def main(*, phase="P4", default_profile="agent_runtime"):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--profile", default=default_profile)
    args = parser.parse_args()
    features = load_profile(args.profile)
    output = args.output.resolve()
    if output.exists() and any(output.iterdir()):
        raise SystemExit(
            "output directory must be new or empty; use a new run directory"
        )
    output.mkdir(parents=True, exist_ok=True)
    started = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()
    manifest = {
        "schema_version": 1,
        "phase": phase,
        "source_state": "Working tree based on head; source.zip and source_hashes define the validated code.",
        "started_at": started,
        "timezone": "Asia/Shanghai",
        "head": git("rev-parse", "HEAD"),
        "initial_git_status": git("status", "--short"),
        "profile": args.profile,
        "features": features,
        "environment": {
            "python": sys.version,
            "python_executable": sys.executable,
            "platform": platform.platform(),
            "dependencies": {
                name: importlib.metadata.version(name)
                for name in ("zzcode", "pytest", "python-dotenv", "ruff")
            },
            "uv_version": subprocess.check_output(
                ["uv", "--version"], text=True
            ).strip(),
            "package_source": str(__import__("zzcode").__file__),
        },
        "checks": {},
        "measurement_limits": {
            "tokens": None,
            "cost": None,
            "reason": "FakeModel has no real token usage or provider cost; null is not zero",
            "latency": "local wall time only; not real-provider latency",
            "resolved": "scripted diagnostics are not coding capability measurements",
        },
    }
    checks = manifest["checks"]
    for name, target, extra in (
        ("product-tests", "tests", ["--ignore=tests/evaluation"]),
        ("harness-tests", "tests/evaluation", ["-m", "not docker and not real_model"]),
    ):
        print(f"Running {name}...", flush=True)
        checks[name] = run_check(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                target,
                *extra,
                f"--junitxml={output / (name + '.xml')}",
            ],
            output,
            name,
        )
        if (output / f"{name}.xml").exists():
            checks[name]["counts"] = junit_summary(output / f"{name}.xml")
    checks["pytest-entry"] = run_check(
        [str(Path(sys.executable).parent / "pytest"), "--collect-only", "-q", "tests"],
        output,
        "pytest-entry",
    )
    checks["installed-package"] = run_check(
        [sys.executable, "-I", "-c", "import zzcode; print(zzcode.__file__)"],
        output,
        "installed-package",
    )
    checks["cli-entry"] = run_check(
        [str(Path(sys.executable).parent / "zzcode"), "--help"],
        output,
        "cli-entry",
    )
    checks["lint"] = run_check(
        [
            str(Path(sys.executable).parent / "ruff"),
            "check",
            "scripts/testing/freeze_p4_baseline.py",
            "scripts/testing/protocol_golden.py",
            "scripts/testing/run_p0_smoke.py",
            "scripts/evaluation/run_internal_eval.py",
            "tests/benchmarks/test_protocol_golden.py",
            "tests/providers/test_native_protocol.py",
            "scripts/testing/run_native_smoke.py",
            "zzcode/agent",
            "tests/agent/test_p4_runtime.py",
            "zzcode/context",
            "zzcode/storage/session.py",
            "tests/context/test_p3_context.py",
            "scripts/testing/run_p3_context_comparison.py",
            "zzcode/execution",
            "zzcode/cli.py",
            "tests/execution/test_tool_execution.py",
            "zzcode/core",
            "zzcode/providers",
            "zzcode/models.py",
            "zzcode/agent/coordinator.py",
            "zzcode/context/manager.py",
            "zzcode/execution/tools.py",
            "zzcode/benchmarks/metrics.py",
            "zzcode/benchmarks/evaluator.py",
            "tests/agent/test_zzcode.py",
            "tests/execution/test_safety_invariants.py",
        ],
        output,
        "lint",
    )
    checks["context-comparison"] = run_check(
        [sys.executable, str(ROOT / "scripts/testing/run_p3_context_comparison.py"), "--output", str(output / "context-comparison.json")], output, "context-comparison")
    print("Running native golden transcripts...", flush=True)
    golden = []
    try:
        for case in load_cases():
            case_root = output / "golden-runs" / case["id"]
            row = run_case(case, case_root)
            write_json(case_root / "transcript.json", row)
            golden.append(row)
        checks["golden"] = {"passed": len(golden), "failed": 0}
    except Exception as exc:
        checks["golden"] = {"passed": len(golden), "failed": 1, "error": str(exc)}
    write_json(output / "golden.json", golden)
    print("Running 12 scripted regressions twice in separate workspaces...", flush=True)
    stable_rows = []
    for repetition in (1, 2):
        began = time.monotonic()
        workspaces = output / "workspaces" / str(repetition)
        artifact = run_harness_regression_v2(
            benchmark_path=ROOT / "benchmarks/coding_tasks.json",
            artifact_path=output
            / ("regression.json" if repetition == 1 else "regression-repeat.json"),
            workspace_root=workspaces,
        )
        stable_rows.append(
            [
                {
                    key: row[key]
                    for key in (
                        "id",
                        "passed",
                        "stop_reason",
                        "tool_steps",
                        "attempts",
                        "artifact_digest",
                    )
                }
                for row in artifact["rows"]
            ]
        )
        checks[f"regression-{repetition}"] = {
            "summary": artifact["summary"],
            "duration_seconds": round(time.monotonic() - began, 3),
        }
        if repetition == 1:
            for row in artifact["rows"]:
                source = workspaces / row["run_dir_relpath"]
                shutil.copytree(source, output / "regression-runs" / row["id"])
    checks["regression-repeatability"] = {
        "semantic_results_equal": stable_rows[0] == stable_rows[1]
    }
    checks["repo-smoke-preflight"] = run_check(
        [sys.executable, str(ROOT / "scripts/testing/run_p0_smoke.py"), "--dry-run"],
        output,
        "repo-smoke-preflight",
    )
    try:
        docker = subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            text=True,
            capture_output=True,
            timeout=15,
            check=False,
        )
        docker_available = docker.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        docker_available = False
    manifest["real_repo_smoke"] = {
        "instance_ids": ["ZZCODE-BUG-001", "ZZCODE-BUG-002"],
        "inference_executed": False,
        "docker_available": docker_available,
        "status": "not_run" if docker_available else "blocked",
        "reason": "real-model smoke is separate from deterministic P4 freeze"
        if docker_available
        else "Docker daemon is unavailable",
        "image_digest": None,
    }
    # No credentials or environment values are recorded.
    manifest.update(source_snapshot(output))
    shutil.copyfile(ROOT / "uv.lock", output / "uv.lock")
    changed = [
        ".gitignore",
        "README.md",
        "zzcode",
        "tests/agent/test_zzcode.py",
        "tests/execution/test_safety_invariants.py",
        "tests/test_p0_golden.py",
        "scripts/testing/freeze_p0_baseline.py",
        "scripts/p0_golden.py",
        "tests/fixtures/legacy_golden.json",
        "evaluation/configs/migration-profiles.json",
        "docs/zzcode-upgrade-plan-v2.md",
    ]
    (output / "changes.patch").write_text(git("diff", "--", *changed) + "\n")
    passed = (
        all(
            checks[name]["exit_code"] == 0
            for name in (
                "product-tests",
                "harness-tests",
                "pytest-entry",
                "installed-package",
                "cli-entry",
                "lint",
                "repo-smoke-preflight",
                "context-comparison",
            )
        )
        and checks["golden"]["failed"] == 0
        and all(checks[f"regression-{i}"]["summary"]["passed"] == 12 for i in (1, 2))
        and checks["regression-repeatability"]["semantic_results_equal"]
    )
    manifest["local_gate"] = "passed" if passed else "failed"
    manifest["finished_at"] = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()
    manifest["artifacts"] = {
        str(path.relative_to(output)): digest(path)
        for path in sorted(output.rglob("*"))
        if path.is_file()
        and not path.is_relative_to(output / "workspaces")
        and ".zzcode" not in path.relative_to(output).parts
    }
    write_json(output / "manifest.json", manifest)
    print(
        json.dumps(
            {
                "output": str(output),
                "local_gate": manifest["local_gate"],
                "checks": checks,
                "real_repo_smoke": manifest["real_repo_smoke"],
            },
            indent=2,
        )
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
