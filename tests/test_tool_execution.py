"""验证执行事实、持久化顺序和恢复行为，而不是只检查展示文本。"""
import json
import os
import subprocess
import sys

import pytest

from zzcode import FakeModelClient, MiniAgent, SessionStore, WorkspaceContext
from zzcode.core.messages import tool_response
from zzcode.execution.ledger import OperationBusyError, PersistenceError


def agent_at(path, outputs=(), **kwargs):
    return MiniAgent(FakeModelClient(outputs), WorkspaceContext.build(path),
                     SessionStore(path / '.zzcode' / 'sessions'), approval_policy='auto', **kwargs)


class Crash(BaseException):
    pass


def crash_at(agent, stage):
    def hook(current, operation_id):
        if current == stage:
            raise Crash(stage)
    agent.gateway.fault_hook = hook


@pytest.mark.parametrize('stage,status,written', [
    ('after_planned', 'cancelled', False), ('after_running', 'failed', False),
    ('after_replace', 'succeeded', True), ('before_terminal', 'succeeded', True),
])
def test_crash_stages_reconcile_full_file_hash(tmp_path, stage, status, written):
    agent = agent_at(tmp_path)
    crash_at(agent, stage)
    with pytest.raises(Crash):
        agent.run_tool('write_file', {'path': 'file.txt', 'content': 'new'}, call_id='original')
    recovered = agent_at(tmp_path)
    row = recovered.gateway.ledger.get(agent.session['id'], 'original')
    assert row['state'] == status
    assert (tmp_path / 'file.txt').exists() is written
    if written:
        assert (tmp_path / 'file.txt').read_text() == 'new'
        assert json.loads(row['details'])['reconciliation']['observed_hash'] == json.loads(row['details'])['after_hash']


def test_real_process_exit_after_replace(tmp_path):
    source = """
import os,sys
from pathlib import Path
from zzcode import FakeModelClient,MiniAgent,SessionStore,WorkspaceContext
p=Path(sys.argv[1]);a=MiniAgent(FakeModelClient([]),WorkspaceContext.build(p),SessionStore(p/'.zzcode'/'sessions'),approval_policy='auto')
a.gateway.fault_hook=lambda stage,op: os._exit(71) if stage=='after_replace' else None
a.run_tool('write_file',{'path':'file.txt','content':'durable'},call_id='killed')
"""
    result = subprocess.run([sys.executable, '-c', source, str(tmp_path)], cwd=os.getcwd())
    assert result.returncode == 71
    recovered = agent_at(tmp_path)
    row = recovered.gateway.ledger.connection.execute("SELECT * FROM operations WHERE call_id='killed'").fetchone()
    assert row['state'] == 'succeeded'
    assert (tmp_path / 'file.txt').read_text() == 'durable'


@pytest.mark.parametrize('method', ['plan', 'start'])
def test_persistence_failure_prevents_effect(tmp_path, monkeypatch, method):
    agent = agent_at(tmp_path)
    def failure(*args):
        raise PersistenceError('injected commit failure')
    monkeypatch.setattr(agent.gateway.ledger, method, failure)
    with pytest.raises(PersistenceError):
        agent.run_tool('write_file', {'path': 'file.txt', 'content': 'new'})
    assert not (tmp_path / 'file.txt').exists()


def test_terminal_commit_failure_stops_run_and_recovers(tmp_path, monkeypatch):
    agent = agent_at(tmp_path, [tool_response('write_file', {'path': 'file.txt', 'content': 'new'})])
    def failure(result):
        raise PersistenceError('terminal commit failure')
    monkeypatch.setattr(agent.gateway.ledger, 'finish', failure)
    with pytest.raises(PersistenceError):
        agent.ask('write file')
    assert agent.current_task_state.stop_reason == 'persistence_error'
    assert (tmp_path / 'file.txt').read_text() == 'new'
    assert agent.gateway.ledger.unsettled()[0]['state'] == 'running'
    recovered = agent_at(tmp_path)
    assert recovered.gateway.ledger.unsettled() == []


