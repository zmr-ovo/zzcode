"""事件消费边界、完成证据和 CLI 的端到端契约。"""
import json
import shlex
import sys
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from zzcode import Agent, AgentEvent, FakeModelClient, RunRequest, SessionStore, WorkspaceContext
from zzcode import cli
from zzcode.core.messages import Message, ModelResponse, ToolCall, tool_response
from zzcode.storage.session import SessionError


def build_agent(tmp_path, outputs, **kwargs):
    (tmp_path / 'input.txt').write_text('input\n')
    return Agent(FakeModelClient(outputs), WorkspaceContext.build(tmp_path),
                 SessionStore(tmp_path / '.zzcode/sessions'), approval_policy=kwargs.pop('approval_policy', 'auto'), **kwargs)


def writes():
    return ModelResponse(Message('assistant', (
        ToolCall('first', 'write_file', {'path': 'a.txt', 'content': 'a'}),
        ToolCall('second', 'write_file', {'path': 'b.txt', 'content': 'b'}),
    )), 'tool_call')


def test_order_ids_immutable_payload_and_persisted_facts(tmp_path):
    agent = build_agent(tmp_path, [tool_response('read_file', {'path': 'input.txt'}), 'done'])
    events = list(agent.run(RunRequest('read', 'question')))
    assert [item.type for item in events] == [
        'run.started', 'model.started', 'model.completed', 'tool.started', 'tool.completed',
        'checkpoint.completed', 'model.started', 'model.completed', 'checkpoint.completed', 'run.completed']
    assert [item.sequence for item in events] == list(range(1, len(events) + 1))
    assert len({item.event_id for item in events}) == len(events)
    assert events[1].correlation_id == events[2].correlation_id
    assert events[3].correlation_id == events[4].correlation_id
    with pytest.raises(TypeError):
        events[-1].payload['result']['status'] = 'bad'
    with pytest.raises(FrozenInstanceError):
        events[0].sequence = 100
    traces = [json.loads(line) for line in Path(agent.last_result.trace_path).read_text().splitlines()]
    assert [row['public_event']['event_id'] for row in traces if 'public_event' in row] == [event.event_id for event in events]
    assert events[-1].payload['result']['resolved'] is None


@pytest.mark.parametrize('boundary', ['run.started', 'model.started', 'model.completed'])
def test_close_before_tools_cancels_pending_without_effects(tmp_path, boundary):
    agent = build_agent(tmp_path, [writes(), 'done'])
    stream = agent.run(RunRequest('write', 'question'))
    for event in stream:
        if event.type == boundary:
            break
    stream.close()
    assert not (tmp_path / 'a.txt').exists()
    assert not (tmp_path / 'b.txt').exists()
    assert agent.last_result.stop_reason == 'cancelled'
    if boundary == 'model.completed':
        assert agent.gateway.ledger.get(agent.session['id'], 'first')['state'] == 'cancelled'
        assert agent.gateway.ledger.get(agent.session['id'], 'second')['state'] == 'cancelled'
    else:
        assert not agent.model_client.requests
    assert not agent._running


def test_close_after_first_tool_preserves_fact_and_cancels_remaining(tmp_path):
    agent = build_agent(tmp_path, [writes(), 'done'])
    stream = agent.run(RunRequest('write', 'question'))
    for event in stream:
        if event.type == 'tool.completed':
            break
    stream.close()
    assert (tmp_path / 'a.txt').read_text() == 'a'
    assert not (tmp_path / 'b.txt').exists()
    assert agent.gateway.ledger.get(agent.session['id'], 'first')['state'] == 'succeeded'
    assert agent.gateway.ledger.get(agent.session['id'], 'second')['state'] == 'cancelled'
    agent.model_client.outputs = ['continued']
    assert agent.ask('continue') == 'continued'


