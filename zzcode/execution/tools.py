"""工具定义与执行辅助逻辑。

可以把这个文件看成 agent 的能力白名单：模型能申请哪些动作、这些动作
如何做参数校验，以及最终如何执行，都是在这里定义的。
"""

import json
import shutil
from zzcode.core.messages import ToolSpec
from zzcode.execution.commands import CommandRequest
from zzcode.execution.output import BoundedOutput, ToolOutput
from functools import partial

from zzcode.context.workspace import IGNORED_PATH_NAMES, PRIVATE_PATH_NAMES, clip

BASE_TOOL_SPECS = {
    "list_files": {
        "schema": {
            "type": "object",
            "properties": {"path": {"type": "string", "default": "."}},
            "required": [],
            "additionalProperties": False,
        },
        "risky": False,
        "description": "List files in the workspace.",
    },
    "read_file": {
        "schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "start": {"type": "integer", "default": 1},
                "end": {"type": "integer", "default": 200},
            },
            "required": ["path"],
            "additionalProperties": False,
        },
        "risky": False,
        "description": "Read a UTF-8 file by line range.",
    },
    "search": {
        "schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string"},
                "path": {"type": "string", "default": "."},
            },
            "required": ["pattern"],
            "additionalProperties": False,
        },
        "risky": False,
        "description": "Search the workspace with rg or a simple fallback.",
    },
    "run_shell": {
        "schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "timeout": {"type": "integer", "default": 20},
            },
            "required": ["command"],
            "additionalProperties": False,
        },
        "risky": True,
        "description": "Run a shell command in the repo root.",
    },
    "write_file": {
        "schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
            "required": ["path", "content"],
            "additionalProperties": False,
        },
        "risky": True,
        "description": "Write a text file.",
    },
    "patch_file": {
        "schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "old_text": {"type": "string"},
                "new_text": {"type": "string"},
            },
            "required": ["path", "old_text", "new_text"],
            "additionalProperties": False,
        },
        "risky": True,
        "description": "Replace one exact text block in a file.",
    },
}

BASE_TOOL_SPECS["read_artifact"] = {
    "schema": {"type": "object", "properties": {"artifact_id": {"type": "string"}, "start": {"type": "integer", "default": 1}, "end": {"type": "integer", "default": 80}}, "required": ["artifact_id"], "additionalProperties": False},
    "risky": False, "description": "Read retained sanitized tool output by artifact ID and line range.",
}

DELEGATE_TOOL_SPEC = {
    "schema": {
        "type": "object",
        "properties": {
            "task": {"type": "string"},
            "max_steps": {"type": "integer", "default": 3},
        },
        "required": ["task"],
        "additionalProperties": False,
    },
    "risky": False,
    "description": "Ask a bounded read-only child agent to investigate.",
}

TOOL_EXAMPLES = {
    "list_files": {"path": "."},
    "read_file": {"path": "README.md", "start": 1, "end": 80},
    "search": {"pattern": "binary_search", "path": "."},
    "run_shell": {"command": "python -m pytest -q", "timeout": 20},
    "write_file": {"path": "file.py", "content": "print('hello')\n"},
    "patch_file": {"path": "file.py", "old_text": "hello", "new_text": "world"},
    "delegate": {"task": "inspect README.md", "max_steps": 3},
}


def native_tool_specs(registry):
    specs = []
    for name, tool in registry.items():
        schema = tool["schema"]
        side_effect = "shell" if name == "run_shell" else "file" if name in {"write_file", "patch_file"} else "none"
        recovery = "manual" if side_effect == "shell" else "file" if side_effect == "file" else "reread"
        specs.append(ToolSpec(name, tool["description"], schema, side_effect,
                              "high" if tool["risky"] else "low", 20 if name == "run_shell" else None, recovery))
    return tuple(specs)


