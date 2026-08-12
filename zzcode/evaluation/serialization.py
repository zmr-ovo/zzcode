"""Crash-resistant JSON and JSONL primitives for evaluation artifacts."""

from __future__ import annotations

import fcntl
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from .errors import ArtifactError


def write_json_atomic(path: Path, payload: Any, *, overwrite: bool = True) -> Path:
    """Write complete JSON via a same-directory temporary file and replace."""

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not overwrite and path.exists():
        raise ArtifactError(f"artifact already exists and will not be overwritten: {path}")
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            delete=False,
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
        ) as handle:
            temporary = Path(handle.name)
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        if not overwrite and path.exists():
            raise ArtifactError(f"artifact already exists and will not be overwritten: {path}")
        temporary.replace(path)
        return path
    except (OSError, TypeError, ValueError) as exc:
        raise ArtifactError(f"failed to write JSON artifact {path}: {exc}") from exc
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def write_text_atomic(path: Path, text: str, *, overwrite: bool = False) -> Path:
    path = Path(path)
    if not isinstance(text, str):
        raise ArtifactError("text artifact content must be a string")
    path.parent.mkdir(parents=True, exist_ok=True)
    if not overwrite and path.exists():
        raise ArtifactError(f"artifact already exists and will not be overwritten: {path}")
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            delete=False,
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
        ) as handle:
            temporary = Path(handle.name)
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        if not overwrite and path.exists():
            raise ArtifactError(f"artifact already exists and will not be overwritten: {path}")
        temporary.replace(path)
        return path
    except OSError as exc:
        raise ArtifactError(f"failed to write text artifact {path}: {exc}") from exc
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def append_jsonl(path: Path, payload: dict[str, Any], *, unique_key: str | None = None) -> Path:
    """Append one complete JSON line under an exclusive file lock."""

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ArtifactError(f"JSONL payload for {path} is not serializable: {exc}") from exc
    try:
        with path.open("a+", encoding="utf-8", newline="\n") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            if unique_key is not None:
                if unique_key not in payload:
                    raise ArtifactError(f"JSONL payload is missing unique key {unique_key!r}")
                handle.seek(0)
                for line_number, line in enumerate(handle, start=1):
                    if not line.strip():
                        continue
                    try:
                        existing = json.loads(line)
                    except json.JSONDecodeError as exc:
                        raise ArtifactError(
                            f"existing JSONL is invalid at {path}:{line_number}: {exc}"
                        ) from exc
                    if existing.get(unique_key) == payload[unique_key]:
                        raise ArtifactError(
                            f"duplicate {unique_key} {payload[unique_key]!r} in {path}"
                        )
            handle.seek(0, os.SEEK_END)
            handle.write(encoded)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except OSError as exc:
        raise ArtifactError(f"failed to append JSONL artifact {path}: {exc}") from exc
    return path


def load_json(path: Path) -> Any:
    path = Path(path)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ArtifactError(f"artifact does not exist: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ArtifactError(f"invalid JSON artifact {path}: {exc}") from exc
