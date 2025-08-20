# services/accounts_service.py
import os, csv, time
from typing import List, Dict, Tuple, Optional
import memory
from psycopg2 import sql as psql

DEFAULT_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static", "data", "americas_accounts.csv")

headers: List[str] = []  # public union of headers/keys

_CSV = {"path": None, "mtime": None, "headers": [], "rows": []}
_DB = {"ready": False, "schema": "public", "table": None,
       "cols": {"account_name": None, "owner": None, "type": None}, "headers": []}

def _csv_path() -> str:
    return os.getenv("ACCOUNTS_CSV_PATH") or DEFAULT_PATH

def _normalize_row(row: Dict[str, str]) -> Dict[str, str]:
    r = { (k.strip() if isinstance(k, str) else k): (v.strip() if isinstance(v, str) else v) for k, v in (row or {}).items() }
    if "Account" in r and "Pure Rep" in r and "Pure Type" in r:
        norm = {"account_name": r.get("Account",""), "owner": r.get("Pure Rep",""), "type": r.get("Pure Type",""), "source": "GovTop"}
    else:
        norm = {"account_name": r.get("Account Name","") or r.get("Account",""),
                "owner": r.get("Account Owner","") or r.get("Pure Rep",""),
                "type": r.get("Type","") or r.get("Pure Type",""),
                "source": "Corporate/Commercial/Enterprise/PubSec"}
    norm.update({
        "Account": r.get("Account","") or r.get("Account Name",""),
        "Account Name": r.get("Account Name","") or r.get("Account",""),
        "Account Owner": r.get("Account Owner","") or r.get("Pure Rep",""),
        "Pure Rep": r.get("Pure Rep","") or r.get("Account Owner",""),
        "Type": r.get("Type","") or r.get("Pure Type",""),
        "Pure Type": r.get("Pure Type","") or r.get("Type",""),
    })
    return norm

def _csv_load(path: Optional[str] = None):
    p = path or _csv_path()
    try:
        mtime = os.path.getmtime(p)
    except FileNotFoundError:
        _CSV.update({"path": p, "mtime": None, "headers": [], "rows": []})
        return
    if _CSV["path"] == p and _CSV["mtime"] == mtime:
        return
    with open(p, "r", encoding="utf-8-sig") as f:
        rdr = csv.DictReader(f)
        rows = [dict(row) for row in rdr]
        hdrs = list(rdr.fieldnames or [])
    _CSV.update({"path": p, "mtime": mtime, "headers": hdrs, "rows": rows})

def _split_schema_table(name: Optional[str]) -> Tuple[str, Optional[str]]:
    if not name: return ("public", None)
    if "." in name:
        s, t = name.split(".", 1)
        return (s.strip() or "public", t.strip() or None)
    return ("public", name)

def _discover_db() -> bool:
    conn = None
    try:
        conn = memory.get_connection()
    except Exception:
        conn = None
    if not conn:
        return False

    table_env = os.getenv("ACCOUNTS_TABLE")
    candidates: List[Tuple[str,str]] = []
    with conn:
        with conn.cursor() as cur:
            if table_env:
                sch, tbl = _split_schema_table(table_env)
                if tbl:
                    candidates.append((sch, tbl))
            else:
                cur.execute("""
                    SELECT table_schema, table_name
                    FROM information_schema.tables
                    WHERE table_schema IN ('public')
                """)
                rows = cur.fetchall() or []
                candidates = [(r["table_schema"], r["table_name"]) for r in rows]

            for schema, table in candidates:
                try:
                    cur.execute("""
                        SELECT column_name
                        FROM information_schema.columns
                        WHERE table_schema=%s AND table_name=%s
                    """, (schema, table))
                    cols_rows = cur.fetchall() or []
                    cols = [r["column_name"] for r in cols_rows]
                    lowers = [c.lower() for c in cols]

                    def find(names: List[str]) -> Optional[str]:
                        for n in names:
                            if n in lowers:
                                return cols[lowers.index(n)]
                        return None

                    col_account = find(["account_name","account"])
                    col_owner   = find(["account_owner","owner","pure_rep","pure rep","rep"])
                    col_type    = find(["type","pure_type","segment"])
                    if not col_account:
                        continue

                    _DB.update({"ready": True, "schema": schema, "table": table,
                                "cols": {"account_name": col_account, "owner": col_owner, "type": col_type},
                                "headers": cols})
                    global headers
                    headers = sorted(set(cols) | {"account_name","owner","type","source"})
                    return True
                except Exception:
                    continue
    return False

def _db_search(q: str, limit: int=25) -> List[Dict[str,str]]:
    if not _DB["ready"] and not _discover_db():
        return []
    conn = memory.get_connection()
    if not conn:
        return []
    schema = _DB["schema"]; table = _DB["table"]; c = _DB["cols"]
    an = psql.Identifier(c["account_name"])
    ow = psql.Identifier(c["owner"]) if c["owner"] else psql.SQL("NULL::text")
    ty = psql.Identifier(c["type"]) if c["type"] else psql.SQL("NULL::text")
    query = psql.SQL("""
        SELECT {an} AS an, {ow} AS ow, {ty} AS ty
        FROM {schema}.{table}
        WHERE {an} ILIKE %s
        ORDER BY {an} ASC
        LIMIT %s
    """).format(an=an, ow=ow, ty=ty, schema=psql.Identifier(schema), table=psql.Identifier(table))
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(query, (f"%{q}%", limit))
                rows = cur.fetchall() or []
                out = []
                for r in rows:
                    an_val = r.get("an") if isinstance(r, dict) else r[0]
                    ow_val = (r.get("ow") if isinstance(r, dict) else r[1]) if len(r) > 1 else None
                    ty_val = (r.get("ty") if isinstance(r, dict) else r[2]) if len(r) > 2 else None
                    out.append({"account_name": an_val or "", "owner": ow_val or "", "type": ty_val or "", "source": f"db:{table}"})
                return out
    except Exception:
        return []

def _csv_search(q: str, limit: int=25) -> List[Dict[str,str]]:
    _csv_load()
    if not _CSV["rows"] or not q:
        return []
    ql = q.lower()
    out = []
    for row in _CSV["rows"]:
        n = _normalize_row(row)
        if ql in (n.get("account_name") or "").lower():
            out.append(n)
            if len(out) >= limit:
                break
    global headers
    hdrs = set(_CSV["headers"]) | {"account_name","owner","type","source","Account","Account Name","Account Owner","Pure Rep","Type","Pure Type"}
    headers = sorted(hdrs)
    return out

def reload(path: Optional[str] = None) -> None:
    _CSV.update({"path": None, "mtime": None, "headers": [], "rows": []})
    _DB.update({"ready": False, "table": None, "headers": [], "cols": {"account_name": None, "owner": None, "type": None}})
    if path:
        os.environ["ACCOUNTS_CSV_PATH"] = path

def search_accounts(q: str, limit: int=25) -> List[Dict[str,str]]:
    res = _db_search(q, limit=limit)
    if res:
        return res
    return _csv_search(q, limit=limit)

# ---- Backward-compatible exports expected by older app.py ----
def find_by_account(q: str, limit: int=25) -> List[Dict[str,str]]:
    return search_accounts(q, limit=limit)

def team_for_account(account_name: str) -> Dict[str,str]:
    res = search_accounts(account_name, limit=1)
    if not res:
        return {"account_name": account_name, "owner": "", "type": "", "found": False}
    m = res[0]
    return {"account_name": m.get("account_name",""), "owner": m.get("owner",""), "type": m.get("type",""), "found": True}
