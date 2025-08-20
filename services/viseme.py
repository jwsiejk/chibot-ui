VOWELS_AI = set(list("aAiI"))
VOWEL_E = set(list("eE"))
ROUND_OU = set(list("oOuUwWqQ"))
LABIAL_M = set(list("mMbBpP"))
LABIODENTAL_F = set(list("fFvV"))
ALVEOLAR_L = set(list("lL"))
SIBILANT_S = set(list("sSzZxXcC"))
RHOTIC_R = set(list("rR"))
NASAL_N = set(list("nN"))

def char_to_viseme(ch: str) -> str:
    if ch in LABIAL_M:
        return "M"
    if ch in LABIODENTAL_F:
        return "F"
    if ch in ALVEOLAR_L:
        return "L"
    if ch in ROUND_OU:
        return "O"
    if ch in VOWEL_E:
        return "E"
    if ch in VOWELS_AI:
        return "AI"
    if ch in SIBILANT_S:
        return "S"
    if ch in RHOTIC_R:
        return "R"
    if ch in NASAL_N:
        return "N"
    if ch.isspace():
        return "REST"
    return "REST"

def schedule_for_text(text: str):
    text = (text or "").strip()
    if not text:
        return [{"t": 0.0, "id": "REST"}]
    raw = [char_to_viseme(ch) for ch in text]
    if all(v == "REST" for v in raw):
        raw = ["AI"]
    compressed = []
    last = None
    for v in raw:
        if v != last:
            compressed.append(v)
            last = v
    if len(compressed) < 3:
        compressed = (compressed + ["REST"])[:3]
    count = len(compressed)
    out = []
    for i, v in enumerate(compressed):
        t = i / max(1, count - 1)
        out.append({"t": max(0.0, min(1.0, t)), "id": v})
    if out[-1]["id"] != "REST":
        out.append({"t": 1.0, "id": "REST"})
    final = []
    prev = None
    for item in out:
        if not prev or item["id"] != prev["id"] or item["t"] != prev["t"]:
            final.append(item); prev = item
    return final
