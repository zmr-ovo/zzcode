from pathlib import Path

import pytest

from zzcode.evaluation import DatasetValidationError, PrivateTestSpec, TestExecutor


def test_private_test_patch_cannot_modify_product_source(tmp_path):
    patch_path = tmp_path / "test.patch"
    patch_path.write_text(
        """diff --git a/calc.py b/calc.py
index 1111111..2222222 100644
--- a/calc.py
+++ b/calc.py
@@ -1 +1 @@
-old
+new
""",
        encoding="utf-8",
    )
    gold_path = tmp_path / "gold.patch"
    gold_path.write_text("gold", encoding="utf-8")
    spec = PrivateTestSpec(
        instance_id="ZZCODE-BUG-001",
        gold_patch_path=gold_path,
        test_patch_path=patch_path,
        fail_to_pass=("hidden_tests/test_x.py::test_x",),
        pass_to_pass=("tests/test_x.py",),
    )

    with pytest.raises(DatasetValidationError, match="only modify hidden_tests"):
        TestExecutor().inject_test_patch(Path(tmp_path), spec)
