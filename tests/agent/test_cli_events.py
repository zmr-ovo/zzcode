import json


import sys



import pytest

from zzcode import Agent, FakeModelClient, SessionStore, WorkspaceContext

from zzcode import cli

from zzcode.core.messages import Message, ModelResponse, ToolCall


def build_agent(tmp_path, outputs, **kwargs):
    (tmp_path / 'input.txt').write_text('input\n')
    return Agent(FakeModelClient(outputs), WorkspaceContext.build(tmp_path),
                 SessionStore(tmp_path / '.zzcode/sessions'), approval_policy=kwargs.pop('approval_policy', 'auto'), **kwargs)

def writes():
    return ModelResponse(Message('assistant', (
        ToolCall('first', 'write_file', {'path': 'a.txt', 'content': 'a'}),
        ToolCall('second', 'write_file', {'path': 'b.txt', 'content': 'b'}),
    )), 'tool_call')

@pytest.mark.parametrize('mode', ['human', 'jsonl'])
def test_cli_uses_same_sdk_entry_and_jsonl_is_pure(tmp_path, monkeypatch, capsys, mode):
    agent = build_agent(tmp_path, ['done'])
    monkeypatch.setattr(cli, 'build_agent', lambda args: agent)
    monkeypatch.setattr(cli, '_load_env_files', lambda cwd: None)
    assert cli.main(['--cwd', str(tmp_path), '--output', mode, '--task-type', 'question', 'hello']) == 0
    captured = capsys.readouterr()
    if mode == 'jsonl':
        rows = [json.loads(line) for line in captured.out.splitlines()]
        assert rows[0]['type'] == 'run.started'
        assert rows[-1]['payload']['result'] == agent.last_result.to_dict()
    else:
        assert captured.out.endswith('done\n')

def test_failed_renderer_closes_stream_before_tool_effect(tmp_path, monkeypatch):
    agent = build_agent(tmp_path, [writes(), 'done'])
    monkeypatch.setattr(cli, 'build_agent', lambda args: agent)
    monkeypatch.setattr(cli, '_load_env_files', lambda cwd: None)
    class BrokenOutput:
        def write(self, text):
            if 'model.completed' in text:
                raise OSError('renderer unavailable')
            return len(text)
        def flush(self):
            pass
    monkeypatch.setattr(sys, 'stdout', BrokenOutput())
    with pytest.raises(OSError, match='renderer unavailable'):
        cli.main(['--cwd', str(tmp_path), '--output', 'jsonl', 'write'])
    assert not (tmp_path / 'a.txt').exists()
    assert agent.last_result.stop_reason == 'cancelled'
