"""复用同一验证驱动，保存 P5 的确定性基线。"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.testing.freeze_p4_baseline import main  # noqa: E402

if __name__ == '__main__':
    raise SystemExit(main(phase='P5', default_profile='unified_execution'))
