from __future__ import annotations
import json, time
from typing import Dict, Any

SCHEMA = {
    "confirm_ms": {"type":"int", "min": 100, "max": 2000},
    "echo_threshold_boost": {"type":"float", "min": 1.0, "max": 5.0},
    "nudge_delay_ms": {"type":"int", "min": 1000, "max": 10000},
    "suggestions_max_items": {"type":"int", "min": 0, "max": 4},
    "suggestions_max_words": {"type":"int", "min": 1, "max": 7},
    "language_lock": {"type":"str", "enum":["en"]},
    "nebraska_persona_level": {"type":"float", "min": 0.0, "max": 1.0}
}

def validate_config(cfg: Dict[str, Any]) -> Dict[str, str]:
    errors = {}
    for k, rule in SCHEMA.items():
        if k not in cfg:
            continue
        v = cfg[k]
        t = rule["type"]
        try:
            if t=="int":
                v2 = int(v)
                if "min" in rule and v2 < rule["min"]: errors[k]="below min"
                if "max" in rule and v2 > rule["max"]: errors[k]="above max"
            elif t=="float":
                v2 = float(v)
                if "min" in rule and v2 < rule["min"]: errors[k]="below min"
                if "max" in rule and v2 > rule["max"]: errors[k]="above max"
            elif t=="str":
                v2 = str(v)
                if "enum" in rule and v2 not in rule["enum"]: errors[k]="invalid value"
        except Exception:
            errors[k]="type error"
    return errors
