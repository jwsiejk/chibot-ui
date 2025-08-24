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
    return " ".join(parts[:3]).strip()


def _limit_words(text: str, cap: int = WORD_CAP) -> str:
    if not text:
        return ""
    words = text.split()
    if len(words) <= cap:
        return text.strip()
    trimmed = " ".join(words[:cap]).rstrip(",;:—-")
    if not trimmed.endswith(('.', '!', '?')):
        trimmed += "."
    return trimmed.strip()
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
    return _limit_words(_spokenize((resp.choices[0].message.content or "").strip()))

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
    resp = client.chat.completions.create(model=model or _model(), messages=msgs, temperature=temperature, max_tokens=80, presence_penalty=0.3, frequency_penalty=0.4)
    return _limit_words(_spokenize((resp.choices[0].message.content or "").strip()))

def generate_response(user_text: str, history=None, force_email: bool=False, model: Optional[str]=None) -> Dict[str,str]:
    client = _client()
    history = _coerce_history(history)
    sys = _compose_system(user_text, history)
    msgs = [{"role":"system","content": sys}] + history + [{"role":"user","content": user_text or ""}]
    resp = client.chat.completions.create(model=model or _model(), messages=msgs, temperature=0.7, max_tokens=220, presence_penalty=0.3, frequency_penalty=0.4)
    text = (resp.choices[0].message.content or "").strip()
    return {"text": _limit_words(_spokenize(text))}

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
    resp = client.chat.completions.create(model=model or _model(), messages=msgs, temperature=0.7, max_tokens=200, presence_penalty=0.3, frequency_penalty=0.4)
    return _limit_words(_spokenize((resp.choices[0].message.content or "").strip()))

# --- Dynamic follow-up and nudge generators (no canned lines) ---
def generate_followup(user_text: str,
                      assistant_text: str,
                      history=None,
                      model: Optional[str] = None) -> Dict[str,str]:
    """Return a single short, persona-consistent follow-up, or empty text.
    Rules: <= 16 words; natural speech; no lists; not a restatement; no 'anything else' cliché; no menus.
    """
    client = _client()
    history = _coerce_history(history)
    sys = CHIP_PERSONA + (
        "\nKeep this follow-up very short. No numbered/bulleted lists; no menus. "
        "Offer one clear next step (e.g., go deeper on a subtopic, move to next step, email a brief checklist), "
        "but phrase it freshly and conversationally."
    )
    prompt = (
        "Based on the prior user message and your last reply, craft ONE short follow-up question or offer "
        "(<=16 words). Natural speech. No clichés like 'anything else'. No repeating your last sentence. "
        "Do not include greetings. If a follow-up would be noisy, return just the word: SILENT.\n\n"
        f"USER: {user_text}\nASSISTANT: {assistant_text}"
    )
    msgs = [{"role":"system","content": sys}] + history + [{"role":"user","content": prompt}]
    resp = client.chat.completions.create(
        model=model or _model(), messages=msgs, temperature=0.7, max_tokens=40, presence_penalty=0.3, frequency_penalty=0.5
    )
    text = (resp.choices[0].message.content or "").strip()
    text = _spokenize(text)
    if not text or text.upper().strip() == "SILENT":
        return {"text": ""}
    # enforce cap
    return {"text": _limit_words(text, cap=int(os.getenv("CHIP_FOLLOWUP_CAP","16")))}

def generate_nudge(state_hint: Dict=None, history=None, model: Optional[str]=None) -> Dict[str,str]:
    """Return a single short, gentle nudge if the user is silent.
    Rules: <= 14 words; friendly; no pressure; Pure-first; ask for product+goal in one sentence, but phrase it freshly.
    """
    client = _client()
    history = _coerce_history(history)
    state_hint = state_hint or {}
    prod = state_hint.get("product","")
    task = state_hint.get("task","")
    depth = state_hint.get("depth","")

    sys = CHIP_PERSONA + (
        "\nCraft a gentle, friendly nudge for silence. Natural speech, <=14 words. "
        "Do not reuse phrasing used earlier in this chat. No menus. No lists."
    )
    hint_bits = []
    if prod: hint_bits.append(f"product: {prod}")
    if task: hint_bits.append(f"task: {task}")
    if depth: hint_bits.append(f"depth: {depth}")
    prompt = "Context: " + (", ".join(hint_bits) if hint_bits else "none") + ". Nudge only."

    msgs = [{"role":"system","content": sys}, {"role":"user","content": prompt}]
    resp = client.chat.completions.create(
        model=model or _model(), messages=msgs, temperature=0.7, max_tokens=30, presence_penalty=0.3, frequency_penalty=0.6
    )
    text = (resp.choices[0].message.content or "").strip()
    text = _spokenize(text)
    return {"text": _limit_words(text, cap=int(os.getenv("CHIP_NUDGE_CAP","14")))}

