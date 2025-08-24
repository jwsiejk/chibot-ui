import os
from typing import List, Dict, Optional
from openai import OpenAI

# --- Chip persona (no triage/menu language) ---
CHIP_SYSTEM = (
    "You are Chip Tracewell, a well-mannered, unintentionally funny, tech-savvy Nebraskan who works as a virtual systems engineer. "
    "Teach clearly and practically with concrete steps. Be concise, personable, and lightly witty—no hype. "
    "Use plain language and, when relevant, accurate Pure Storage details (FlashArray, FlashBlade, Portworx). "
    "Occasionally a gentle Nebraska-ism is fine, but sparingly. "
    "When appropriate, end with a short invitational question to keep the conversation going. "
    "Never mention that you are an AI. Never break character. "
    "Do not present option menus like ‘design / sizing / troubleshooting / briefing’. Keep the flow natural."
)

def _client() -> OpenAI:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not set")
    return OpenAI(api_key=api_key)

def _default_model() -> str:
    return os.getenv("OPENAI_MODEL", "gpt-4o-mini")

def _coerce_history(history: Optional[List[Dict]]) -> List[Dict[str, str]]:
    msgs: List[Dict[str, str]] = []
    for item in history or []:
        if isinstance(item, dict):
            role = item.get("role") or item.get("speaker") or item.get("author") or ""
            content = item.get("content") or item.get("text") or item.get("message") or ""
            if role in ("system", "user", "assistant") and content:
                msgs.append({"role": role, "content": str(content)})
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            # e.g., ["user", "hello"]
            role, content = item[0], item[1]
            if role in ("system", "user", "assistant") and content:
                msgs.append({"role": str(role), "content": str(content)})
    return msgs

def _humanize(text: str) -> str:
    t = (text or "").strip()
    if not t:
        return "Tell me the product and your goal, and I’ll jump in. Want me to keep it high‑level or step‑by‑step?"
    # Trim to a few sentences and ensure a helpful follow‑up at the end
    import re
    t = re.sub(r"\n{2,}", "\n", t).strip()
    parts = re.split(r"(?<=[.!?])\s+", t)
    t = " ".join(parts[:3]).strip()
    if not t.endswith("?"):
        t += " Anything you want me to expand?"
    return t

def generate_reply(messages: Optional[List[Dict[str, str]]] = None,
                   prompt: Optional[str] = None,
                   model: Optional[str] = None,
                   max_tokens: int = 500,
                   temperature: float = 0.7) -> str:
    """Low-level wrapper: returns a single assistant string."""
    client = _client()
    model = model or _default_model()
    if prompt and not messages:
        messages = [{"role": "user", "content": prompt}]
    messages = [{"role": "system", "content": CHIP_SYSTEM}] + _coerce_history(messages or [])
    resp = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return (resp.choices[0].message.content or "").strip()

def generate_greeting(profile: Optional[Dict[str, str]] = None,
                      model: Optional[str] = None,
                      temperature: float = 0.8) -> str:
    """Short dynamic greeting (1–2 sentences) that nods to profile lightly."""
    client = _client()
    model = model or _default_model()
    profile = profile or {}
    name = (profile.get("name") or "").strip()
    title = (profile.get("title") or "").strip()
    region = (profile.get("region") or "").strip()

    hint_parts: List[str] = []
    if name: hint_parts.append(f"name: {name}")
    if title: hint_parts.append(f"title: {title}")
    if region: hint_parts.append(f"region: {region}")
    hint = "; ".join(hint_parts) if hint_parts else "no profile context"

    sys = CHIP_SYSTEM + " Keep greetings varied; avoid stock phrases."
    user = (
        "Create a warm, dynamic greeting to start a short voice chat. "
        "Use 1–2 sentences, natural spoken cadence. "
        "Subtly reference context if useful and end with a friendly specific question. "
        f"Context: {hint}. Do not list profile fields explicitly or say 'your profile says'. No emojis."
    )
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": sys}, {"role": "user", "content": user}],
        temperature=temperature,
        max_tokens=120,
    )
    return (resp.choices[0].message.content or '').strip()

# --- Simple intent hints (no menus) ---
def _hist_texts(hist) -> List[str]:
    out: List[str] = []
    for item in hist or []:
        if isinstance(item, dict):
            out.append(str(item.get("message") or item.get("text") or item.get("content") or ""))
        elif isinstance(item, (list, tuple)) and item:
            out.append(str(item[-1]))
        elif isinstance(item, str):
            out.append(item)
    return [s.strip().lower() for s in out if s]

def _detect_product(t: str) -> str:
    tl = (t or "").lower()
    if "flashblade" in tl or "flash blade" in tl:
        return "flashblade"
    if "flasharray" in tl or "flash array" in tl:
        return "flasharray"
    if "portworx" in tl:
        return "portworx"
    return ""

def _detect_task(t: str) -> str:
    tl = (t or "").lower()
    if any(k in tl for k in ("brief", "overview", "summary")): 
        return "briefing"
    if any(k in tl for k in ("troubleshoot", "troubleshooting", "issue", "error", "down", "fail", "broken")):
        return "troubleshooting"
    if "design" in tl or "architecture" in tl:
        return "design"
    if "size" in tl or "sizing" in tl or "capacity" in tl:
        return "sizing"
    return ""

