# Add near the top:
import random
try:
    from memory import get_connection
except Exception:
    get_connection = None

def _db_phrases(category: str):
    if not get_connection: return []
    try:
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT text FROM public.phrases WHERE category=%s AND active=true", (category,))
            return [r[0] for r in cur.fetchall()]
    except Exception:
        return []

def _pick_from_db(category: str, fallback: list[str]):
    rows = _db_phrases(category)
    pool = rows if rows else fallback
    return random.choice(pool) if pool else ""

# Fallbacks:
_VOICE_OPENERS_FB  = ["Let me get that for you.","Give me a moment.","I’ll pull that up.","One moment while I check."]
_VOICE_FOLLOWUPS_FB= ["Would you like me to email that to you?","Want that emailed?","I can email that to you if you want.","Should I email the details to you?"]

# Where you built opener/tail:
opener = _pick_from_db("voice_opener", _VOICE_OPENERS_FB) + " "
tail   = " " + _pick_from_db("voice_followup", _VOICE_FOLLOWUPS_FB)
