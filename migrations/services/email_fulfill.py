# services/email_fulfill.py
import requests

def _friendly_error(resp) -> str | None:
    try:
        js = resp.json()
        if isinstance(js, dict) and "error" in js:
            err = (js.get("error") or "").strip().lower()
            if "unauthenticated" in err: return "Please log in first."
            if "not found" in err:        return "I couldn’t find that."
            if "missing" in err:          return "I’m missing a detail to send that."
            return js.get("error")
    except Exception:
        pass
    return None

def fulfill_email_intent(intent: dict, base_url: str, user_id: str) -> str:
    t = (intent or {}).get("type")
    if t == "acct_team":
        company = intent["company"]
        r = requests.post(f"{base_url}/email/account-team", json={"company": company}, timeout=20)
        return f"I’ve emailed you the team for {company}." if r.ok else (_friendly_error(r) or f"I couldn’t send the email for {company}.")
    if t == "doc_link":
        title_hint = intent["title"]
        sr = requests.get(f"{base_url}/repo/search", params={"q": title_hint}, timeout=20)
        doc = None
        if sr.ok:
            js = sr.json() or {}
            results = js.get("results") or []
            if results:
                doc = results[0]
        if not doc:
            return f"I couldn’t find a deck matching “{title_hint}”."
        r = requests.post(f"{base_url}/email/repo-link", json={"doc_id": doc["id"]}, timeout=20)
        return "Sent. Check your inbox." if r.ok else (_friendly_error(r) or "I couldn’t send that email.")
    if t == "email_last":
        r = requests.post(f"{base_url}/email/last", timeout=20)
        return "Emailed." if r.ok else (_friendly_error(r) or "I don’t have anything to email yet.")
    return "I’m not sure what you want emailed."
