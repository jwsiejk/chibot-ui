# app/obs/metrics.py
from typing import Dict, Any
from ..db import db

def inc(name: str, tags: Dict[str, Any] | None = None):
    db.memory.setdefault("metrics", []).append({"type":"count","name":name,"value":1,"tags":tags or {}})

def observe(name: str, value: float, tags: Dict[str, Any] | None = None):
    db.memory.setdefault("metrics", []).append({"type":"observe","name":name,"value":float(value),"tags":tags or {}})
