"""统一命令契约及 Docker 受控工作区；容器策略测试使用假 Engine。"""
import json
import os
import shlex
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

from zzcode import Agent, FakeModelClient, SessionStore, WorkspaceContext
from zzcode import cli
from zzcode.context.compaction import validation_signature
from zzcode.execution.commands import CommandRequest, CommandResult, ExecutorError
from zzcode.execution.docker import DockerExecutor
from zzcode.execution.files import FileConflictError
from zzcode.execution.shell import LocalExecutor
from zzcode.execution.workspace_copy import WorkspaceCopy


def shell(code):
    return shlex.join([sys.executable, '-c', code])


def test_local_result_env_cwd_exit_code_and_output_limit(tmp_path):
    request = CommandRequest(shell('import os; print(os.getcwd()); print(os.environ.get("VISIBLE")); print("x"*10000); raise SystemExit(3)'),
                             tmp_path, {'VISIBLE': 'yes'}, output_limit=512)
    result = LocalExecutor().execute(request)
    assert isinstance(result, CommandResult) and result.exit_code == result.returncode == 3
    assert str(tmp_path) in result.stdout and 'yes' in result.stdout
    assert result.truncated and len(result.stdout) < 600
    assert result.resource_status == 'completed'
    with pytest.raises(TypeError):
        request.env['NEW'] = 'bad'


@pytest.mark.parametrize('cancelled', [False, True])
def test_local_timeout_or_cancel_reaps_process_group(tmp_path, cancelled):
    marker = tmp_path / 'escaped'
    command = shell('import subprocess,time; subprocess.Popen([' + repr(sys.executable) + ',"-c",' + repr(f'import time; from pathlib import Path; time.sleep(.8); Path({str(marker)!r}).write_text("bad")') + ']); time.sleep(5)')
    started = time.monotonic()
    request = CommandRequest(command, tmp_path, os.environ.copy(), timeout=.15 if not cancelled else 3,
                             cancel=(lambda: time.monotonic() - started > .15) if cancelled else None)
    result = LocalExecutor().execute(request)
    assert result.cancelled is cancelled
    assert result.timed_out is not cancelled
    time.sleep(.85)
    assert not marker.exists()


def test_pre_cancel_does_not_start_process(tmp_path):
    result = LocalExecutor().execute(CommandRequest('touch bad', tmp_path, cancel=lambda: True))
    assert result.cancelled and not (tmp_path / 'bad').exists()


def test_controlled_copy_excludes_credentials_and_synchronizes_files_dirs_modes(tmp_path):
    (tmp_path / '.env').write_text('API_KEY=private')
    (tmp_path / '.zzcode').mkdir()
    (tmp_path / '.zzcode/session.json').write_text('private')
    (tmp_path / 'old.txt').write_text('old')
    with WorkspaceCopy(tmp_path) as copy:
        assert not (copy.path / '.env').exists()
        assert not (copy.path / '.zzcode').exists()
        (copy.path / 'old.txt').unlink()
        (copy.path / 'new').mkdir()
        (copy.path / 'new/script').write_text('new')
        (copy.path / 'new/script').chmod(0o755)
        copy.sync()
        clone = copy.path
    assert not clone.exists()
    assert not (tmp_path / 'old.txt').exists()
    assert (tmp_path / 'new/script').read_text() == 'new'
    assert (tmp_path / 'new/script').stat().st_mode & 0o777 == 0o755
    assert (tmp_path / '.env').read_text() == 'API_KEY=private'


@pytest.mark.parametrize('unsafe', ['symlink', 'socket', 'credential'])
def test_copy_rejects_unsafe_workspace(tmp_path, unsafe):
    if unsafe == 'symlink':
        (tmp_path / 'link').symlink_to('/etc/passwd')
    elif unsafe == 'socket':
        os.mkfifo(tmp_path / 'pipe')
    else:
        (tmp_path / 'source.py').write_text('configured-private-key')
    with pytest.raises(ExecutorError):
        with WorkspaceCopy(tmp_path, ('configured-private-key',)):
            pass


def test_copy_conflict_is_checked_before_any_writeback(tmp_path):
    (tmp_path / 'a').write_text('a')
    (tmp_path / 'b').write_text('b')
    with WorkspaceCopy(tmp_path) as copy:
        (copy.path / 'a').write_text('new a')
        (copy.path / 'b').write_text('new b')
        (tmp_path / 'b').write_text('user edit')
        with pytest.raises(FileConflictError):
            copy.sync()
    assert (tmp_path / 'a').read_text() == 'a'
    assert (tmp_path / 'b').read_text() == 'user edit'


