"""POSIX Shell 执行：有限输出、进程组超时和真实退出码。"""

import os
import selectors
import signal
import subprocess
import time
from .commands import CommandRequest, CommandResult

from .output import BoundedOutput

ShellOutcome = CommandResult  # 旧工具结果名称保留为同一类型。


def _execute(request, *, on_start=None):
    command, cwd, env, timeout = request.command, request.cwd, dict(request.env), request.timeout
    started = time.monotonic()
    if request.cancel and request.cancel():
        return CommandResult(None, "", "", cancelled=True, command=command, resource_status="cancelled")
    process = subprocess.Popen(command, shell=isinstance(command, str), cwd=cwd, env=env, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, start_new_session=True)
    outputs = {process.stdout: BoundedOutput(request.output_limit), process.stderr: BoundedOutput(request.output_limit)}
    timed_out = False
    cancelled = False
    try:
        # PID 先写入账本；保存失败必须终止刚启动的进程并中止 Run。
        if on_start:
            on_start(process.pid)
        deadline = time.monotonic() + timeout
        with selectors.DefaultSelector() as selector:
            for pipe in outputs:
                os.set_blocking(pipe.fileno(), False)
                selector.register(pipe, selectors.EVENT_READ)
            while selector.get_map():
                if request.cancel and request.cancel():
                    cancelled = True
                    break
                if time.monotonic() >= deadline:
                    timed_out = True
                    break
                for key, _ in selector.select(min(0.1, max(0, deadline-time.monotonic()))):
                    chunk = os.read(key.fileobj.fileno(), 8192)
                    if chunk:
                        outputs[key.fileobj].append(chunk)
                    else:
                        selector.unregister(key.fileobj)
        if not timed_out and not cancelled:
            try:
                process.wait(timeout=max(0.001, deadline-time.monotonic()))
            except subprocess.TimeoutExpired:
                timed_out = True
    except KeyboardInterrupt:
        cancelled = True
    finally:
        # 终止整个进程组，避免超时后子进程继续修改工作区。
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
        for pipe in outputs:
            pipe.close()
    stdout, stderr = outputs.values()
    return CommandResult(process.returncode, stdout.text(), stderr.text(), timed_out,
                         stdout.truncated or stderr.truncated, cancelled, command, time.monotonic() - started,
                         resource_status="timed_out" if timed_out else "cancelled" if cancelled else "completed")


class LocalExecutor:
    backend = 'local'

    def execute(self, request, *, on_start=None):
        return _execute(request, on_start=on_start)


def execute_shell(command, *, cwd, env, timeout, on_start=None, output_limit=65536, cancel=None):
    """原有 Shell 入口也消费统一契约。"""
    return LocalExecutor().execute(CommandRequest(command, cwd, env, timeout, output_limit, cancel), on_start=on_start)
