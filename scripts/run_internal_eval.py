#!/usr/bin/env python3
"""Run the Phase 6 Null -> Gold x3 -> real Agent -> Grader pipeline."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from dotenv import load_dotenv

from zzcode.evaluation import (
    AgentRunConfig,
    DockerRunner,
    DockerTestExecutor,
    EvaluationDataset,
    ResourceLimits,
    VerticalSliceRunner,
    ZZCodeAgentAdapter,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="执行 Phase 6 两项 Repo Task 的完整纵向切片评测。"
    )
    parser.add_argument(
        "--public-root",
        type=Path,
        default=Path("evaluation/datasets/zzcode-bench-v1"),
    )
    parser.add_argument("--private-root", type=Path, default=None)
    parser.add_argument("--split", default="dev")
    parser.add_argument("--repo-path", type=Path, default=Path.cwd())
    parser.add_argument(
        "--workspace-root",
        type=Path,
        default=Path("evaluation/workspaces"),
    )
    parser.add_argument("--artifact-root", type=Path, default=Path("evaluation/runs"))
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--provider", choices=("openai", "anthropic", "ollama"), required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--host", default=None)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--top-p", type=float, default=0.9)
    parser.add_argument("--max-steps", type=int, default=30)
    parser.add_argument("--max-new-tokens", type=int, default=8192)
    parser.add_argument("--agent-timeout-seconds", type=float, default=900.0)
    parser.add_argument("--provider-timeout-seconds", type=float, default=300.0)
    parser.add_argument("--test-timeout-seconds", type=float, default=120.0)
    parser.add_argument("--gold-repetitions", type=int, default=3)
    parser.add_argument("--image", default="zzcode-eval-py313:phase4")
    parser.add_argument("--cpus", type=float, default=1.0)
    parser.add_argument("--memory-mb", type=int, default=1024)
    parser.add_argument("--pids-limit", type=int, default=128)
    parser.add_argument("--tmpfs-mb", type=int, default=256)
    return parser


def _private_root(argument: Path | None) -> Path:
    if argument is not None:
        return argument
    configured = os.environ.get("ZZCODE_EVAL_PRIVATE_ROOT")
    if configured:
        return Path(configured)
    return Path("evaluation/private")


def main() -> int:
    args = build_parser().parse_args()
    repo_path = args.repo_path.resolve()
    # 只将凭据加载到当前进程环境；Manifest、request 和日志均不保存其值。
    load_dotenv(repo_path / ".env", override=False)
    public_root = args.public_root.resolve()
    private_root = _private_root(args.private_root).resolve()
    workspace_root = args.workspace_root.resolve()
    artifact_root = args.artifact_root.resolve()
    workspace_root.mkdir(parents=True, exist_ok=True)
    artifact_root.mkdir(parents=True, exist_ok=True)

    dataset = EvaluationDataset.load(public_root, private_root, args.split)
    task_repos = {task.repo for task in dataset.tasks()}
    if len(task_repos) != 1:
        raise SystemExit("Phase 6 runner currently requires one repository per run")
    repo_name = next(iter(task_repos))
    limits = ResourceLimits(
        cpus=args.cpus,
        memory_mb=args.memory_mb,
        pids_limit=args.pids_limit,
        tmpfs_mb=args.tmpfs_mb,
    )
    docker = DockerRunner(allowed_mount_roots=(workspace_root, artifact_root))
    image_digest = docker.image_digest(args.image)
    test_executor = DockerTestExecutor(docker, image=args.image, limits=limits)
    config = AgentRunConfig(
        provider=args.provider,
        model=args.model,
        temperature=args.temperature,
        top_p=args.top_p,
        max_steps=args.max_steps,
        max_new_tokens=args.max_new_tokens,
        timeout_seconds=args.agent_timeout_seconds,
        provider_timeout_seconds=args.provider_timeout_seconds,
        base_url=args.base_url,
        host=args.host,
        tool_image=args.image,
    )
    runner = VerticalSliceRunner(
        dataset=dataset,
        repositories={repo_name: repo_path},
        workspace_root=workspace_root,
        artifact_root=artifact_root,
        test_executor=test_executor,
        adapter=ZZCodeAgentAdapter(),
        environment_image=args.image,
        image_digest=image_digest,
        resource_limits=limits,
        gold_repetitions=args.gold_repetitions,
        test_timeout_seconds=args.test_timeout_seconds,
    )
    paths = runner.run(config, run_id=args.run_id)
    payload = json.loads(paths.results.read_text(encoding="utf-8"))
    print(
        json.dumps(
            {
                "run_id": paths.root.name,
                "run_root": str(paths.root),
                "summary": payload["summary"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
