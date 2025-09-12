
from __future__ import annotations
import json, os
from typing import Any, Dict

DEFAULTS = {
    "stt_mode": "batch",
    "deepgram": {
        "model": "nova-3",
        "language": "en",
        "smart_format": True,
        "listen_url": "wss://api.deepgram.com/v1/listen",
        "encoding": "opus",
        "sample_rate": 48000,
        "interim_results": True
    }
}

CFG_PATH = os.environ.get("ASKCHIP_CONFIG_PATH", os.path.abspath("./config.json"))

def _load() -> Dict[str, Any]:
    if not os.path.exists(CFG_PATH):
        return DEFAULTS.copy()
    try:
        with open(CFG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = {}
    out = DEFAULTS.copy()
    out.update(data)
    # ensure deepgram defaults nested
    dg = out.get("deepgram", {})
    dg_defaults = DEFAULTS["deepgram"].copy()
    dg_defaults.update(dg)
    out["deepgram"] = dg_defaults
    return out

def _save(cfg: Dict[str, Any]):
    with open(CFG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f)

def get_config() -> Dict[str, Any]:
    return _load()

def set_config(key: str, value: Any) -> Dict[str, Any]:
    cfg = _load()
    cfg[key] = value
    _save(cfg)
    return cfg
