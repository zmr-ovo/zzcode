"""读取时限制内存；只保存脱敏后的有限输出。"""

from dataclasses import dataclass

import hashlib
import os
import tempfile
import re

OUTPUT_LIMIT = 65536
MODEL_OUTPUT_LIMIT = 3000
SECRET_PATTERN = re.compile(r"\b(?:sk-[A-Za-z0-9_-]{6,}|gh[pousr]_[A-Za-z0-9_]{6,})\b")


def sanitize(agent, text):
    return SECRET_PATTERN.sub("<redacted>", agent.redact_text(str(text)))


@dataclass(frozen=True)
class ToolOutput:
    content: str
    truncated: bool = False


class BoundedOutput:
    def __init__(self, limit=OUTPUT_LIMIT):
        self.limit = limit
        self.head = bytearray()
        self.tail = bytearray()
        self.total = 0

    def append(self, data):
        self.total += len(data)
        room = self.limit // 2 - len(self.head)
        self.head.extend(data[:room])
        self.tail.extend(data[room:])
        if len(self.tail) > self.limit // 2:
            del self.tail[:-self.limit // 2]

    @property
    def truncated(self):
        return self.total > self.limit

    def text(self):
        separator = b"\n... output truncated ...\n" if self.truncated else b""
        return (bytes(self.head) + separator + bytes(self.tail)).decode("utf-8", errors="replace")


class ArtifactStore:
    def __init__(self, agent):
        self.agent = agent
        self.root = agent.root / ".zzcode" / "artifacts"
        if self.root.resolve() != self.root:
            raise ValueError("artifact store must not contain symlinks")
        self.root.mkdir(parents=True, exist_ok=True)

    def save(self, text):
        data = sanitize(self.agent, text).encode("utf-8")[:OUTPUT_LIMIT].decode("utf-8", errors="ignore").encode("utf-8")
        artifact_id = hashlib.sha256(data).hexdigest()
        path = self.root / (artifact_id + ".txt")
        from .ledger import PersistenceError
        try:
            with tempfile.NamedTemporaryFile("wb", dir=self.root, delete=False) as handle:
                temporary = handle.name
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        except OSError as exc:
            raise PersistenceError("artifact commit failed after execution; reconcile before retry") from exc
        return artifact_id

    def read(self, artifact_id, start=1, end=80):
        if not re.fullmatch(r"[0-9a-f]{64}", artifact_id):
            raise ValueError("invalid artifact ID")
        if not isinstance(start, int) or not isinstance(end, int) or not 1 <= start <= end or end - start >= 200:
            raise ValueError("artifact range must contain at most 200 lines")
        path = self.root / (artifact_id + ".txt")
        if path.is_symlink():
            raise ValueError("artifact symlinks are not allowed")
        with path.open("rb") as handle:
            data = handle.read(OUTPUT_LIMIT + 1)
        if len(data) > OUTPUT_LIMIT or hashlib.sha256(data).hexdigest() != artifact_id:
            raise ValueError("artifact exceeds its limit or its digest changed")
        lines = sanitize(self.agent, data.decode("utf-8")).splitlines()
        return "\n".join(f"{index}: {line}" for index, line in enumerate(lines[start-1:end], start))[:MODEL_OUTPUT_LIMIT]

    def present(self, text):
        text = sanitize(self.agent, text)
        if len(text) <= MODEL_OUTPUT_LIMIT:
            return text, False, ()
        artifact_id = self.save(text)
        half = MODEL_OUTPUT_LIMIT // 2
        summary = text[:half] + "\n... truncated; read_artifact for retained output ...\n" + text[-half:]
        return summary, True, (artifact_id,)
