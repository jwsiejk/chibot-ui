"""Application configuration helpers."""

import os

try:
    from dotenv import load_dotenv  # type: ignore
except ImportError:  # pragma: no cover - optional dependency
    load_dotenv = None
else:  # pragma: no branch
    load_dotenv(dotenv_path=".env", override=False)


def env_bool(name: str, default: bool = False) -> bool:
    """Return an environment variable parsed as a boolean."""

    value = os.getenv(name)
    if value is None:
        return bool(default)
    value = value.strip().lower()
    if value in {"1", "true", "t", "yes", "y", "on"}:
        return True
    if value in {"0", "false", "f", "no", "n", "off"}:
        return False
    return bool(default)


DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY")
ASR_BACKPRESSURE_THRESHOLD_BYTES = int(
    os.getenv("ASR_BACKPRESSURE_THRESHOLD_BYTES", "1048576")
)
ASR_IDLE_CLOSE_MS = int(os.getenv("ASR_IDLE_CLOSE_MS", "4000"))
ASR_TRACE = env_bool("ASR_TRACE", False)


def get_env(name: str, default=None):
    """Retrieve an environment variable with an optional default."""

    value = os.getenv(name)
    return value if value is not None else default