def test_approval_cannot_change_parameters(tmp_path):
    agent = agent_at(tmp_path)
    def approve(name, args):
        args['content'] = 'changed'
        return True
    agent.approve = approve
    result = agent.run_tool('write_file', {'path': 'file.txt', 'content': 'original'})
    assert result.error_code == 'approval_parameters_changed'
    assert not (tmp_path / 'file.txt').exists()


def test_concurrent_edit_is_not_overwritten(tmp_path):
    path = tmp_path / 'file.txt'
    path.write_text('before')
    agent = agent_at(tmp_path)
    agent.gateway.fault_hook = lambda stage, op: path.write_text('human edit') if stage == 'after_running' else None
    result = agent.run_tool('write_file', {'path': 'file.txt', 'content': 'after'})
    assert result.error_code == 'file_conflict'
    assert path.read_text() == 'human edit'


def test_recovery_conflict_blocks_writes_until_explicit_resolution(tmp_path):
    agent = agent_at(tmp_path)
    crash_at(agent, 'after_replace')
    with pytest.raises(Crash):
        agent.run_tool('write_file', {'path': 'file.txt', 'content': 'after'})
    (tmp_path / 'file.txt').write_text('human edit')
    recovered = agent_at(tmp_path)
    pending = recovered.gateway.ledger.unsettled()[0]
    assert pending['state'] == 'unknown'
    assert recovered.run_tool('write_file', {'path': 'other.txt', 'content': 'new'}).error_code == 'recovery_required'
    assert recovered.run_tool('read_file', {'path': 'file.txt'}).status == 'succeeded'
    recovered.gateway.ledger.resolve(pending['operation_id'], 'failed', 'Human edit preserved; reviewed file.')
    assert recovered.run_tool('write_file', {'path': 'other.txt', 'content': 'new'}).status == 'succeeded'
    assert (tmp_path / 'file.txt').read_text() == 'human edit'


def test_same_call_replays_but_new_call_is_new_intent(tmp_path):
    agent = agent_at(tmp_path)
    args = {'command': "printf x >> file.txt"}
    first = agent.run_tool('run_shell', args, call_id='one')
    replay = agent.run_tool('run_shell', args, call_id='one')
    assert replay == first
    assert (tmp_path / 'file.txt').read_text() == 'x'
    second = agent.run_tool('run_shell', args, call_id='two')
    assert second.operation_id != first.operation_id
    assert (tmp_path / 'file.txt').read_text() == 'xx'
    with pytest.raises(ValueError):
        agent.run_tool('run_shell', {'command': 'false'}, call_id='one')


def test_exit_code_comes_from_executor(tmp_path):
    result = agent_at(tmp_path).run_tool('run_shell', {'command': "printf 'exit_code: 0'; exit 7"})
    assert result.exit_code == 7
    assert result.status == 'failed'


@pytest.mark.parametrize('write', [False, True])
def test_timeout_is_uncertain_and_not_automatically_retried(tmp_path, write):
    agent = agent_at(tmp_path)
    command = ('printf x > file.txt; ' if write else '') + 'sleep 10'
    result = agent.run_tool('run_shell', {'command': command, 'timeout': 1}, call_id='timeout')
    assert result.timed_out
    assert result.status == ('partial_success' if write else 'unknown')
    assert result.affected_paths == (('file.txt',) if write else ())
    details = json.loads(agent.gateway.ledger.get(agent.session['id'], 'timeout')['details'])
    with pytest.raises(ProcessLookupError):
        os.killpg(details['pid'], 0)
    recovered = agent_at(tmp_path)
    assert len(recovered.gateway.ledger.unsettled()) == 1
    assert recovered.run_tool('run_shell', {'command': 'echo new'}).error_code == 'recovery_required'


def test_another_writer_cannot_execute(tmp_path):
    first = agent_at(tmp_path)
    second = agent_at(tmp_path)
    with first.gateway.ledger.writer():
        with pytest.raises(OperationBusyError):
            second.run_tool('write_file', {'path': 'file.txt', 'content': 'new'})
    assert not (tmp_path / 'file.txt').exists()