@pytest.mark.parametrize('component', ['append_trace', 'write_report'])
def test_observation_failure_keeps_executed_fact(tmp_path, monkeypatch, component):
    agent = build_agent(tmp_path, [tool_response('write_file', {'path': 'a.txt', 'content': 'a'}, 'one'), 'done'])
    def fail(*args):
        raise OSError('observer unavailable')
    monkeypatch.setattr(agent.run_store, component, fail)
    result = agent.run_to_completion(RunRequest('write', 'question'))
    assert result.status == 'completed'
    assert (tmp_path / 'a.txt').read_text() == 'a'
    assert agent.gateway.ledger.get(agent.session['id'], 'one')['state'] == 'succeeded'
    assert agent.observation_errors


@pytest.mark.parametrize('component', ['session', 'task_state'])
def test_required_store_failure_prevents_model_and_tools(tmp_path, monkeypatch, component):
    agent = build_agent(tmp_path, [writes()])
    def fail(*args):
        raise SessionError('disk unavailable') if component == 'session' else OSError('disk unavailable')
    monkeypatch.setattr(agent.session_store if component == 'session' else agent.run_store,
                        'save' if component == 'session' else 'write_task_state', fail)
    events = list(agent.run(RunRequest('write', 'question')))
    assert events[-1].type == 'run.failed'
    assert agent.last_result.error
    assert not agent.model_client.requests
    assert not (tmp_path / 'a.txt').exists()


def test_error_terminal_event_and_ask_preserves_exception(tmp_path):
    agent = build_agent(tmp_path, [RuntimeError('provider failed')])
    events = list(agent.run(RunRequest('question')))
    assert [event.type for event in events][-2:] == ['model.failed', 'run.failed']
    assert events[-1].payload['result']['error'] == 'provider failed'
    agent.model_client.outputs = [RuntimeError('provider failed')]
    with pytest.raises(RuntimeError, match='provider failed'):
        agent.ask('again')


@pytest.mark.parametrize('task_type', ['question', 'investigation', 'auto'])
def test_no_change_tasks_complete(tmp_path, task_type):
    agent = build_agent(tmp_path, ['No modification is necessary because the existing behavior is correct.'])
    result = agent.run_to_completion(RunRequest('inspect', task_type))
    assert result.status == 'completed'
    assert result.model_final and result.resolved is None


def test_changed_investigation_rejected_with_bound(tmp_path):
    agent = build_agent(tmp_path, [tool_response('write_file', {'path': 'a.txt', 'content': 'a'}), 'done', 'done'])
    result = agent.run_to_completion(RunRequest('inspect', 'investigation'))
    assert result.status == 'stopped'
    assert result.stop_reason == 'investigation_changed_workspace'
    assert result.attempts == 3


@pytest.mark.parametrize('task_type', ['auto', 'code_change'])
def test_code_change_missing_verification_stops_after_bounded_finals(tmp_path, task_type):
    agent = build_agent(tmp_path, [tool_response('write_file', {'path': 'a.txt', 'content': 'a'}), 'done', 'done'])
    events = list(agent.run(RunRequest('write', task_type)))
    assert sum(event.type == 'run.rejected' for event in events) == 1
    assert agent.last_result.status == 'stopped'
    assert agent.last_result.stop_reason.startswith('verification_missing')
    assert agent.last_result.model_final and agent.last_result.resolved is None


@pytest.mark.parametrize('exit_code', [0, 1])
def test_verification_gateway_facts_and_result(tmp_path, exit_code):
    command = f'{shlex.quote(sys.executable)} -c "import sys; sys.exit({exit_code})"'
    agent = build_agent(tmp_path, [tool_response('write_file', {'path': 'a.txt', 'content': 'a'}), 'done', 'done'])
    result = agent.run_to_completion(RunRequest('write', 'code_change', (command,)))
    assert result.status == ('completed' if exit_code == 0 else 'stopped')
    assert result.verification[-1]['exit_code'] == exit_code
    assert result.verification[-1]['evidence']['workspace_digest']
    assert result.tool_steps == (2 if exit_code == 0 else 3)
    assert agent.run_store.load_report(result.run_id)['tool_counters']['executed'] == result.tool_steps
    completion = agent.run_store.load_task_state(result.run_id)['completion']
    assert completion['model_final']
    assert completion['accepted'] == (exit_code == 0)
    assert completion['verification'][-1]['operation_id'] == result.verification[-1]['operation_id']


