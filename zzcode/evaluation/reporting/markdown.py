"""Render a completed evaluation run as a compact Markdown report."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from ..errors import ArtifactError
from ..serialization import write_text_atomic


def _load_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ArtifactError(f"missing evaluation artifact: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ArtifactError(f"invalid JSON artifact {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ArtifactError(f"evaluation artifact must be a JSON object: {path}")
    return value


def _rate(value: object) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ArtifactError("evaluation rate must be numeric or null")
    return f"{float(value) * 100:.1f}%"


def _cell(value: object) -> str:
    return str(value if value is not None else "-").replace("|", "\\|").replace("\n", " ")


def render_run_report(manifest: Mapping[str, Any], results: Mapping[str, Any]) -> str:
    """Render already-persisted run data without recomputing any score."""

    summary = results.get("summary")
    tasks = results.get("tasks")
    if not isinstance(summary, Mapping) or not isinstance(tasks, list):
        raise ArtifactError("results.json must contain summary and tasks")

    dataset_gates_passed = summary.get(
        "dataset_gate_passed", summary.get("dataset_gates_passed")
    )
    status_counts = summary.get(
        "status_counts", summary.get("resolved_status_counts", {})
    )
    failure_counts: dict[str, int] = {}
    for task in tasks:
        if isinstance(task, Mapping) and task.get("failure_category"):
            category = str(task["failure_category"])
            failure_counts[category] = failure_counts.get(category, 0) + 1

    lines = [
        f"# Evaluation Report: {_cell(manifest.get('run_id'))}",
        "",
        "## Run configuration",
        "",
        f"- Dataset: `{_cell(manifest.get('dataset_name'))}` / `{_cell(manifest.get('split'))}`",
        f"- Dataset digest: `{_cell(manifest.get('dataset_digest'))}`",
        f"- Agent commit: `{_cell(manifest.get('agent_commit'))}`",
        f"- Model: `{_cell(manifest.get('provider'))}:{_cell(manifest.get('model_name_or_path'))}`",
        f"- Environment: `{_cell(manifest.get('environment_id'))}`",
        f"- Container image digest: `{_cell(manifest.get('image_digest'))}`",
        "",
        "## Pass@1 summary",
        "",
        f"- Resolved: **{_cell(summary.get('resolved'))}/{_cell(summary.get('task_count'))}**",
        f"- Resolution rate: **{_rate(summary.get('resolution_rate'))}**",
        f"- Dataset gates passed: **{_cell(dataset_gates_passed)}/{_cell(summary.get('task_count'))}**",
        "",
        "## Per-task results",
        "",
        "| Instance | Dataset gate | Agent | Resolved | F2P | P2P | Failure category |",
        "|---|---:|---|---|---:|---:|---|",
    ]
    for task in tasks:
        if not isinstance(task, Mapping):
            raise ArtifactError("results.json tasks must contain JSON objects")
        lines.append(
            "| "
            + " | ".join(
                (
                    _cell(task.get("instance_id")),
                    "PASS" if task.get("dataset_gate_passed") is True else "FAIL",
                    _cell(task.get("agent_status")),
                    _cell(task.get("resolved_status")),
                    _rate(task.get("fail_to_pass_rate")),
                    _rate(task.get("pass_to_pass_rate")),
                    _cell(task.get("failure_category")),
                )
            )
            + " |"
        )

    lines.extend(
        [
            "",
            "## Distribution",
            "",
            f"- Resolved status: `{json.dumps(status_counts, ensure_ascii=False, sort_keys=True)}`",
            f"- Failure category: `{json.dumps(failure_counts, ensure_ascii=False, sort_keys=True)}`",
            "",
            "> Scores in this document are rendered from immutable run artifacts; the renderer does not execute tests or change grading decisions.",
        ]
    )
    return "\n".join(lines) + "\n"


def render_run_directory(run_root: Path, output: Path | None = None) -> Path:
    """Read one completed run and atomically write its Markdown report."""

    root = Path(run_root).resolve()
    manifest = _load_object(root / "run_manifest.json")
    results = _load_object(root / "results.json")
    if manifest.get("run_id") != root.name or results.get("run_id") != root.name:
        raise ArtifactError("run_id does not match the run directory")
    destination = Path(output).resolve() if output is not None else root / "report.md"
    if destination.exists():
        destination.write_text(render_run_report(manifest, results), encoding="utf-8")
        return destination
    return write_text_atomic(destination, render_run_report(manifest, results), overwrite=False)
