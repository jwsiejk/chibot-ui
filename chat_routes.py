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

# Replace the old lists with fallbacks:
_CHAT_OPENERS_FB  = ["Let me grab that.","One sec—pulling it up.","Got it—here’s what I see.","Let me get that for you."]
_CHAT_FOLLOWUPS_FB= ["Want me to email this to you?","Want that emailed?","I can email that to you if you want.","Should I email you the details?"]

# In compose/handler where you had:
#   opener = _pick(_CHAT_OPENERS) + " "
#   tail   = " " + _pick(_CHAT_FOLLOWUPS)
# change to:
opener = _pick_from_db("chat_opener", _CHAT_OPENERS_FB) + " "
tail   = " " + _pick_from_db("chat_followup", _CHAT_FOLLOWUPS_FB)
