"""Docker 执行统一请求；受控副本与显式回传隔离容器和宿主工作区。"""
import json
import os
import shlex
import subprocess
import time
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

from .commands import CommandRequest, CommandResult, ExecutorError, ResourceLimits
from .docker_policy import IMAGE_ID, SECRET_ENV, isolation_arguments, isolation_violations
from .shell import LocalExecutor
from .workspace_copy import WorkspaceCopy


class DockerExecutor:
    backend = 'docker'

    def __init__(self, workspace, *, image, docker_binary='docker', limits=None, secret_values=()):
        self.workspace = Path(workspace).resolve()
        if not self.workspace.is_dir() or not image.strip():
            raise ValueError('Docker requires an existing workspace and explicit image')
        self.image, self.docker_binary = image, docker_binary
        self.limits = limits or ResourceLimits()
        self.secret_values = tuple(set(value for value in (*secret_values, *(value for key, value in os.environ.items() if SECRET_ENV.search(key))) if len(value) >= 4))
        self.image_digest = ''
        self.local = LocalExecutor()

    def prepare(self, timeout=30):
        if self.image_digest:
            return self.image_digest
        deadline = time.monotonic() + timeout
        response = self._docker(['image', 'inspect', self.image, '--format', '{{json .}}'], timeout=timeout)
        if response.returncode != 0:
            raise ExecutorError('cannot resolve immutable tool image: ' + response.stderr.strip(), status='blocked')
        data = json.loads(response.stdout)
        digest = data.get('Id', '')
        if not IMAGE_ID.fullmatch(digest):
            raise ExecutorError('Docker image has no immutable sha256 image ID')
        for entry in data.get('Config', {}).get('Env') or []:
            key, _, value = entry.partition('=')
            if SECRET_ENV.search(key) or any(secret in value for secret in self.secret_values):
                raise ExecutorError('Docker image configuration contains a credential variable: ' + key)
        self._audit_image(digest, data, deadline)
        self.image_digest = digest
        return digest

    def _audit_image(self, digest, data, deadline):
        # 容器不启动、没有宿主挂载；只检查镜像中的常见凭证位置。
        def remaining():
            value = deadline - time.monotonic()
            if value <= 0:
                raise ExecutorError('image admission time budget exhausted')
            return value
        created = self._docker(['create', '--network', 'none', '--entrypoint', '/bin/true', digest], timeout=remaining())
        if created.returncode or not created.stdout.strip():
            raise ExecutorError('cannot create stopped container for image admission')
        container = created.stdout.strip()
        homes = {'/root'}
        for value in (data.get('Config') or {}).get('Env') or []:
            if value.startswith('HOME=') and value[5:].startswith('/'):
                homes.add(value[5:])
        workdir = (data.get('Config') or {}).get('WorkingDir') or '/'
        paths = {str(Path(workdir) / '.env')}
        for home in homes:
            paths.update(str(Path(home) / name) for name in ('.env', '.netrc', '.aws/credentials', '.ssh/id_rsa', '.ssh/id_ed25519'))
        try:
            for path in sorted(paths):
                result = self._docker(['cp', f'{container}:{path}', '-'], timeout=remaining())
                if result.returncode == 0:
                    raise ExecutorError('Docker image contains a credential file: ' + path)
                if 'Could not find the file' not in result.stderr:
                    raise ExecutorError('cannot verify Docker image credential boundary')
        finally:
            self._checked(['rm', '--force', '--volumes', container], timeout=30)

    def execute(self, request, *, on_start=None):
        started = time.monotonic()
        if request.cancel and request.cancel():
            return CommandResult(None, "", "", cancelled=True, command=request.command, backend="docker", resource_status="cancelled")
        relative = request.cwd.relative_to(self.workspace)
        command = request.command if isinstance(request.command, str) else shlex.join(request.command)
        if any(secret in command for secret in self.secret_values):
            raise ExecutorError('Docker command contains a configured credential')
        image = self.prepare(timeout=min(30, request.timeout))
        env = {key: value for key, value in request.env.items() if key in {'LANG', 'LC_ALL', 'LC_CTYPE', 'TZ', 'TERM'}}
        if any(secret in value for value in env.values() for secret in self.secret_values):
            raise ExecutorError('Docker environment contains a configured credential')
        with WorkspaceCopy(self.workspace, self.secret_values) as workspace:
            uid, gid = os.getuid(), os.getgid()
            user = f'{uid}:{gid}' if uid else '65532:65532'
            args = isolation_arguments('zzcode-tool-' + uuid4().hex[:12], self.limits, user=user,
                                       cwd='/workspace' + ('/' + relative.as_posix() if relative.parts else ''), env=env)
            args += ['--mount', f'type=bind,src={workspace.path},dst=/workspace', '--entrypoint', '/bin/sh', image, '-lc', command]
            remaining = request.timeout - (time.monotonic() - started)
            if remaining <= 0:
                raise ExecutorError("Docker preparation exhausted the command time budget")
            created = self._docker(args, timeout=min(60, remaining))
            if created.returncode or not created.stdout.strip():
                raise ExecutorError('Docker container create failed: ' + created.stderr.strip(), status='blocked')
            container = created.stdout.strip()
            try:
                if on_start:
                    on_start({'container_id': container, 'docker_binary': self.docker_binary})
                inspection = self._inspect(container)
                violations = isolation_violations(inspection, limits=self.limits, mounts={'/workspace': (workspace.path, True)})
                if violations:
                    raise ExecutorError('container isolation policy mismatch: ' + ', '.join(violations))
                remaining = request.timeout - (time.monotonic() - started)
                if remaining <= 0:
                    raise ExecutorError('Docker preparation exhausted the command time budget')
                result = self.local.execute(CommandRequest((self.docker_binary, 'start', '--attach', container), self.workspace,
                                                          os.environ.copy(), remaining, request.output_limit, request.cancel), on_start=on_start)
                # 先停容器再检查状态/回传；停止 attach 客户端不会自动停止容器。
                if result.timed_out or result.cancelled:
                    if self._inspect(container).get('State', {}).get('Running') is True:
                        self._checked(['kill', container], timeout=30)
                state = self._inspect(container).get('State', {})
                if state.get('Running') is not False:
                    raise ExecutorError('container did not stop; workspace cannot be synchronized', executed=True)
                status = 'oom' if state.get('OOMKilled') is True else result.resource_status
                exit_code = None if result.timed_out or result.cancelled else state.get('ExitCode')
                if exit_code is not None and (not isinstance(exit_code, int) or isinstance(exit_code, bool)):
                    raise ExecutorError('Docker returned invalid container exit code', executed=True)
                workspace.sync()
                return replace(result, command=request.command, exit_code=exit_code, backend='docker', resource_status=status,
                               container_id=container, image_digest=image, duration_seconds=time.monotonic() - started)
            finally:
                cleanup = self._docker(['rm', '--force', '--volumes', container], timeout=30)
                removed = cleanup.returncode == 0 or 'No such container' in cleanup.stderr
                if on_start:
                    on_start({'container_removed': removed})
                if not removed:
                    raise ExecutorError('container cleanup failed: ' + cleanup.stderr.strip(), status='cleanup_failed', executed=True)

    def _checked(self, args, *, timeout):
        value = self._docker(args, timeout=timeout)
        if value.returncode:
            raise ExecutorError('Docker lifecycle command failed: ' + value.stderr.strip(), executed=True)
        return value

    def _inspect(self, container):
        rows = json.loads(self._checked(['inspect', container], timeout=30).stdout)
        if not isinstance(rows, list) or len(rows) != 1:
            raise ExecutorError('Docker returned invalid container inspection data', executed=True)
        return rows[0]

    def _docker(self, args, *, timeout):
        try:
            result = self.local.execute(CommandRequest((self.docker_binary, *args), self.workspace, os.environ.copy(), timeout, 524288))
        except OSError as exc:
            raise ExecutorError('Docker is unavailable: ' + str(exc), status='blocked') from exc
        if result.timed_out or result.cancelled or result.truncated:
            raise ExecutorError('Docker lifecycle command did not complete', status='blocked')
        return subprocess.CompletedProcess(args, result.exit_code, result.stdout, result.stderr)
