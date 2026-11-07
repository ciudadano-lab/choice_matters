import os
from pathlib import Path

from dotenv import load_dotenv


def load_backend_env() -> Path:
    """Load any backend or project .env files that are present."""
    backend_dir = Path(__file__).resolve().parent
    project_root = backend_dir.parent
    env_candidates = [project_root / ".env", backend_dir / ".env"]
    env_path = next((candidate for candidate in env_candidates if candidate.exists()), backend_dir / ".env")

    for candidate in env_candidates:
        if candidate.exists():
            load_dotenv(candidate, override=False)

    return env_path


load_backend_env()


def get_backend_setting(name: str, default: str = "") -> str:
    return os.environ.get(name, default)