def fake_engine(executor, monkeypatch, *, state=None, policy_drift=False, run=None, cleanup_fail=False, image_env=()):
    calls = []
    clone = None
    state = state or {'ExitCode': 0, 'OOMKilled': False, 'Running': False}
    def docker(args, *, timeout):
        nonlocal clone
        calls.append(args)
        if args[0] == 'cp':
            return subprocess.CompletedProcess(args, 1, '', 'Could not find the file')
        if args[0] == 'create' and '--mount' not in args:
            return subprocess.CompletedProcess(args, 0, 'audit', '')
        if args[:2] == ['image', 'inspect']:
            text = json.dumps({'Id': 'sha256:' + '1'*64, 'Config': {'Env': list(image_env)}})
        elif args[0] == 'create':
            clone = Path(args[args.index('--mount')+1].split('src=',1)[1].split(',dst=',1)[0])
            text = 'container'
        elif args[0] == 'inspect':
            text = json.dumps([{'State': state, 'Config': {'User': '501:20'},
                    'HostConfig': {'NetworkMode': 'bridge' if policy_drift else 'none', 'ReadonlyRootfs': True,
                     'Memory': 1024*1024*1024, 'MemorySwap': 1024*1024*1024, 'NanoCpus': 1000000000,
                     'PidsLimit': 128, 'CapDrop': ['ALL'], 'SecurityOpt': ['no-new-privileges:true'], 'Tmpfs': {'/tmp':'rw'}},
                    'Mounts': [{'Type':'bind', 'Source':str(clone), 'Destination':'/workspace', 'RW':True}]}])
        else:
            text = ''
        return subprocess.CompletedProcess(args, 1 if cleanup_fail and args[0]=='rm' and args[-1]=='container' else 0, text, 'cleanup failed' if cleanup_fail else '')
    def execute(request, **kwargs):
        if run:
            return run(request, clone)
        (clone / 'result.txt').write_text('done')
        return CommandResult(0, 'ok', '', command=request.command)
    monkeypatch.setattr(executor, '_docker', docker)
    monkeypatch.setattr(executor.local, 'execute', execute)
    return calls


def test_docker_result_same_contract_mount_copy_pin_and_env_boundary(tmp_path, monkeypatch):
    (tmp_path / '.env').write_text('private-provider-key')
    executor = DockerExecutor(tmp_path, image='tool:test', secret_values=('private-provider-key',))
    calls = fake_engine(executor, monkeypatch)
    result = executor.execute(CommandRequest('true', tmp_path, {'OPENAI_API_KEY':'private-provider-key','LANG':'C'}))
    assert result.backend == 'docker' and result.exit_code == 0
    assert result.image_digest == 'sha256:' + '1'*64
    assert (tmp_path / 'result.txt').read_text() == 'done'
    create = next(call for call in calls if call[0]=='create' and '--mount' in call)
    assert create[create.index('--user')+1] not in {'0','0:0','root'}
    assert create[-3] == result.image_digest
    assert not any('private-provider-key' in item for item in create)
    assert str(tmp_path) not in create[create.index('--mount')+1]
    assert ['rm','--force','--volumes','container'] in calls


@pytest.mark.parametrize('drift', ['network', 'image_credential'])
def test_docker_rejects_policy_drift_before_start(tmp_path, monkeypatch, drift):
    executor = DockerExecutor(tmp_path, image='tool:test')
    calls = fake_engine(executor, monkeypatch, policy_drift=drift=='network', image_env=('API_KEY=bad',) if drift=='image_credential' else ())
    with pytest.raises(ExecutorError):
        executor.execute(CommandRequest('true', tmp_path))
    assert not (tmp_path / 'result.txt').exists()
    if drift == 'image_credential':
        assert not any(call[0]=='create' for call in calls)


@pytest.mark.parametrize('status', ['oom', 'timed_out', 'cancelled'])
def test_docker_resource_result_and_cleanup(tmp_path, monkeypatch, status):
    executor = DockerExecutor(tmp_path, image='tool:test')
    def run(request, clone):
        (clone / 'partial').write_text('partial')
        return CommandResult(137, '', '', timed_out=status=='timed_out', cancelled=status=='cancelled', resource_status=status)
    calls = fake_engine(executor, monkeypatch, state={'ExitCode':137,'OOMKilled':status=='oom','Running':False}, run=run)
    result = executor.execute(CommandRequest('true', tmp_path))
    assert result.resource_status == status
    assert (tmp_path / 'partial').read_text() == 'partial'
    assert calls[-1] == ['rm','--force','--volumes','container']


