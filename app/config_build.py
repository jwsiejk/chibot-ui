import datetime
import os
import subprocess

_BUILD_ID = None


def _git_sha_short():
    try:
        return (
            subprocess.check_output(
                ["git", "rev-parse", "--short", "HEAD"], stderr=subprocess.DEVNULL
            )
            .decode()
            .strip()
        )
    except Exception:
        return None


def current_build_id():
    global _BUILD_ID
    if _BUILD_ID:
        return _BUILD_ID
    env = os.environ.get("BUILD_ID")
    if env and env.strip():
        _BUILD_ID = env.strip()
        return _BUILD_ID
    sha = _git_sha_short()
    if sha:
        _BUILD_ID = sha
        return _BUILD_ID
    _BUILD_ID = datetime.datetime.utcnow().strftime("%Y%m%d%H%M%S")
    return _BUILD_ID
