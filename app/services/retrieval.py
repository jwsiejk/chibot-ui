
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


def list_documents(query: str = "", tag: str = "", limit: int = 50, offset: int = 0):
    if _persist():
        try:
            from ..dal import neon_pg
            return neon_pg.kb_list_docs(query, tag, limit, offset)
        except Exception:
            pass
    kb = db.memory.setdefault('kb', {'docs':[], 'chunks':[]})
    docs = kb['docs']
    if query:
        docs = [d for d in docs if query.lower() in (d.get('title','')+d.get('body','')).lower()]
    if tag:
        docs = [d for d in docs if tag.lower() in (d.get('tags','').lower())]
    return [{"id":d["id"],"title":d["title"],"tags":d["tags"],"size":len(d.get("body","")), "chunks": sum(1 for c in kb['chunks'] if c['doc_id']==d['id'])} for d in docs[offset:offset+limit]]

def get_document(doc_id: int):
    if _persist():
        try:
            from ..dal import neon_pg
            return neon_pg.kb_get_doc(doc_id)
        except Exception:
            pass
    kb = db.memory.setdefault('kb', {'docs':[], 'chunks':[]})
    d = next((x for x in kb['docs'] if x['id']==doc_id), None)
    if not d: return None
    chunks = [c for c in kb['chunks'] if c['doc_id']==doc_id]
    return {"id": d["id"], "title": d["title"], "tags": d["tags"], "body": d["body"], "chunks": chunks}

def delete_document(doc_id: int) -> bool:
    if _persist():
        try:
            from ..dal import neon_pg
            return neon_pg.kb_delete_doc(doc_id)
        except Exception:
            pass
    kb = db.memory.setdefault('kb', {'docs':[], 'chunks':[]})
    kb['docs'] = [d for d in kb['docs'] if d['id'] != doc_id]
    kb['chunks'] = [c for c in kb['chunks'] if c['doc_id'] != doc_id]
    return True
