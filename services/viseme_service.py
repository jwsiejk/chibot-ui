import re

VISEMES = ["NEUTRAL","M","F","L","S","R","E","AI","O","U"]

_vowels = {
    "AI": r"(?:ai|ay|ae|\ba(?!u))",
    "E": r"(?:ee|ea|ei|ie|e|y$)",
    "O": r"(?:oa|ow|aw|o)",
    "U": r"(?:oo|ou|ew|u)",
}

def _token_to_viseme(token: str) -> str:
    t = token.lower()
    if re.search(r"[mbp]", t): return "M"
    if re.search(r"[fv]", t): return "F"
    if re.search(r"l", t): return "L"
    if re.search(r"[szx]|sh|ch|j", t): return "S"
    if re.search(r"r", t): return "R"
    for v, pat in _vowels.items():
        if re.search(pat, t):
            return v
    return "E" if re.search(r"[aeiouy]", t) else "NEUTRAL"

def _split_tokens(text: str):
    words = re.findall(r"[A-Za-z']+|[0-9]+", text or "")
    tokens = []
    for w in words:
        chunks = re.findall(r"(?:[bcdfghjklmnpqrstvwxyz]+|[aeiouy]+)", w, flags=re.I)
        tokens.extend(chunks if chunks else [w])
    return tokens

def visemes_for_text(text: str):
    tokens = _split_tokens(text or "")
    if not tokens:
        return [{"t":0.0, "v":"NEUTRAL"}, {"t":1.0, "v":"NEUTRAL"}]
    lengths = [max(1, len(t)) for t in tokens]
    total = float(sum(lengths))
    t = 0.0
    schedule = []
    for tok, ln in zip(tokens, lengths):
        v = _token_to_viseme(tok)
        dur = ln / total
        schedule.append({"t": max(0.0, min(1.0, t)), "v": v})
        t += dur
    schedule.append({"t":1.0, "v":"NEUTRAL"})
    return schedule
