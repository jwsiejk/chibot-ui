# services/accounts_service.py
import sys
from typing import List, Dict
from memory import get_connection

def _accounts_table_exists(conn) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.accounts') AS t;")
        row = cur.fetchone()
        return bool(row and row.get("t"))

def search_accounts(query: str, limit: int = 20) -> List[Dict]:
    """
    Return a list of accounts matching `query`.
    Expects an optional table: public.accounts(id, name, owner, type).
    If the table doesn't exist, returns [] (no warnings).
    """
    conn = get_connection()
    if not conn:
        return []

    try:
        with conn:
            if not _accounts_table_exists(conn):
                return []
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, name, owner, type
                    FROM public.accounts
                    WHERE name ILIKE %s
                    ORDER BY name ASC
                    LIMIT %s
                    """,
                    (f"%{query}%", limit),
                )
                rows = cur.fetchall() or []
                # Rows are dict-like thanks to memory.get_connection() row_factory
                return [
                    {
                        "id": r.get("id"),
                        "name": r.get("name"),
                        "owner": r.get("owner"),
                        "type": r.get("type"),
                    }
                    for r in rows
                ]
    except Exception as e:
        # Don't crash the app; just log and return empty
        sys.stderr.write(f"[warning] search_accounts failed: {e}\n")
        return []
    finally:
        conn.close()
