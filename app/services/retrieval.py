
# app/services/retrieval.py
from typing import List, Dict
from ..db import db

def add_document(title: str, body: str, tags: str = "") -> int:
    if _persist():
        try:
            from ..dal import neon_pg
            return neon_pg.kb_upsert_doc(title, body, tags)
        except Exception:
            pass
    # memory fallback
    kb = db.memory.setdefault('kb', {'docs':[], 'chunks':[]})
    doc_id = len(kb['docs'])+1
    kb['docs'].append({'id':doc_id,'title':title,'tags':tags,'body':body})
    # naive chunking
    for i in range(0, len(body), 600):
        kb['chunks'].append({'doc_id':doc_id, 'idx':i//600, 'content': body[i:i+600]})
    return doc_id

def search(query: str, limit: int = 5) -> List[str]:
    if _persist():
        try:
            from ..dal import neon_pg
            return neon_pg.kb_search(query, limit)
        except Exception:
            pass
    # memory fallback naive
    kb = db.memory.setdefault('kb', {'docs':[], 'chunks':[]})
    hits = [c['content'] for c in kb['chunks'] if query.lower() in c['content'].lower()]
    return hits[:limit]

def _persist() -> bool:
    try:
        from ..db import persist_enabled
        return persist_enabled()
    except Exception:
        return False
