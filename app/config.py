"""Application configuration helpers."""

try:
    from dotenv import load_dotenv  # type: ignore
except ImportError:  # pragma: no cover - optional dependency
    load_dotenv = None
else:  # pragma: no branch
    load_dotenv(dotenv_path=".env", override=False)


def get_env(name: str, default=None):
    """Retrieve an environment variable with an optional default."""
    import os

    value = os.getenv(name)
    return value if value is not None else default
