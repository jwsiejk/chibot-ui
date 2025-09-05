import time, copy
class DB:
    # P4_PATCH: Neon-like persistence via DAL when DATABASE_URL is set
    _persist = None

    def __init__(self):
        self.memory={
            'configs':{
                'csrf_enforced':False,'profile_gate_enabled':False,
                'show_instruction_strip': True,'show_state_dots': True,'theme':'light',
                'suggestions_enabled': True,'suggestions_max_items':4,'suggestions_max_words':7,
                'nudges_enabled': True,'nudge_delay_ms':4200,'nudge_backoff_after_ignored':2,
                'confirm_ms':420,'echo_threshold_boost':1.9,'min_speech_ms':220,'voice_command_hints':True,
                'language_lock':'en','max_turn_seconds':90,'normalization_table_version':1,
                'nebraska_persona_level':0.13,'nebraska_quotes_enabled':True,
                'ws_ping_interval_ms':25000,'ws_idle_timeout_ms':30000,'reconnect_policy':'1_attempt_5s','llm_provider':'mock','openai_model':'gpt-4o-mini','stt_provider':'mock','tts_provider':'mock',
                'redact_email_in_logs':True
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
seed_default_persona()


def persist_enabled():
    import os
    return bool(os.environ.get("DATABASE_URL"))
