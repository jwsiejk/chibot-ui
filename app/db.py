import time, copy
from typing import Any, Dict, Iterable, Mapping, Optional


def _ensure_iterable(value: Any) -> Iterable[Any]:
    if isinstance(value, (list, tuple, set)):
        return value
    if value is None:
        return []
    return [value]


def _empty_session_goal() -> Dict[str, Any]:
    return {
        "phase": None,
        "depth": None,
        "delivery_pref": None,
        "working_intent": None,
        "entities": {"products": [], "keywords": []},
        "confirmed": [],
    }


def _normalize_session_goal(goal: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    if not isinstance(goal, Mapping):
        return _empty_session_goal()
    normalized = _empty_session_goal()
    for key in ("phase", "depth", "delivery_pref", "working_intent"):
        if key in goal:
            normalized[key] = goal.get(key)
    entities = goal.get("entities")
    if isinstance(entities, Mapping):
        for name in ("products", "keywords"):
            raw_items = entities.get(name)
            bucket: Dict[str, Any] = normalized["entities"]
            if isinstance(raw_items, Iterable) and not isinstance(raw_items, (str, bytes)):
                bucket[name] = [str(item) for item in raw_items if str(item).strip()]
    confirmed = goal.get("confirmed")
    if isinstance(confirmed, Iterable) and not isinstance(confirmed, (str, bytes)):
        normalized["confirmed"] = [str(item) for item in confirmed if str(item).strip()]
    elif isinstance(confirmed, str) and confirmed.strip():
        normalized["confirmed"] = [confirmed.strip()]
    return normalized


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
                'silence_guard_ms':1800,
                'confirm_ms':420,'echo_threshold_boost':1.9,'min_speech_ms':220,'voice_command_hints':True,
                'language_lock':'en','max_turn_seconds':90,'normalization_table_version':1,
                'nebraska_persona_level':0.13,'nebraska_quotes_enabled':True,
                'ws_ping_interval_ms':25000,'ws_idle_timeout_ms':30000,'reconnect_policy':'1_attempt_5s','llm_provider':'auto','openai_model':'gpt-4o-mini','stt_provider':'auto','tts_provider':'auto',
                'redact_email_in_logs':True,
                'nlu_logging_enabled': True,
                'gen_humor': 0.0,
                'gen_target_verbosity': 'medium',
                'gen_max_sentences': 4,
                'gen_top_p': 1.0,
                'gen_temperature': 0.3,
                'feature_audio': True,
                'tts_voice_id': '',
                'tts_output_format': 'mp3_44100_128',
                'tts_model_id': 'eleven_multilingual_v2',
                'feature_manual_barge_in': True,
                'barge_in_mode_manual': True,
                'planner_high_threshold': 0.75,
                'planner_medium_threshold': 0.60,
            },
            'users':{},'profiles':{},'sessions':{},'emails':[],'logs':[],'layouts':{},'personas':{}
        }
        self._ensure_config_defaults()
    def ensure_session(self, sid, email):
        try:
            if persist_enabled():
                from .dal import neon_pg
                neon_pg.ensure_schema(); neon_pg.upsert_user(email); neon_pg.ensure_session(sid, email)
        except Exception:
            pass
        session = self.memory['sessions'].setdefault(
            sid,
            {'email':email,'messages':[],'nudges':0,'persona_id':'chip'}
        )
        if not isinstance(session.get('goal'), dict):
            session['goal'] = _empty_session_goal()
        else:
            session['goal'] = _normalize_session_goal(session['goal'])
    def add_message(self, sid, role, text):
        try:
            if persist_enabled():
                from .dal import neon_pg
                neon_pg.add_message(sid, role, text)
        except Exception:
            pass
        session = self.memory['sessions'].setdefault(
            sid,
            {'email':'user@example.com','messages':[],'nudges':0,'persona_id':'chip'}
        )
        if not isinstance(session.get('goal'), dict):
            session['goal'] = _empty_session_goal()
        else:
            session['goal'] = _normalize_session_goal(session['goal'])
        self.memory['sessions'][sid]['messages'].append((role,text))
    def get_transcript(self, sid): return "\n".join([f"{r.upper()}: {t}" for r,t in self.memory['sessions'].get(sid,{'messages':[]})['messages']])
    def _ensure_config_defaults(self) -> None:
        configs = self.memory.setdefault('configs', {})
        if configs.get('planner_high_threshold') is None:
            configs['planner_high_threshold'] = 0.75
        if configs.get('planner_medium_threshold') is None:
            configs['planner_medium_threshold'] = 0.60

    def get_config(self, key: Optional[str] = None, default: Any = None):
        self._ensure_config_defaults()
        snapshot = copy.deepcopy(self.memory['configs'])
        if key is None:
            return snapshot
        return snapshot.get(key, default)
    def update_config(self, updates):
        self.memory['configs'].update(updates)
        self._ensure_config_defaults()
        try:
            if persist_enabled():
                from .dal import neon_pg
                neon_pg.ensure_schema(); neon_pg.save_config(self.memory['configs'])
        except Exception:
            pass
        return self.get_config()
    def add_email(self,to,subject,body): self.memory['emails'].append({'to':to,'subject':subject,'body':body,'created_at':time.time()})
    def list_emails(self): return list(self.memory['emails'])
    def get_session_goal(self, sid: str) -> Dict[str, Any]:
        session = self.memory['sessions'].get(sid, {})
        goal = session.get('goal') if isinstance(session, dict) else None
        return copy.deepcopy(_normalize_session_goal(goal))

    def update_session_goal(self,
                            sid: Optional[str],
                            *,
                            phase: Optional[Any] = None,
                            depth: Optional[Any] = None,
                            delivery_pref: Optional[Any] = None,
                            working_intent: Optional[Any] = None,
                            entities: Optional[Mapping[str, Any]] = None,
                            confirmed: Optional[Iterable[Any]] = None) -> Dict[str, Any]:
        if not sid:
            return _empty_session_goal()
        session = self.memory['sessions'].setdefault(
            sid,
            {'email':'user@example.com','messages':[],'nudges':0,'persona_id':'chip'}
        )
        if not isinstance(session.get('goal'), dict):
            session['goal'] = _empty_session_goal()
        goal = session['goal']

        def _store_value(key: str, value: Optional[Any]) -> None:
            if value is not None:
                goal[key] = value
                _mark_confirmed(key, value)

        def _mark_confirmed(key: str, value: Optional[Any]) -> None:
            confirmed_list = goal.setdefault('confirmed', [])
            if not isinstance(confirmed_list, list):
                confirmed_list = []
                goal['confirmed'] = confirmed_list
            if value and str(value).strip():
                normalized = key.strip()
                if normalized and normalized not in confirmed_list:
                    confirmed_list.append(normalized)
            else:
                if key in confirmed_list:
                    confirmed_list.remove(key)

        if phase is not None:
            _store_value('phase', phase)
        if depth is not None:
            _store_value('depth', depth)
        if delivery_pref is not None:
            _store_value('delivery_pref', delivery_pref)
        if working_intent is not None:
            _store_value('working_intent', working_intent)

        if entities:
            dest = goal.setdefault('entities', {"products": [], "keywords": []})
            if not isinstance(dest, dict):
                dest = {"products": [], "keywords": []}
                goal['entities'] = dest
            for key in entities.keys():
                raw_items = entities.get(key)
                if raw_items is None:
                    continue
                bucket = dest.setdefault(key, [])
                if not isinstance(bucket, list):
                    bucket = list(bucket) if isinstance(bucket, Iterable) else []
                    dest[key] = bucket
                seen = {str(item).strip().lower() for item in bucket if str(item).strip()}
                for item in _ensure_iterable(raw_items):
                    text = str(item or "").strip()
                    if not text:
                        continue
                    lowered = text.lower()
                    if lowered in seen:
                        continue
                    bucket.append(text)
                    seen.add(lowered)
                if bucket:
                    singular = key[:-1] if key.endswith('s') and len(key) > 1 else key
                    _mark_confirmed(singular, bucket)

        if confirmed:
            for item in confirmed:
                key = str(item or "").strip()
                if not key:
                    continue
                confirmed_list = goal.setdefault('confirmed', [])
                if key not in confirmed_list:
                    confirmed_list.append(key)

        return copy.deepcopy(goal)
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
