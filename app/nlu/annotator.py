from typing import Dict

def annotate(meta: Dict) -> Dict:
    """Map prosody/meta to lightweight dialog tags."""
    tags = {"uncertain": False, "frustrated": False, "in_a_hurry": False}
    if meta.get("interruption_during_tts"): tags["in_a_hurry"] = True
    if meta.get("avg_rms", 0) > meta.get("avg_rms_baseline", 0) * 1.8: tags["in_a_hurry"] = True
    if meta.get("user_text_contains_caps"): tags["frustrated"] = True
    if meta.get("hedges_count", 0) >= 2: tags["uncertain"] = True
    return tags
