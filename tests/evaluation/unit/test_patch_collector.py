import subprocess

import pytest

from zzcode.evaluation import ArtifactError, collect_patch


def _git(repo, *args):
    return subprocess.run(
        ["git", *args], cwd=repo, text=True, capture_output=True, check=True
    ).stdout.strip()


@pytest.fixture
def patch_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "--quiet")
    _git(repo, "config", "user.name", "Phase 5 Test")
    _git(repo, "config", "user.email", "phase5@example.invalid")
    (repo / "tracked.py").write_text("VALUE = 1\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "--quiet", "-m", "base")
    return repo


def test_collect_patch_includes_staged_unstaged_and_untracked_but_not_runtime(patch_repo):
    (patch_repo / "tracked.py").write_text("VALUE = 2\n", encoding="utf-8")
    (patch_repo / "new.py").write_text("NEW = True\n", encoding="utf-8")
    runtime = patch_repo / ".zzcode"
    runtime.mkdir()
    (runtime / "session.json").write_text("private runtime data", encoding="utf-8")

    patch = collect_patch(patch_repo)

    assert "diff --git a/tracked.py b/tracked.py" in patch
    assert "diff --git a/new.py b/new.py" in patch
    assert "+NEW = True" in patch
    assert ".zzcode" not in patch


def test_collect_patch_returns_empty_for_clean_workspace(patch_repo):
    assert collect_patch(patch_repo) == ""


def test_collect_patch_enforces_size_limit(patch_repo):
    (patch_repo / "tracked.py").write_text("VALUE = 'a very long value'\n", encoding="utf-8")
    with pytest.raises(ArtifactError, match="exceeds"):
        collect_patch(patch_repo, max_bytes=10)
