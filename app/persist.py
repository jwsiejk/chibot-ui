import json, os, threading
from pathlib import Path
DEFAULT_PATH = Path(os.environ.get("ASK_CHIP_DATA_PATH", "/mnt/data/ask-chip/.data/db.json"))
_lock = threading.Lock()
_default_payload = {
    'configs': {
        'csrf_enforced': False,
        'profile_gate_enabled': False,
    },
    'users': {},
    'profiles': {},
    'sessions': {},
    'emails': [],
    'layouts': {},
    'logs': [],
}
def load(path: Path = DEFAULT_PATH):
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        save(_default_payload, path)
    with _lock:
        try:
            return json.loads(path.read_text())
        except Exception:
            return dict(_default_payload)
def save(payload: dict, path: Path = DEFAULT_PATH):
    path.parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        path.write_text(json.dumps(payload, indent=2))