def build_tool_registry(agent):
    # 工具不是动态发现的，而是显式注册的。
    # 这样模型看到的是一个有边界、可审计的动作集合。
    tools = {}
    for name, spec in BASE_TOOL_SPECS.items():
        tools[name] = dict(spec)
        if name in _TOOL_RUNNERS:
            tools[name]["run"] = partial(_TOOL_RUNNERS[name], agent)
    # 子 agent 是刻意做成受限能力的：一旦深度耗尽，
    # 就连 delegate 这个工具都不再暴露给模型。
    if agent.depth < agent.max_depth:
        tools["delegate"] = {**DELEGATE_TOOL_SPEC, "run": partial(tool_delegate, agent)}
    return tools


def tool_example(name):
    return json.dumps(TOOL_EXAMPLES.get(name, {}), ensure_ascii=False)


def validate_tool(agent, name, args):
    if not isinstance(args, dict):
        raise ValueError("arguments must be a JSON object")
    schema = next(
        spec.input_schema
        for spec in native_tool_specs(agent.tools)
        if spec.name == name
    )
    for required in schema["required"]:
        if required not in args:
            raise ValueError(f"missing {required}")
    for key, value in args.items():
        if key not in schema["properties"]:
            raise ValueError(f"unexpected argument: {key}")
        kind = schema["properties"][key]["type"]
        if (kind == "string" and not isinstance(value, str)) or (
            kind == "integer"
            and (not isinstance(value, int) or isinstance(value, bool))
        ):
            raise ValueError(f"{key} must be {kind}")

    def reject_private_path(path):
        relative_parts = path.relative_to(agent.root).parts
        if any(part in PRIVATE_PATH_NAMES | {".zzcode", ".git"} or part.startswith(".env.") for part in relative_parts):
            raise ValueError("access to private environment files is not allowed")

    if name == "list_files":
        path = agent.path(args.get("path", "."))
        reject_private_path(path)
        if not path.is_dir():
            raise ValueError("path is not a directory")
        return

    if name == "read_file":
        path = agent.path(args["path"])
        reject_private_path(path)
        if not path.is_file():
            raise ValueError("path is not a file")
        start = int(args.get("start", 1))
        end = int(args.get("end", 200))
        if start < 1 or end < start:
            raise ValueError("invalid line range")
        return

    if name == "search":
        pattern = str(args.get("pattern", "")).strip()
        if not pattern:
            raise ValueError("pattern must not be empty")
        reject_private_path(agent.path(args.get("path", ".")))
        return

    if name == "run_shell":
        command = str(args.get("command", "")).strip()
        if not command:
            raise ValueError("command must not be empty")
        timeout = int(args.get("timeout", 20))
        if timeout < 1 or timeout > 120:
            raise ValueError("timeout must be in [1, 120]")
        return

    if name == "write_file":
        path = agent.path(args["path"])
        reject_private_path(path)
        if path.exists() and path.is_dir():
            raise ValueError("path is a directory")
        if "content" not in args:
            raise ValueError("missing content")
        return

    if name == "read_artifact":
        if not 1 <= args.get("start", 1) <= args.get("end", 80) or args.get("end", 80) - args.get("start", 1) >= 200:
            raise ValueError("invalid artifact line range")
        return

    if name == "patch_file":
        # patch_file 故意做得很严格：old_text 必须精确命中且只能出现一次，
        # 这样修改行为才是确定的，失败原因也更容易解释。
        path = agent.path(args["path"])
        reject_private_path(path)
        if not path.is_file():
            raise ValueError("path is not a file")
        old_text = str(args.get("old_text", ""))
        if not old_text:
            raise ValueError("old_text must not be empty")
        if "new_text" not in args:
            raise ValueError("missing new_text")
        return

    if name == "delegate":
        task = str(args.get("task", "")).strip()
        if not task:
            raise ValueError("task must not be empty")
        return


def tool_list_files(agent, args):
    path = agent.path(args.get("path", "."))
    if not path.is_dir():
        raise ValueError("path is not a directory")
    entries = [
        item
        for item in sorted(
            path.iterdir(), key=lambda item: (item.is_file(), item.name.lower())
        )
        if item.name not in IGNORED_PATH_NAMES
    ]
    lines = []
    for entry in entries[:200]:
        kind = "[D]" if entry.is_dir() else "[F]"
        lines.append(f"{kind} {entry.relative_to(agent.root)}")
    return "\n".join(lines) or "(empty)"


