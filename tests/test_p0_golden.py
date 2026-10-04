"""Migration guards freeze legacy behavior, including budget and denial edges."""

import pytest

from scripts.p0_golden import load_cases, load_profile, run_case


@pytest.mark.parametrize("case", load_cases(), ids=lambda case: case["id"])
def test_legacy_transcript(case, tmp_path):
    run_case(case, tmp_path / case["id"])


def test_unimplemented_migration_profile_is_rejected():
    with pytest.raises(ValueError, match="not implemented"):
        load_profile("structured")
