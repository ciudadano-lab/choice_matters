import importlib
import sys
from pathlib import Path


def test_backend_main_imports_from_project_root():
    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root.parent) not in sys.path:
        sys.path.insert(0, str(repo_root.parent))

    module = importlib.import_module("backend.main")

    assert hasattr(module, "app")
    assert module.VALID_PLAYERS == [1, 2, 3]
