import importlib
import os


def test_load_backend_env_reads_backend_dotenv(tmp_path, monkeypatch):
    env_module = importlib.import_module("env_config")
    backend_dir = tmp_path / "backend"
    backend_dir.mkdir()
    (backend_dir / ".env").write_text("GROQ_API_KEY=test-key\n", encoding="utf-8")
    monkeypatch.setattr(env_module, "__file__", str(backend_dir / "env_config.py"))
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    env_module.load_backend_env()

    assert os.environ["GROQ_API_KEY"] == "test-key"
