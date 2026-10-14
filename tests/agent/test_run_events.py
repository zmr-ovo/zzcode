import json



from dataclasses import FrozenInstanceError

from pathlib import Path

import pytest

from zzcode import Agent, AgentEvent, FakeModelClient, RunRequest, SessionStore, WorkspaceContext


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

def test_public_event_excludes_raw_arguments_and_secrets(tmp_path, monkeypatch):
    secret = 'private-provider-value'
    monkeypatch.setenv('OPENAI_API_KEY', secret)
    agent = build_agent(tmp_path, [tool_response('write_file', {'path': 'a.txt', 'content': secret}), secret])
    events = list(agent.run(RunRequest('write', 'question')))
    output = json.dumps([event.to_dict() for event in events])
    assert secret not in output
    assert '"args"' not in output
    assert '"result": "wrote' not in output

def test_contract_copies_nested_payload():
    original = {'nested': {'values': [1]}}
    event = AgentEvent('run', 1, 'run.started', original, 'run')
    original['nested']['values'].append(2)
    assert event.to_dict()['payload'] == {'nested': {'values': [1]}}

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