def _infer_from_history(hist) -> (str, str):
    product = ""
    task = ""
    for h in reversed(_hist_texts(hist)[-10:]):
        if not product:
            product = _detect_product(h) or product
        if not task:
            task = _detect_task(h) or task
        if product and task:
            break
    return product, task

def _fb_brief() -> str:
    return _humanize(
        "FlashBlade//S gives you scale‑out file and object with low‑latency flash and simple growth by blades. "
        "It shines for fast restore, analytics, and AI staging."
    )

def _fb_troubleshoot() -> str:
    return _humanize(
        "Let’s pinpoint the FlashBlade issue. Is it NFS/SMB, S3 calls, or performance? "
        "We’ll check recent array events and the network path (MTU/LACP)."
    )

def _fb_design() -> str:
    return _humanize(
        "For a FlashBlade design, I look at usable TB, target GB/s, and file vs object split. "
        "We’ll size blades for ingest and restores and set clean export/bucket policies."
    )

def _fb_sizing() -> str:
    return _humanize(
        "For sizing, share usable capacity, peak throughput, and concurrency; "
        "I’ll sketch a first‑pass blade count and validation approach."
    )

def _fa_brief() -> str:
    return _humanize(
        "FlashArray unifies block, file, and object with consistently low latency and strong data reduction—"
        "great for databases and mixed consolidations. Upgrades stay nondisruptive."
    )

def _fa_design() -> str:
    return _humanize(
        "Designing FlashArray starts with the app mix and latency goals. "
        "Then we pick protocol paths and protection that meet your recovery window."
    )

def _fa_troubleshoot() -> str:
    return _humanize(
        "On FlashArray, I’d confirm host paths and recent alerts, then check queue depth and workload changes. "
        "I can tailor a short host‑specific checklist."
    )

def _fa_sizing() -> str:
    return _humanize(
        "Share usable TB and IOPS/latency goals and I’ll outline a sizing start you can validate."
    )

def _px_brief() -> str:
    return _humanize(
        "Portworx brings Kubernetes‑native data services—block/file, backup/DR, and database controls. "
        "What’s the cluster goal you want to hit?"
    )

def _px_design() -> str:
    return _humanize(
        "For Portworx design, share cluster topology and needed storage classes; I’ll sketch a sensible layout."
    )

def _px_troubleshoot() -> str:
    return _humanize(
        "For Portworx troubleshooting, we’ll look at pxctl status, pool health, and PVC events to narrow it fast."
    )

def _dispatch(product: str, task: str) -> str:
    if product == "flashblade":
        if task == "briefing": return _fb_brief()
        if task == "troubleshooting": return _fb_troubleshoot()
        if task == "design": return _fb_design()
        if task == "sizing": return _fb_sizing()
        return _humanize("FlashBlade//S—what are you trying to accomplish? I can dive into design, sizing, or troubleshooting.")
    if product == "flasharray":
        if task == "briefing": return _fa_brief()
        if task == "troubleshooting": return _fa_troubleshoot()
        if task == "design": return _fa_design()
        if task == "sizing": return _fa_sizing()
        return _humanize("FlashArray—tell me your goal (migration, consolidation, performance, DR, etc.) and I’ll map a clean path.")
    if product == "portworx":
        if task in ("briefing", ""): return _px_brief()
        if task == "design": return _px_design()
        if task == "troubleshooting": return _px_troubleshoot()
        return _px_brief()
    return ""

def generate_response(user_text: str,
                      history: Optional[List[Dict]] = None,
                      force_email: bool = False,
                      model: Optional[str] = None) -> Dict[str, str]:
    """Primary entrypoint used by routes.chat. Returns {'text': '...'}.

    No first‑turn triage/menu injection.

    History is respected if provided.

    """
<<<<<<< HEAD
    t = (user_text or "").strip()
    # Optional: lightweight hints before hitting the model
    product = _detect_product(t)
    task = _detect_task(t)
    if task and not product:
        hp, _ = _infer_from_history(history)
        if hp:
            product = hp
    if not product and not task:
        hp, ht = _infer_from_history(history)
        product = product or hp
        task = task or ht

    routed = _dispatch(product, task)
    if routed:
        return {"text": routed}

    # Build messages for the model
    messages: List[Dict[str, str]] = [{"role": "system", "content": CHIP_SYSTEM}]
    messages.extend(_coerce_history(history))
    messages.append({"role": "user", "content": t or "Please reply briefly."})

    try:
        client = _client()
        resp = client.chat.completions.create(
            model=model or _default_model(),
            messages=messages,
            temperature=0.7,
            max_tokens=500,
        )
        text = (resp.choices[0].message.content or "").strip()
        return {"text": _humanize(text)}
    except Exception:
        # Neutral fallback—no menu language
        return {"text": "Tell me what you’re trying to do and which product you’re on (FlashArray, FlashBlade, or Portworx). I’ll jump in."}
=======
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

>>>>>>> cff6bc0262a3a5be528cdb90fa95271d173af028
