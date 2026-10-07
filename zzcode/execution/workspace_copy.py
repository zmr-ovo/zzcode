"""Docker 使用受控副本；提交修改前核对宿主工作区，不复制凭证或运行状态。"""
import os
import shutil
import stat
import tempfile
from pathlib import Path

from .commands import ExecutorError
from ..core.security import PRIVATE_PATH_NAMES, private_name
from .files import FileConflictError, atomic_write, file_hash

EXCLUDED = {'.git', '.zzcode', '.venv', 'venv', '__pycache__', '.pytest_cache', '.ruff_cache',
            *PRIVATE_PATH_NAMES}


def excluded(name):
    return name in EXCLUDED or private_name(name)


def snapshot(root):
    files, dirs = {}, set()
    for directory, children, names in os.walk(root):
        for name in [*children, *names]:
            path = Path(directory) / name
            if excluded(name):
                continue
            if path.is_symlink() or not (path.is_file() or path.is_dir()):
                raise ExecutorError('Docker workspace cannot contain symlinks or special files: ' + str(path.relative_to(root)))
        children[:] = [name for name in children if not excluded(name)]
        dirs.update((Path(directory) / name).relative_to(root).as_posix() for name in children)
        for name in names:
            if not excluded(name):
                path = Path(directory) / name
                files[path.relative_to(root).as_posix()] = (file_hash(path), stat.S_IMODE(path.stat().st_mode))
    return files, dirs


class WorkspaceCopy:
    def __init__(self, root, secret_values=()):
        self.root = Path(root).resolve()
        self.secret_values = tuple(value.encode() for value in secret_values if len(value) >= 4)
        self.temporary = None

    def __enter__(self):
        self.before, self.before_dirs = snapshot(self.root)
        self.temporary = tempfile.TemporaryDirectory(prefix='zzcode-docker-workspace-')
        self.path = Path(self.temporary.name)
        try:
            for name in self.before_dirs:
                (self.path / name).mkdir(parents=True, exist_ok=True)
            for name in self.before:
                source, target = self.root / name, self.path / name
                # 逐块扫描已知凭证，避免把本机配置误带入执行副本。
                overlap = max((len(value) for value in self.secret_values), default=1) - 1
                tail = b''
                with source.open('rb') as handle:
                    while chunk := handle.read(65536):
                        data = tail + chunk
                        if any(value in data for value in self.secret_values):
                            raise ExecutorError('workspace file contains a configured credential: ' + name)
                        tail = data[-overlap:] if overlap else b''
                shutil.copy2(source, target)
            self.directory_modes = {name: stat.S_IMODE((self.root / name).stat().st_mode) for name in self.before_dirs}
            for name, mode in self.directory_modes.items():
                (self.path / name).chmod(mode)
            self.copy_root_mode = stat.S_IMODE(self.path.stat().st_mode)
            # 复制期间源文件变化也不能作为有效的执行基线。
            if snapshot(self.path) != (self.before, self.before_dirs) or snapshot(self.root) != (self.before, self.before_dirs):
                raise FileConflictError('workspace changed while preparing Docker copy')
            if os.getuid() == 0:
                for directory, _, names in os.walk(self.path):
                    os.chown(directory, 65532, 65532)
                    for name in names:
                        os.chown(Path(directory) / name, 65532, 65532)
            return self
        except BaseException:
            self.temporary.cleanup()
            raise

    def sync(self):
        # 输出中的凭证/私有状态也不能回传；明确拒绝，不能静默丢弃后报告成功。
        for directory, children, names in os.walk(self.path):
            for name in [*children, *names]:
                if private_name(name) or name == '.zzcode':
                    raise ExecutorError('Docker command generated a private path that cannot be synchronized: ' + name, executed=True)
            children[:] = [name for name in children if not excluded(name)]
        after, after_dirs = snapshot(self.path)
        # 目录权限变更暂不支持回传，必须在任何文件写入之前明确拒绝。
        if stat.S_IMODE(self.path.stat().st_mode) != self.copy_root_mode or any(
            stat.S_IMODE((self.path / name).stat().st_mode) != mode
            for name, mode in self.directory_modes.items() if name in after_dirs
        ):
            raise ExecutorError("Docker directory permission changes cannot be synchronized", executed=True)
        changed = [name for name in self.before.keys() | after.keys() if self.before.get(name) != after.get(name)]
        current, current_dirs = snapshot(self.root)
        # 先核对整个修改集合，冲突时不覆盖用户同时修改的文件。
        for name in changed:
            if current.get(name) != self.before.get(name):
                raise FileConflictError('host workspace changed during Docker execution: ' + name)
            for parent in (self.root / name).parents:
                if parent == self.root:
                    break
                if parent.is_symlink():
                    raise FileConflictError('host workspace contains a changed symlink')
        removed_dirs = self.before_dirs - after_dirs
        for name in removed_dirs:
            original = self.root / name
            # 被排除的凭证/状态文件不能因副本中看不到它们而被删除。
            if original.exists() and any(excluded(p.name) for p in original.rglob('*')):
                raise FileConflictError('refusing to delete a directory containing private files: ' + name)
            if name not in current_dirs:
                raise FileConflictError('host directory changed during Docker execution: ' + name)
        for name in sorted(after_dirs, key=lambda item: (item.count('/'), item)):
            (self.root / name).mkdir(parents=True, exist_ok=True)
        for name in sorted(changed):
            target = self.root / name
            if name in after:
                atomic_write(target, (self.path / name).read_bytes(), {'before_hash': self.before.get(name, (None,))[0], 'mode': after[name][1]})
            else:
                if file_hash(target) != self.before[name][0]:
                    raise FileConflictError('host file changed before deletion: ' + name)
                target.unlink()
        for name in sorted(removed_dirs, key=lambda item: (-item.count('/'), item)):
            (self.root / name).rmdir()

    def __exit__(self, *args):
        self.temporary.cleanup()
