import importlib
import os
from pathlib import Path


def test_load_backend_env_reads_backend_dotenv(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    env_module = importlib.import_module("env_config")
    env_module.load_backend_env()

    assert os.environ.get("GEMINI_API_KEY")
