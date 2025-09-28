import re

BAD = ["as an ai", "as a language model", "i am an ai", "how can i assist you"]
CONTRACTIONS = [
    (r"\bI am\b", "I’m"), (r"\bwe are\b", "we’re"), (r"\bdo not\b", "don’t"),
    (r"\bcan not\b", "can’t"), (r"\bI will\b", "I’ll"), (r"\bwe will\b", "we’ll")
]

def humanize_text(txt: str) -> str:
    if not txt: return txt
    for pat, rep in CONTRACTIONS:
        txt = re.sub(pat, rep, txt, flags=re.IGNORECASE)
    for p in BAD:
        txt = re.sub(p, "", txt, flags=re.IGNORECASE)
    txt = re.sub(r"\s{2,}", " ", txt).strip()
    txt = re.sub(r"(?m)^\s*[-•]\s*", "- ", txt)
    return txt

def sounds_botty(txt: str) -> bool:
    if any(k in txt.lower() for k in BAD): return True
    if txt and len(txt.split()) > 160 and "\n" not in txt: return True
    return False