def test_cleanup_failure_keeps_ledger_unknown(tmp_path, monkeypatch):
    executor = DockerExecutor(tmp_path, image='tool:test')
    fake_engine(executor, monkeypatch, cleanup_fail=True)
    agent = Agent(FakeModelClient([]), WorkspaceContext.build(tmp_path), SessionStore(tmp_path/'.zzcode/sessions'), executor=executor, approval_policy='auto')
    result = agent.run_tool('run_shell', {'command':'true'}, call_id='cleanup')
    assert result.status in {'partial_success','unknown'}
    assert result.error_code == 'cleanup_failed'
    assert agent.gateway.ledger.get(agent.session['id'], 'cleanup')['state'] == result.status


def test_backend_changes_invalidate_validation_evidence(tmp_path):
    agent = Agent(FakeModelClient([]), WorkspaceContext.build(tmp_path), SessionStore(tmp_path/'.zzcode/sessions'))
    before = validation_signature(agent)
    agent.executor = DockerExecutor(tmp_path, image='test')
    agent.executor.image_digest = 'sha256:'+'1'*64
    assert validation_signature(agent) != before


def test_cli_docker_blocked_without_fallback(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, '_load_env_files', lambda cwd:None)
    assert cli.main(['--cwd',str(tmp_path),'--executor','docker','--output','jsonl','hello']) == 2
    out = capsys.readouterr()
    assert out.out == '' and '--docker-image' in out.err


def test_request_cwd_cannot_escape_docker_workspace(tmp_path):
    executor = DockerExecutor(tmp_path, image='test')
    with pytest.raises(ValueError):
        executor.execute(CommandRequest('true', tmp_path.parent))


def test_image_credential_file_rejected_before_tool_execution(tmp_path, monkeypatch):
    executor = DockerExecutor(tmp_path, image='test')
    calls = fake_engine(executor, monkeypatch)
    original = executor._docker
    def docker(args, *, timeout):
        if args[0] == 'cp':
            return subprocess.CompletedProcess(args, 0, 'credential file exists', '')
        return original(args, timeout=timeout)
    monkeypatch.setattr(executor, '_docker', docker)
    with pytest.raises(ExecutorError, match='credential file'):
        executor.execute(CommandRequest('true', tmp_path))
    assert not any('--mount' in call for call in calls)
    assert ['rm','--force','--volumes','audit'] in calls


def test_agent_rejects_executor_bound_to_other_workspace(tmp_path):
    parent = tmp_path / 'parent'
    child = parent / 'child'
    child.mkdir(parents=True)
    with pytest.raises(ValueError, match='must match'):
        Agent(FakeModelClient([]), WorkspaceContext.build(child, repo_root_override=child), SessionStore(child/'.zzcode/sessions'),
              executor=DockerExecutor(parent, image='test'))


def test_private_output_rejected_before_any_synchronization(tmp_path):
    with WorkspaceCopy(tmp_path) as copy:
        (copy.path/'normal.txt').write_text('normal')
        (copy.path/'.env').write_text('new private state')
        with pytest.raises(ExecutorError,match='private path'):
            copy.sync()
    assert not (tmp_path/'normal.txt').exists()
    assert not (tmp_path/'.env').exists()


def test_docker_sdk_discovers_provider_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY','a-live-configured-provider-key')
    executor = DockerExecutor(tmp_path,image='test')
    assert 'a-live-configured-provider-key' in executor.secret_values


def test_directory_permission_change_is_rejected_before_writeback(tmp_path):
    directory = tmp_path / "nested"
    directory.mkdir(mode=0o750)
    target = directory / "file.txt"
    target.write_text("before")
    with WorkspaceCopy(tmp_path) as copy:
        assert stat.S_IMODE((copy.path / "nested").stat().st_mode) == 0o750
        (copy.path / "nested" / "file.txt").write_text("after")
        (copy.path / "nested").chmod(0o755)
        with pytest.raises(ExecutorError, match="directory permission"):
            copy.sync()
    assert target.read_text() == "before"
