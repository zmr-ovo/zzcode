"""真实容器验收；使用预先准备的镜像，不拉取依赖或启用网络。"""
import json
import os
import shlex
import time

import pytest

from zzcode import Agent, FakeModelClient, RunRequest, SessionStore, WorkspaceContext
from zzcode.core.messages import tool_response
from zzcode.execution import CommandRequest, DockerExecutor, ResourceLimits

pytestmark = [pytest.mark.docker, pytest.mark.skipif(os.environ.get('RUN_DOCKER_TESTS') != '1', reason='requires explicit Docker test opt-in')]


def executor(root, **kwargs):
    value = DockerExecutor(root, image=os.environ.get('ZZCODE_EVAL_IMAGE', 'zzcode-eval-py313:phase4'), **kwargs)
    value.prepare()
    return value


def command(code):
    return shlex.join(['python', '-c', code])


def test_real_docker_credentials_output_and_nested_cwd(tmp_path):
    (tmp_path / '.env').write_text('OPENAI_API_KEY=private-provider-value')
    nested = tmp_path / 'subdir'
    nested.mkdir()
    runner = executor(tmp_path, secret_values=('private-provider-value',))
    code = 'import os,json; from pathlib import Path; print(json.dumps({"cwd":os.getcwd(),"key":os.getenv("OPENAI_API_KEY"),"env_file":Path("../.env").exists()})); print("x"*20000)'
    result = runner.execute(CommandRequest(command(code), nested, {'OPENAI_API_KEY':'private-provider-value'}, timeout=10, output_limit=512))
    public = json.loads(result.stdout.splitlines()[0])
    assert public == {'cwd':'/workspace/subdir','key':None,'env_file':False}
    assert result.exit_code == 0 and result.truncated
    assert len(result.stdout) < 600 and 'private-provider-value' not in result.stdout


@pytest.mark.parametrize('kind', ['oom','timeout','cancel'])
def test_real_docker_resource_status_and_no_orphan(tmp_path, kind):
    runner = executor(tmp_path, limits=ResourceLimits(memory_mb=64,pids_limit=32,tmpfs_mb=16))
    started = time.monotonic()
    code = 'x=bytearray(256*1024*1024)' if kind=='oom' else 'import time; time.sleep(20)'
    result = runner.execute(CommandRequest(command(code), tmp_path, timeout=1 if kind=='timeout' else 10,
                                          cancel=(lambda: time.monotonic()-started>.5) if kind=='cancel' else None))
    assert result.resource_status == {'oom':'oom','timeout':'timed_out','cancel':'cancelled'}[kind]
    inspection = runner._docker(['inspect',result.container_id],timeout=10)
    assert inspection.returncode != 0
    assert result.cleanup_complete


def test_real_agent_shell_and_file_tools_share_current_workspace(tmp_path):
    runner = executor(tmp_path)
    model = FakeModelClient([tool_response('run_shell', {'command':command('from pathlib import Path; Path("value.txt").write_text("2")') }),
                             tool_response('read_file', {'path':'value.txt'}), 'done'])
    agent = Agent(model,WorkspaceContext.build(tmp_path,repo_root_override=tmp_path),SessionStore(tmp_path/'.zzcode/sessions'),
                  executor=runner,approval_policy='auto')
    verify = command('from pathlib import Path; assert Path("value.txt").read_text()=="2"')
    result = agent.run_to_completion(RunRequest('modify and verify','code_change',(verify,)))
    assert result.status == 'completed' and result.resolved is None
    assert (tmp_path/'value.txt').read_text() == '2'
    assert any('2' in message['content'] for message in agent.session['history'] if message['role']=='tool' and message['name']=='read_file')
    assert result.verification[-1]['evidence']['image_digest']==runner.image_digest
