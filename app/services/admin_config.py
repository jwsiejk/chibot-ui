
from typing import Dict, Any
from .config_store import get_config, set_config  # fallback shim to your existing store if present

def get_admin_config() -> Dict[str, Any]:
    return get_config()

def set_admin_config_patch(updates: Dict[str, Any]) -> Dict[str, Any]:
    cfg = get_config()
    cfg.update(updates)
    return set_config(cfg)
