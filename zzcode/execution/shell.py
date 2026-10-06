"""POSIX Shell 执行：有限输出、进程组超时和真实退出码。"""

import os
import selectors
import signal
import subprocess
import time
from dataclasses import dataclass

from .output import BoundedOutput


@dataclass(frozen=True)
class ShellOutcome:
    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool = False
    truncated: bool = False
    cancelled: bool = False

    def display(self):
        return f"exit_code: {self.exit_code}\nstdout:\n{self.stdout.strip() or '(empty)'}\nstderr:\n{self.stderr.strip() or '(empty)'}"


def execute_shell(command, *, cwd, env, timeout, on_start=None):
    process = subprocess.Popen(command, shell=isinstance(command, str), cwd=cwd, env=env, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, start_new_session=True)
    outputs = {process.stdout: BoundedOutput(), process.stderr: BoundedOutput()}
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
                if time.monotonic() >= deadline:
                    timed_out = True
                    break
                for key, _ in selector.select(min(0.1, max(0, deadline-time.monotonic()))):
                    chunk = os.read(key.fileobj.fileno(), 8192)
                    if chunk:
                        outputs[key.fileobj].append(chunk)
                    else:
                        selector.unregister(key.fileobj)
        if not timed_out:
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
    return ShellOutcome(process.returncode, stdout.text(), stderr.text(), timed_out,
                        stdout.truncated or stderr.truncated, cancelled)
