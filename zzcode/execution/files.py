"""完整文件哈希用于核对；文本片段不能证明某次操作已经完成。"""

import hashlib
import os
import stat
import tempfile


class FileConflictError(ValueError):
    pass


def file_hash(path):
    if not path.exists():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def prepare_file(agent, name, args):
    raw = agent.root / args["path"]
    path = agent.path(args["path"])
    # 写操作明确禁止软链接，避免原子替换意外改变链接语义。
    for part in (raw, *raw.parents):
        if part == agent.root:
            break
        if part.is_symlink():
            raise ValueError("write paths must not contain symlinks")
    before = file_hash(path)
    if name == "patch_file":
        if path.stat().st_size > 8 * 1024 * 1024:
            raise ValueError("patch_file supports files up to 8 MiB")
        original = path.read_text(encoding="utf-8")
        count = original.count(args["old_text"])
        if count != 1:
            raise ValueError(f"old_text must occur exactly once, found {count}")
        content = original.replace(args["old_text"], args["new_text"], 1)
    else:
        content = args["content"]
    data = content.encode("utf-8")
    details = {"path": path.relative_to(agent.root).as_posix(), "before_hash": before,
               "after_hash": hashlib.sha256(data).hexdigest(),
               "mode": stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o644}
    return path, data, details


def atomic_write(path, data, details):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("wb", dir=path.parent, delete=False, prefix=".zzcode-write-") as handle:
        temporary = handle.name
        try:
            handle.write(data)
            os.fchmod(handle.fileno(), details["mode"])
            handle.flush()
            os.fsync(handle.fileno())
            if path.is_symlink() or file_hash(path) != details["before_hash"]:
                raise FileConflictError("file changed after planning; refusing to overwrite")
            os.replace(temporary, path)
            descriptor = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
