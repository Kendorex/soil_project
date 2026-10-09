import sys
from pathlib import Path

# Модули src используют относительные импорты и должны загружаться пакетом.
project_path = Path(__file__).resolve().parent.parent
if str(project_path) not in sys.path:
    sys.path.insert(0, str(project_path))
