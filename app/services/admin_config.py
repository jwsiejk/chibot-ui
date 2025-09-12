# app/services/admin_config.py
from __future__ import annotations
from typing import Dict, Any

# Use your existing config source; no write wrapper here.
from .config_store import get_config

def get_admin_config() -> Dict[str, Any]:
    """
    Read-only view of the current admin config.
    Writes continue to flow through your existing POST /api/v1/admin/config.
    """
    return get_config()
