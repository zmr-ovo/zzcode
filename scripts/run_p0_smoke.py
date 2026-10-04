"""Fixed two-task real-model smoke; dry-run validates data without inference."""

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from zzcode.evaluation import EvaluationDataset  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--private-root", type=Path, default=ROOT / "evaluation/private")
    parser.add_argument("--artifact-root", type=Path, default=ROOT / "evaluation/runs")
    parser.add_argument("--workspace-root", type=Path, default=ROOT / "evaluation/workspaces")
    args = parser.parse_args()
    config = json.loads((ROOT / "evaluation/configs/p0-smoke.json").read_text())
    dataset = EvaluationDataset.load(ROOT / config["dataset"], args.private_root, config["split"])
    if not dataset.verify_lock():
        raise SystemExit("P0 smoke requires a dataset lock")
    tasks = {task.instance_id: task for task in dataset.tasks()}
    for instance_id in config["instance_ids"]:
        if tasks[instance_id].base_commit != config["base_commit"]:
            raise SystemExit("P0 smoke base commit drifted")
    subprocess.run(
        ["git", "cat-file", "-e", config["base_commit"] + "^{commit}"], cwd=ROOT, check=True,
    )
    command = [
        sys.executable, str(ROOT / "scripts/run_internal_eval.py"),
        "--repo-path", str(ROOT), "--public-root", str(ROOT / config["dataset"]),
        "--private-root", str(args.private_root.resolve()), "--split", config["split"],
        "--artifact-root", str(args.artifact_root.resolve()),
        "--workspace-root", str(args.workspace_root.resolve()),
    ]
    for instance_id in config["instance_ids"]:
        command.extend(["--instance-id", instance_id])
    for key in (
        "provider", "model", "temperature", "max_steps", "max_new_tokens", "image",
        "gold_repetitions", "test_timeout_seconds", "agent_timeout_seconds", "provider_timeout_seconds",
    ):
        command.extend(["--" + key.replace("_", "-"), str(config[key])])
    if args.dry_run:
        print(json.dumps({
            "status": "ready", "inference_executed": False,
            "instance_ids": config["instance_ids"], "base_commit": config["base_commit"],
            "source_split_digest": dataset.digest(), "dataset_lock_verified": True,
            "command": command,
        }, indent=2))
        return 0
    return subprocess.run(command, cwd=ROOT, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
