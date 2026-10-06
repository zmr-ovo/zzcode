"""Compare information retention against the frozen P2 policy; no live model costs."""

import argparse
import inspect
import json
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from zzcode import FakeModelClient, MiniAgent, SessionStore, WorkspaceContext  # noqa: E402
from zzcode.core.messages import Message, ToolCall, ToolResult  # noqa: E402


def prepare(agent):
    agent.record({"role": "user", "content": "约束：保持接口不变，添加必要中文注释。"})
    for index in range(12):
        call = ToolCall(f"old_{index}", "read_file", {"path": "target.txt"})
        result = ToolResult(call.call_id, call.name, "code evidence " + "x" * 4000)
        agent.record(
            {
                "role": "assistant",
                "content": "inspect",
                "message": Message("assistant", (call,)).to_dict(),
            }
        )
        agent.record(
            {
                "role": "tool",
                "name": call.name,
                "args": call.arguments,
                "content": result.content,
                "message": Message("tool", (result,)).to_dict(),
            }
        )
    agent._turn_start_index = len(agent.session["history"])
    agent.record({"role": "user", "content": "继续修改 target.txt"})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    archive = ROOT / "artifacts/p2-baseline/verified/source.zip"
    # 旧代码只在临时目录运行，冻结证据保持原样。
    with tempfile.TemporaryDirectory(prefix="zzcode-p3-comparison-") as directory:
        root = Path(directory)
        old = root / "p2"
        old.mkdir()
        with zipfile.ZipFile(archive) as source:
            for name in source.namelist():
                if name.startswith("zzcode/"):
                    source.extract(name, old)
        program = (
            """
import json
from pathlib import Path
from zzcode import FakeModelClient,MiniAgent,SessionStore,WorkspaceContext
from zzcode.core.messages import Message,ToolCall,ToolResult
root=Path('workspace').resolve(); root.mkdir(); (root/'target.txt').write_text('old')
a=MiniAgent(FakeModelClient([]),WorkspaceContext.build(root),SessionStore(root/'.zzcode/sessions'),approval_policy='auto')
"""
            + inspect.getsource(prepare)
            + "\nprepare(a)\n"
            + """
p,m=a._build_prompt_and_metadata('继续修改 target.txt'); r=a.context_manager.build_request('继续修改 target.txt',p,m)
text=json.dumps([x.to_dict() for x in r.messages],ensure_ascii=False)
print(json.dumps({'constraint_retained':'保持接口不变' in text,'history_messages':len(r.messages),'serialized_history_bytes':len(text.encode())}))
"""
        )
        result = subprocess.run(
            [sys.executable, "-c", program],
            cwd=old,
            capture_output=True,
            text=True,
            check=True,
        )
        previous = json.loads(result.stdout)
        current_root = root / "p3"
        current_root.mkdir()
        (current_root / "target.txt").write_text("old")
        agent = MiniAgent(
            FakeModelClient([]),
            WorkspaceContext.build(current_root),
            SessionStore(current_root / ".zzcode/sessions"),
            approval_policy="auto",
            context_window=22000,
            max_output_tokens=512,
        )
        prepare(agent)
        prompt, metadata = agent._build_prompt_and_metadata("继续修改 target.txt")
        request = agent.context_manager.build_request(
            "继续修改 target.txt", prompt, metadata
        )
        text = json.dumps([m.to_dict() for m in request.messages], ensure_ascii=False)
        current = {
            "constraint_retained": "保持接口不变" in text,
            "history_messages": len(request.messages),
            "estimated_input_tokens": metadata["estimated_input_tokens"],
            "available_input_tokens": metadata["context_available_tokens"],
            "compaction_count": len(agent.session["compactions"]),
            "raw_history_preserved": len(agent.session["history"]) == 26,
        }
        if not current["constraint_retained"] or not current["raw_history_preserved"]:
            raise RuntimeError("P3 information retention failed")
    data = {
        "previous_phase": "P2",
        "previous_source": "artifacts/p2-baseline/verified/source.zip",
        "previous": previous,
        "current": current,
        "real_tokens": None,
        "real_cost": None,
        "measurement_limit": "Scripted information retention; estimates are not actual provider token usage or cost.",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(data, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
