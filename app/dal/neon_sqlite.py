import os, sqlite3, json, time
DEFAULT_PATH = os.getenv("PERSIST_SQLITE_PATH", "/mnt/data/ask_chip.sqlite")
DDL = [
    "CREATE TABLE IF NOT EXISTS app_config (key TEXT PRIMARY KEY, value_json TEXT);",
    "CREATE TABLE IF NOT EXISTS layouts (breakpoint TEXT PRIMARY KEY, state_json TEXT);",
    "CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, email TEXT, persona_id TEXT, started_at REAL, ended_at REAL, summary_json TEXT);",
    "CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, role TEXT, text TEXT, meta_json TEXT, created_at REAL);"
]
def connect(path=None):
    return sqlite3.connect(path or DEFAULT_PATH)
def init_schema(conn):
    cur=conn.cursor()
    for stmt in DDL: cur.execute(stmt)
    conn.commit()
def snapshot_memory(conn, memory: dict, session_id=None):
    init_schema(conn); cur=conn.cursor()
    cur.execute("DELETE FROM app_config")
    for k,v in (memory.get('configs') or {}).items():
        cur.execute("INSERT OR REPLACE INTO app_config(key,value_json) VALUES(?,?)",(k,json.dumps(v)))
    cur.execute("DELETE FROM layouts")
    for bp, state in (memory.get('layouts') or {}).items():
        cur.execute("INSERT OR REPLACE INTO layouts(breakpoint,state_json) VALUES(?,?)",(bp,json.dumps(state)))
    if session_id:
        sess=(memory.get('sessions') or {}).get(session_id)
        if sess:
            cur.execute("INSERT OR REPLACE INTO sessions(id,email,persona_id,started_at,ended_at,summary_json) VALUES(?,?,?,?,?,?)",
                        (session_id, sess.get('email'), sess.get('persona_id','chip'), time.time(), None, None))
            cur.execute("DELETE FROM messages WHERE session_id=?", (session_id,))
            for role,text in (sess.get('messages') or []):
                cur.execute("INSERT INTO messages(session_id,role,text,meta_json,created_at) VALUES(?,?,?,?,?)",
                            (session_id, role, text, None, time.time()))
    conn.commit()
def restore_memory(conn, memory: dict, session_id=None):
    cur=conn.cursor()
    memory['configs']={}
    for k,vj in cur.execute("SELECT key,value_json FROM app_config"):
        import json as _j; memory['configs'][k]=_j.loads(vj)
    memory['layouts']={}
    for bp,sj in cur.execute("SELECT breakpoint,state_json FROM layouts"):
        import json as _j; memory['layouts'][bp]=_j.loads(sj)
    if session_id:
        msgs=[]
        for role,text in cur.execute("SELECT role,text FROM messages WHERE session_id=? ORDER BY id ASC",(session_id,)):
            msgs.append((role,text))
        if msgs:
            memory.setdefault('sessions',{}).setdefault(session_id, {'email':'user@example.com','messages':[],'nudges':0,'persona_id':'chip'})
            memory['sessions'][session_id]['messages']=msgs
