"""评测保留原有调用外观，执行实现统一位于 execution。"""
import os
from ...execution.commands import CommandRequest, ExecutorError, ResourceLimits
from ...execution.docker import DockerExecutor
from ..errors import ArtifactError


class DockerToolSandbox(DockerExecutor):
    def __init__(self, workspace, *, image, docker_binary='docker', cpus=1.0, memory_mb=1024, pids_limit=128, tmpfs_mb=256):
        super().__init__(workspace, image=image, docker_binary=docker_binary,
                         limits=ResourceLimits(cpus, memory_mb, pids_limit, tmpfs_mb),
                         secret_values=tuple(value for key, value in os.environ.items() if any(marker in key.upper() for marker in ('API_KEY', 'TOKEN', 'SECRET', 'PASSWORD'))))

    def run_args(self, args, *, on_start=None):
        return self.run(args['command'], timeout_seconds=args.get('timeout', 20), on_start=on_start)

    def run(self, command, *, timeout_seconds, on_start=None):
        try:
            return self.execute(CommandRequest(command, self.workspace, timeout=timeout_seconds), on_start=on_start)
        except ExecutorError as exc:
            raise ArtifactError(str(exc)) from exc
