from pathlib import Path
import sys
sys.path.insert(0, 'C:/Projects/wincro/src')

from utils.config import DATA_DIR, PROJECT_ROOT

print(f"PROJECT_ROOT: {PROJECT_ROOT}")
print(f"PROJECT_ROOT.is_absolute(): {PROJECT_ROOT.is_absolute()}")
print(f"DATA_DIR: {DATA_DIR}")
print(f"DATA_DIR.is_absolute(): {DATA_DIR.is_absolute()}")

output_dir = Path(DATA_DIR / "templates")
print(f"\noutput_dir: {output_dir}")
print(f"output_dir.is_absolute(): {output_dir.is_absolute()}")

test_path = str(output_dir / "click_test.png")
print(f"\ntest_path: {test_path}")
print(f"Path(test_path).is_absolute(): {Path(test_path).is_absolute()}")