def test_active_process_prevents_recovery(tmp_path):
    agent = agent_at(tmp_path)
    process = subprocess.Popen(['sleep', '20'], start_new_session=True)
    ledger = agent.gateway.ledger
    try:
        with ledger.writer():
            op = ledger.plan(agent.session['id'], 'manual', 'live', 'run_shell', 'hash', 'manual', {'pid': process.pid})
            ledger.start(op)
        with pytest.raises(OperationBusyError):
            agent_at(tmp_path)
    finally:
        os.killpg(process.pid, 9)
        process.wait()
    recovered = agent_at(tmp_path)
    assert recovered.gateway.ledger.unsettled()[0]['state'] == 'unknown'


def test_large_output_is_bounded_redacted_and_readable(tmp_path):
    agent = agent_at(tmp_path)
    result = agent.run_tool('run_shell', {'command': "printf 'sk-testsecret123\\n'; yes line | head -n 50000; printf TAIL"})
    assert result.truncated
    assert len(result.content) < 3200
    assert 'TAIL' in result.content
    assert result.artifact_refs
    artifact = agent.gateway.artifacts.root / (result.artifact_refs[0] + '.txt')
    assert artifact.stat().st_size <= 65536
    assert 'sk-testsecret123' not in artifact.read_text()
    read = agent.run_tool('read_artifact', {'artifact_id': result.artifact_refs[0], 'end': 5})
    assert read.status == 'succeeded'
    assert '<redacted>' in read.content
    assert agent.run_tool('read_artifact', {'artifact_id': '../secret'}).status == 'failed'


def test_rejected_secrets_are_not_stored_in_ledger(tmp_path):
    agent = agent_at(tmp_path)
    result = agent.run_tool('write_file', {'path': '../escape', 'content': 'sk-testsecret123'})
    assert result.status == 'rejected'
    assert b'sk-testsecret123' not in agent.gateway.ledger.path.read_bytes()
    assert agent.gateway.ledger.counts('manual')['executed'] == 0


def test_read_only_public_method_cannot_bypass_gateway(tmp_path):
    agent = agent_at(tmp_path, read_only=True)
    agent.approve = lambda *args: True
    assert agent.tool_write_file({'path': 'file.txt', 'content': 'new'}).status == 'rejected'
    assert not (tmp_path / 'file.txt').exists()


def test_new_user_request_can_repeat_tool(tmp_path):
    (tmp_path / 'file.txt').write_text('value')
    response = tool_response('read_file', {'path': 'file.txt'})
    agent = agent_at(tmp_path, [response, 'done', response, 'done'])
    assert agent.ask('read') == 'done'
    assert agent.ask('read again') == 'done'
    assert all(item['message']['content'][0]['status'] == 'succeeded' for item in agent.session['history'] if item['role'] == 'tool')


def test_global_time_budget_stops_before_model_call(tmp_path):
    agent = agent_at(tmp_path, ['unused'], max_run_seconds=0)
    agent.ask('read')
    assert agent.current_task_state.stop_reason == 'time_limit'
    assert not agent.model_client.prompts


def test_uncommitted_effect_blocks_new_operation_in_same_process(tmp_path, monkeypatch):
    agent = agent_at(tmp_path)
    original = agent.gateway.ledger.finish
    monkeypatch.setattr(agent.gateway.ledger, 'finish', lambda result: (_ for _ in ()).throw(PersistenceError('commit failed')))
    with pytest.raises(PersistenceError):
        agent.run_tool('write_file', {'path': 'first.txt', 'content': 'new'})
    monkeypatch.setattr(agent.gateway.ledger, 'finish', original)
    assert agent.run_tool('write_file', {'path': 'second.txt', 'content': 'new'}).error_code == 'recovery_required'
    assert not (tmp_path / 'second.txt').exists()


def test_executor_error_with_changes_stays_unresolved(tmp_path):
    agent = agent_at(tmp_path)
    def broken(args, **kwargs):
        (tmp_path / 'file.txt').write_text('partial')
        raise RuntimeError('executor lost status')
    agent.tools['run_shell']['run'] = broken
    result = agent.run_tool('run_shell', {'command': 'example'})
    assert result.status == 'partial_success'
    assert result.error_code == 'recovery_required'
    assert len(agent.gateway.ledger.unsettled()) == 1
    assert agent.run_tool('write_file', {'path': 'other.txt', 'content': 'new'}).error_code == 'recovery_required'
