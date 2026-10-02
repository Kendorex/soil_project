import sys
from pathlib import Path

# Добавляем папку src в sys.path, чтобы тесты видели модули
src_path = Path(__file__).parent.parent / "src"
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))