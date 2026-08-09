import os
from pathlib import Path

from dotenv import load_dotenv


def load_backend_env() -> Path:
    """Load environment variables from the backend/.env file when present."""
    backend_dir = Path(__file__).resolve().parent
    env_path = backend_dir / ".env"

    if env_path.exists():
        load_dotenv(env_path, override=False)
    else:
        load_dotenv(override=False)

    return env_path


load_backend_env()


def get_backend_setting(name: str, default: str = "") -> str:
    return os.environ.get(name, default)
