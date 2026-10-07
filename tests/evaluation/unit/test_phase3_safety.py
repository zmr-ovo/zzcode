from zzcode.evaluation import FailureType, SafetyPolicy, inspect_patch


SAFE_PATCH = """diff --git a/calc.py b/calc.py
index 1111111..2222222 100644
--- a/calc.py
+++ b/calc.py
@@ -1 +1 @@
-return value + 1
+return abs(value)
"""


def test_inspect_patch_accepts_small_source_change():
    result = inspect_patch(SAFE_PATCH)

    assert result.passed
    assert result.touched_paths == ("calc.py",)
    assert result.added_lines == 1
    assert result.deleted_lines == 1


def test_inspect_patch_rejects_protected_test_and_secret_paths():
    patch = SAFE_PATCH.replace("calc.py", "tests/test_calc.py")
    result = inspect_patch(patch)

    assert not result.passed
    assert result.failure.failure_type == FailureType.SAFETY_VIOLATION
    assert "protected path" in result.failure.message


def test_inspect_patch_rejects_path_traversal_in_file_header():
    patch = SAFE_PATCH.replace("+++ b/calc.py", "+++ b/../../outside.py")
    result = inspect_patch(patch)

    assert not result.passed
    assert "path traversal" in result.failure.message


def test_inspect_patch_enforces_file_and_line_limits():
    policy = SafetyPolicy(max_files=1, max_changed_lines=1)
    result = inspect_patch(SAFE_PATCH, policy)

    assert not result.passed
    assert "limit is 1" in result.failure.message


def test_inspect_patch_rejects_binary_and_symlink_changes():
    binary = SAFE_PATCH + "GIT binary patch\n"
    symlink = SAFE_PATCH + "new file mode 120000\n"

    assert not inspect_patch(binary).passed
    assert not inspect_patch(symlink).passed
