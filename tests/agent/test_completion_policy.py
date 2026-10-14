
import shlex

import sys



import pytest

from zzcode import Agent, FakeModelClient, RunRequest, SessionStore, WorkspaceContext


from zzcode.core.messages import tool_response


def build_agent(tmp_path, outputs, **kwargs):
    (tmp_path / 'input.txt').write_text('input\n')
    return Agent(FakeModelClient(outputs), WorkspaceContext.build(tmp_path),
                 SessionStore(tmp_path / '.zzcode/sessions'), approval_policy=kwargs.pop('approval_policy', 'auto'), **kwargs)

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
