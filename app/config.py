"""Application configuration helpers."""

import os

try:
    from dotenv import load_dotenv  # type: ignore
except ImportError:  # pragma: no cover - optional dependency
    load_dotenv = None
else:  # pragma: no branch
    load_dotenv(dotenv_path=".env", override=False)

DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY")
ASR_IDLE_CLOSE_MS = int(os.getenv("ASR_IDLE_CLOSE_MS", "4000"))


def get_env(name: str, default=None):
    """Retrieve an environment variable with an optional default."""
    value = os.getenv(name)
    return value if value is not None else default