def tool_read_file(agent, args):
    path = agent.path(args["path"])
    if not path.is_file():
        raise ValueError("path is not a file")
    start = int(args.get("start", 1))
    end = int(args.get("end", 200))
    if start < 1 or end < start:
        raise ValueError("invalid line range")
    output = BoundedOutput()
    next_line = None
    with path.open("rb") as handle:
        number = 1
        while True:
            # 长单行也分块读取，避免 readlines/read_text 把整个文件载入内存。
            chunk = handle.readline(8192)
            if not chunk:
                break
            if start <= number <= end:
                output.append(f"{number:>4}: ".encode() + chunk)
            if chunk.endswith(b"\n"):
                number += 1
            if number > end or output.truncated:
                next_line = number
                break
    text = f"# {path.relative_to(agent.root)}\n{output.text().rstrip()}"
    if output.truncated:
        text += "\n[byte limit reached; narrow the requested line range; a single oversized line cannot be read in full]"
    elif next_line:
        text += f"\n[continue with read_file start={next_line}; retained output may be truncated]"
    return ToolOutput(text, output.truncated)


def tool_search(agent, args):
    pattern = str(args.get("pattern", "")).strip()
    if not pattern:
        raise ValueError("pattern must not be empty")
    path = agent.path(args.get("path", "."))

    command = ["rg", "-n", "--smart-case", "--max-count", "200", "--glob", "!.env*", "--glob", "!.zzcode/**", "--glob", "!.git/**", "--", pattern, str(path)]
    if shutil.which("rg") is None:
        raise RuntimeError("search requires rg")
    # 搜索同样走有限输出读取；不在缺少依赖时维护第二份搜索实现。
    from zzcode.execution.shell import execute_shell
    import shlex
    result = execute_shell(shlex.join(command), cwd=agent.root, env=agent.shell_env(), timeout=20)
    if result.timed_out:
        raise TimeoutError("search timed out")
    if result.exit_code not in {0, 1}:
        raise RuntimeError(result.stderr or "search failed")
    matches = result.stdout.splitlines()
    truncated = result.truncated or len(matches) > 200
    text = "\n".join(matches[:200]) or "(no matches)"
    text += "\n[at most 200 matches shown; total unknown]" if truncated else "\n[shown matches complete unless a file reached its 200-match limit; total unknown]"
    return ToolOutput(text, truncated)


def tool_run_shell(agent, args, *, on_start=None):
    return agent.executor.execute(CommandRequest(args["command"], agent.root, agent.shell_env(), args.get("timeout", 20)), on_start=on_start)


def tool_delegate(agent, args):
    if agent.depth >= agent.max_depth:
        raise ValueError("delegate depth exceeded")
    task = str(args.get("task", "")).strip()
    if not task:
        raise ValueError("task must not be empty")

    from zzcode.agent.coordinator import ZZCode

    child = ZZCode(
        model_client=agent.model_client,
        workspace=agent.workspace,
        session_store=agent.session_store,
        run_store=agent.run_store,
        approval_policy="never",
        max_steps=int(args.get("max_steps", 3)),
        max_new_tokens=agent.max_new_tokens,
        depth=agent.depth + 1,
        max_depth=agent.max_depth,
        read_only=True,
        secret_env_names=agent.secret_env_names,
        shell_env_allowlist=agent.shell_env_allowlist,
        operation_ledger=agent.gateway.ledger,
        executor=agent.executor,
    )
    # 委派的目标是“调查”，不是“放权执行”。
    # 子 agent 以只读方式运行、步数更少，最后只把结论文本返回给父 agent。
    child.session["memory"]["task"] = task
    child.session["memory"]["notes"] = [clip(agent.history_text(), 300)]
    return "delegate_result:\n" + child.ask(task)


_TOOL_RUNNERS = {
    "list_files": tool_list_files,
    "read_file": tool_read_file,
    "search": tool_search,
    "run_shell": tool_run_shell,
}
