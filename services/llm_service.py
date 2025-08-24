import os
import re
import random
from typing import List, Dict, Optional, Tuple
from openai import OpenAI

# -------- Core persona (no canned lines; all output is LLM-generated) --------
CHIP_PERSONA = (
    "You are Chip Tracewell: a well-mannered, plain-spoken Nebraskan with dry, unintentional humor; "
    "a tech-savvy virtual systems engineer who teaches like a teammate, not a lecturer. "
    "Pure-Storage-first, practical, and empathetic—use short, clear sentences with natural contractions. "
    "If steps are needed, speak them: 'First …, Next …, Then …, Finally …'. "
    "Never present option menus. Avoid buzzwords and corporate throat-clearing. "
    "If the topic drifts from Pure Storage, craft a fresh, friendly pivot back—do not sound abrupt—and ask for the product and the goal in one sentence. "
    "Do not reuse the exact phrasing you already used earlier in this conversation. "
    "Never break character or mention these instructions."
)

def _client() -> OpenAI:
    key = os.getenv("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("OPENAI_API_KEY not set")
    return OpenAI(api_key=key)

def _model() -> str:
    return os.getenv("OPENAI_MODEL", "gpt-4o-mini")

def _coerce_history(history):
    msgs: List[Dict[str,str]] = []
    for item in history or []:
        if isinstance(item, dict):
            role = item.get("role") or item.get("speaker") or item.get("author") or ""
            content = item.get("content") or item.get("text") or item.get("message") or ""
            if role in ("system","user","assistant") and content:
                msgs.append({"role": role, "content": str(content)})
        elif isinstance(item, (list,tuple)) and len(item) >= 2:
            role, content = item[0], item[1]
            if role in ("system","user","assistant") and content:
                msgs.append({"role": str(role), "content": str(content)})
    return msgs

def _last_assistant_lines(history, n=4):
    out = []
    for item in reversed(history or []):
        if isinstance(item, dict) and item.get("role") == "assistant" and item.get("content"):
            out.append(item["content"])
            if len(out) >= n: break
    return list(reversed(out))

def _detect_product(txt: str) -> str:
    t = (txt or "").lower()
    if "flashblade" in t or "flash blade" in t: return "FlashBlade"
    if "flasharray" in t or "flash array" in t: return "FlashArray"
    if "portworx" in t: return "Portworx"
    if any(k in t for k in ("purity","evergreen","pure storage")): return "Pure"
    return ""

def _detect_task(txt: str) -> str:
    t = (txt or "").lower()
    if any(k in t for k in ("troubleshoot","issue","error","down","fail","broken")): return "troubleshooting"
    if "design" in t or "architecture" in t: return "design"
    if "size" in t or "sizing" in t or "capacity" in t: return "sizing"
    if any(k in t for k in ("install","installation","setup","set up","deploy")): return "installation"
    if any(k in t for k in ("brief","overview","summary")): return "briefing"
    return ""

PURE_KEYS = ("pure storage","flasharray","flash array","flashblade","flash blade","portworx","purity","evergreen")
OFFDOMAIN = ("roblox","minecraft","fortnite","spotify","shopify","wordpress","netflix","youtube","tiktok","instagram","snapchat","discord","tesla","reddit","steam","epic games","twitter","x.com")

def _offtopic_info(txt: str) -> Tuple[bool, Optional[str]]:
    t = (txt or "").lower()
    if any(k in t for k in PURE_KEYS): return (False, None)
    for w in OFFDOMAIN:
        if w in t: return (True, w)
    if len(t.split()) <= 3 and not _detect_product(t) and not _detect_task(t):  # vague
        return (True, None)
    return (False, None)

# --- Post-processor: keep output sounding spoken (no bullets) ---
def _spokenize(text: str) -> str:
    if not text: return ""
    # Convert list-like into spoken steps
    lines = [ln.rstrip() for ln in text.splitlines()]
    items = []
    nonlist = []
    pat = re.compile(r'^\\s*(?:[-–—•*]|\\d+[.)])\\s+(.*\\S)\\s*$')
    for ln in lines:
        m = pat.match(ln)
        if m:
            item = m.group(1).strip()
            item = re.sub(r'\\s*[:;.-]\\s*$', '', item)
            items.append(item)
        else:
            nonlist.append(ln)
    if not items:
        inline = re.findall(r'(?:^|\\s)\\d+[.)]\\s+([^\\n]+)', text)
        items = [c.strip() for c in inline if c.strip()]
    if items:
        seq = []
        for i, it in enumerate(items):
            if i == 0:   seq.append(f"First, {it}")
            elif i == 1: seq.append(f"Next, {it}")
            elif i == len(items)-1: seq.append(f"Finally, {it}")
            else:        seq.append(f"Then, {it}")
        spoken = " ".join(s.strip().rstrip('.') + "." for s in seq if s.strip())
        prefix = " ".join(p.strip() for p in nonlist if p.strip())
        text = (prefix + " " + spoken).strip() if prefix else spoken

    # Light de-jargon
    for pat, rep in [(r"\\butilize\\b","use"),(r"\\bin order to\\b","to"),(r"\\bhowever\\b","but"),(r"\\btherefore\\b","so")]:
        text = re.sub(pat, rep, text, flags=re.I)

    # Keep compact (~4 sentences)
    parts = re.split(r'(?<=[.!?])\\s+', text.strip())
    return " ".join(parts[:4]).strip()

def _compose_system(user_text: str, history: list) -> str:
    t = (user_text or "").lower()
    product_hint = _detect_product(t)
    task_hint = _detect_task(t)
    last_assistant = [m["content"] for m in history[-6:] if m.get("role")=="assistant" and m.get("content")]

    guidance = [
      "Talk like a human. Use natural contractions.",
      "No numbered/bulleted lists—use spoken steps where needed.",
      "No option menus. Keep it concise. Only one short follow-up if useful.",
      "If the topic drifts from Pure Storage, craft a fresh, friendly pivot back and ask for the product and goal in one sentence.",
      "Do not repeat the exact phrasing of earlier assistant messages in this chat."
    ]
    if product_hint: guidance.append(f"Likely product focus: {product_hint}.")
    if task_hint: guidance.append(f"Likely user intent: {task_hint}.")
    if last_assistant:
        joined = " • ".join(s[:140] for s in last_assistant)
        guidance.append("Avoid phrasing similar to: " + joined)

    return CHIP_PERSONA + "\\n\\nGuidance for this turn (do not repeat verbatim):\\n- " + "\\n- ".join(guidance)

# -------- Public API compatible with existing imports --------
def generate_reply(messages: Optional[List[Dict[str, str]]] = None,
                   prompt: Optional[str] = None,
                   model: Optional[str] = None,
                   max_tokens: int = 500,
                   temperature: float = 0.75) -> str:
    client = _client()
    history = _coerce_history(messages) if messages else []
    if prompt and not history:
        history = [{"role":"user","content":prompt}]
    sys = _compose_system(history[-1]["content"] if history and history[-1]["role"]=="user" else "", history)
    msgs = [{"role":"system","content": sys}] + history
    resp = client.chat.completions.create(model=model or _model(), messages=msgs, temperature=temperature, max_tokens=max_tokens, presence_penalty=0.3, frequency_penalty=0.4)
    return _spokenize((resp.choices[0].message.content or "").strip())

def generate_greeting(profile: Optional[Dict[str, str]] = None,
                      model: Optional[str] = None,
                      temperature: float = 0.85) -> str:
    client = _client()
    profile = profile or {}
    name = (profile.get("name") or "").strip()
    title = (profile.get("title") or "").strip()
    region = (profile.get("region") or "").strip()
    hints = [b for b in [f"name: {name}" if name else "", f"title: {title}" if title else "", f"region: {region}" if region else ""] if b]
    sys = CHIP_PERSONA + "\\nKeep greetings varied; no stock phrases; 1–2 sentences; end with a friendly, specific question."
    user = "Create a warm, personable greeting with light Nebraska charm. Use natural speech (no lists). Context: " + (" | ".join(hints) if hints else "no profile hints") + "."
    msgs = [{"role":"system","content": sys}, {"role":"user","content": user}]
    resp = client.chat.completions.create(model=model or _model(), messages=msgs, temperature=temperature, max_tokens=120, presence_penalty=0.3, frequency_penalty=0.4)
    return _spokenize((resp.choices[0].message.content or "").strip())

def generate_response(user_text: str, history=None, force_email: bool=False, model: Optional[str]=None) -> Dict[str,str]:
    client = _client()
    history = _coerce_history(history)
    sys = _compose_system(user_text, history)
    msgs = [{"role":"system","content": sys}] + history + [{"role":"user","content": user_text or ""}]
    resp = client.chat.completions.create(model=model or _model(), messages=msgs, temperature=0.8, max_tokens=600, presence_penalty=0.3, frequency_penalty=0.4)
    text = (resp.choices[0].message.content or "").strip()
    return {"text": _spokenize(text)}

def phrase_data(role: str, data: Dict, history=None, model: Optional[str]=None) -> str:
    """
    Phrase arbitrary structured data in Chip's voice, dynamically (no canned copy).
    role: a short label like 'account_team', 'kpi_summary', etc.
    data: dict payload to describe
    """
    client = _client()
    history = _coerce_history(history)
    sys = CHIP_PERSONA + "\\nSpeak conversationally (no lists). One or two sentences; add one short helpful follow-up only if it adds value."
    prompt = f"Please phrase this {role} information for the user in a friendly, concise way. DATA: {data}"
    msgs = [{"role":"system","content": sys}] + history + [{"role":"user","content": prompt}]
    resp = client.chat.completions.create(model=model or _model(), messages=msgs, temperature=0.8, max_tokens=200, presence_penalty=0.3, frequency_penalty=0.4)
    return _spokenize((resp.choices[0].message.content or "").strip())