def test_final_only_never_runs_verification_or_model_tools(tmp_path):
    agent = build_agent(tmp_path, [tool_response('write_file', {'path': 'a.txt', 'content': 'a'}), 'done'], max_steps=1)
    result = agent.run_to_completion(RunRequest('write', 'code_change', ('echo verified',)))
    assert result.status == 'stopped'
    assert result.stop_reason == 'verification_budget_exhausted'
    assert not result.verification
    assert agent.model_client.requests[-1].tool_choice == "none"


def test_public_event_excludes_raw_arguments_and_secrets(tmp_path, monkeypatch):
    secret = 'private-provider-value'
    monkeypatch.setenv('OPENAI_API_KEY', secret)
    agent = build_agent(tmp_path, [tool_response('write_file', {'path': 'a.txt', 'content': secret}), secret])
    events = list(agent.run(RunRequest('write', 'question')))
    output = json.dumps([event.to_dict() for event in events])
    assert secret not in output
    assert '"args"' not in output
    assert '"result": "wrote' not in output


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


def test_contract_copies_nested_payload():
    original = {'nested': {'values': [1]}}
    event = AgentEvent('run', 1, 'run.started', original, 'run')
    original['nested']['values'].append(2)
    assert event.to_dict()['payload'] == {'nested': {'values': [1]}}


def test_verification_can_reject_then_accept_repaired_workspace(tmp_path):
    # 用单引号包裹代码，避免 shell 先解释代码中的字符串引号。
    command = shlex.join([sys.executable, '-c', 'from pathlib import Path; assert Path("a.txt").read_text() == "b"'])
    agent = build_agent(tmp_path, [tool_response('write_file', {'path': 'a.txt', 'content': 'a'}), 'done',
                                  tool_response('write_file', {'path': 'a.txt', 'content': 'b'}), 'repaired'])
    result = agent.run_to_completion(RunRequest('write', 'code_change', (command,)))
    assert result.status == 'completed'
    assert [item['exit_code'] for item in result.verification] == [1, 0]
    assert result.tool_steps == 4


def test_later_verification_mutation_invalidates_earlier_evidence(tmp_path):
    mutate = shlex.join([sys.executable, '-c', 'from pathlib import Path; Path("a.txt").write_text("changed")'])
    agent = build_agent(tmp_path, ['done'])
    result = agent.run_to_completion(RunRequest('write', 'code_change', ('true', mutate), max_final_rejections=1))
    assert result.status == 'stopped'
    assert result.stop_reason == 'verification_stale'


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


def test_cancel_persistence_failure_is_failed_and_exposes_error(tmp_path, monkeypatch):
    agent = build_agent(tmp_path, [writes()])
    stream = agent.run(RunRequest('write', 'question'))
    for event in stream:
        if event.type == 'model.completed':
            break
    def fail(*args):
        raise SessionError('cancel could not save session')
    monkeypatch.setattr(agent.session_store, 'save', fail)
    with pytest.raises(SessionError):
        stream.close()
    assert agent.last_result.status == 'failed'
    assert agent.last_result.stop_reason == 'cancellation_persistence_error'
    assert not (tmp_path / 'a.txt').exists()
    assert not agent._running


def test_agent_rejects_concurrent_consumer_without_disturbing_active_run(tmp_path):
    agent = build_agent(tmp_path, ['done'])
    stream = agent.run(RunRequest('first'))
    assert next(stream).type == 'run.started'
    with pytest.raises(RuntimeError, match='concurrently'):
        next(agent.run(RunRequest('second')))
    assert list(stream)[-1].type == 'run.completed'
    assert agent.last_result.final_answer == 'done'


def test_initial_workspace_error_still_has_terminal_event(tmp_path, monkeypatch):
    agent = build_agent(tmp_path, ['done'])
    def fail():
        raise OSError('workspace file is unreadable')
    monkeypatch.setattr(agent, 'capture_workspace_snapshot', fail)
    events = list(agent.run(RunRequest('inspect', 'investigation')))
    assert events[-1].type == 'run.failed'
    assert agent.last_result.error == 'workspace file is unreadable'
    assert not agent.model_client.requests
