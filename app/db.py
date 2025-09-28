import time, copy
class DB:
    # P4_PATCH: Neon-like persistence via DAL when DATABASE_URL is set
    _persist = None

    def __init__(self):
        self.memory={
            'configs':{
                'audio_worklet_enabled': False,
                'vad_attack_ms': 12,
                'vad_release_ms': 240,
                'vad_dbfs_threshold': -42,
                'csrf_enforced': bool(__import__('os').environ.get('CSRF_ENFORCED','')).__bool__(),'profile_gate_enabled':False,
                'show_instruction_strip': True,'show_state_dots': True,'theme':'light',
                'suggestions_enabled': True,'suggestions_max_items':4,'suggestions_max_words':7,
                'nudges_enabled': True,'nudge_delay_ms':4200,'nudge_backoff_after_ignored':2,
                'confirm_ms':420,'echo_threshold_boost':1.9,'min_speech_ms':220,'voice_command_hints':True,
                'language_lock':'en','max_turn_seconds':90,'normalization_table_version':1,
                'nebraska_persona_level':0.13,'nebraska_quotes_enabled':True,
                'ws_ping_interval_ms':25000,'ws_idle_timeout_ms':30000,'reconnect_policy':'1_attempt_5s','llm_provider':'auto','openai_model':'gpt-4o-mini','stt_provider':'auto','tts_provider':'auto',
                'redact_email_in_logs':True,
                'gen_humor': 0.0,
                'gen_target_verbosity': 'medium',
                'gen_max_sentences': 4,
                'gen_top_p': 1.0,
                'gen_temperature': 0.3,
                'feature_audio': True,
                'tts_voice_id': '',
                'tts_output_format': 'mp3_44100_128',
                'tts_model_id': 'eleven_multilingual_v2'
            },
            'users':{},'profiles':{},'sessions':{},'emails':[],'logs':[],'layouts':{},'personas':{}
        }
    def ensure_session(self, sid, email):
        try:
            if persist_enabled():
                from .dal import neon_pg
                neon_pg.ensure_schema(); neon_pg.upsert_user(email); neon_pg.ensure_session(sid, email)
        except Exception:
            pass
        self.memory['sessions'].setdefault(sid,{'email':email,'messages':[],'nudges':0,'persona_id':'chip'})
    def add_message(self, sid, role, text):
        try:
            if persist_enabled():
                from .dal import neon_pg
                neon_pg.add_message(sid, role, text)
        except Exception:
            pass
        self.memory['sessions'].setdefault(sid,{'email':'user@example.com','messages':[],'nudges':0,'persona_id':'chip'}); self.memory['sessions'][sid]['messages'].append((role,text))
    def get_transcript(self, sid): return "\n".join([f"{r.upper()}: {t}" for r,t in self.memory['sessions'].get(sid,{'messages':[]})['messages']])
    def get_config(self): import copy; return copy.deepcopy(self.memory['configs'])
    def update_config(self, updates):
        self.memory['configs'].update(updates)
        try:
            if persist_enabled():
                from .dal import neon_pg
                neon_pg.ensure_schema(); neon_pg.save_config(self.memory['configs'])
        except Exception:
            pass
        return self.get_config()
    def add_email(self,to,subject,body): self.memory['emails'].append({'to':to,'subject':subject,'body':body,'created_at':time.time()})
    def list_emails(self): return list(self.memory['emails'])
db=DB()
def seed_default_persona():
    db.memory['personas']['chip']={'id':'chip','owner':'system','published':{'version':1,'pack':{'id':'chip'}},'draft':{'version':1,'pack':{'id':'chip'}},'history':[{'version':1,'pack':{'id':'chip'}}],'active':True}

# --- DAL wrappers for generic SQL (used by greet idempotency etc.) -----------
def sql(q: str, params=None):
    """Execute a SQL statement (no fetch). Uses Neon DAL when available."""
    if not persist_enabled():
        raise AttributeError("sql unavailable: DATABASE_URL not set")
    from .dal import neon_pg
    neon_pg.ensure_schema()
    neon_pg._exec(q, params or [])

def sql_one(q: str, params=None):
    """Execute a SQL query and return the first row as a dict-like mapping.
    If the backend returns tuples, we map the first column to the key 'turn_id'
    when the SELECT list is a single column commonly used by our helpers.
    """
    if not persist_enabled():
        raise AttributeError("sql_one unavailable: DATABASE_URL not set")
    from .dal import neon_pg
    neon_pg.ensure_schema()
    row = neon_pg._fetch_one(q, params or [])
    if row is None:
        return {}
    # sqlite3.Row behaves like a mapping
    try:
        keys = list(row.keys())  # works for sqlite Row
        return {k: row[k] for k in keys}
    except Exception:
        # Likely a tuple (psycopg)
        try:
            if len(row) == 1:
                return {"turn_id": row[0]}
            # Fallback: enumerate
            return {f"col{i}": v for i, v in enumerate(row)}
        except Exception:
            return {}
seed_default_persona()


def persist_enabled():
    import os
    return bool(os.environ.get("DATABASE_URL"))
