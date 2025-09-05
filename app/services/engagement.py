# app/services/engagement.py
from typing import Dict

NEGATIVE = ("not working","broken","this is insane","wtf","doesn't work","doesnt work","i don't get","i dont get","frustrated","angry","mad","upset")
UNCERTAIN = ("maybe","not sure","unsure","confused","i think","i guess","?", "hmm")

def score(text: str, meta: Dict) -> Dict:
    t = (text or "").lower()
    avg_rms = float(meta.get("avg_rms") or 0.0)
    max_rms = float(meta.get("max_rms") or 0.0)
    interrupts = int(meta.get("interrupts") or 0)
    long_pause = bool(meta.get("long_pause") or False)

    sentiment = "neutral"
    if any(p in t for p in NEGATIVE) or interrupts >= 2:
        sentiment = "frustrated"
    elif any(p in t for p in UNCERTAIN) or long_pause:
        sentiment = "uncertain"

    engagement = "med"
    if interrupts >= 2 or max_rms > 0.8: engagement = "high"
    if long_pause and avg_rms < 0.1: engagement = "low"

    return {"sentiment": sentiment, "engagement": engagement}
