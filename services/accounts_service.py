import os, csv

DEFAULT_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static", "data", "americas_accounts.csv")

def _path():
    return os.getenv("ACCOUNTS_CSV_PATH") or DEFAULT_PATH

def _normalize_row(row):
    # Support two schema variants:
    # 1) Account, Pure Rep, Pure Type
    # 2) Account Name, Account Owner, Type
    if "Account" in row and "Pure Rep" in row and "Pure Type" in row:
        return {
            "account_name": row.get("Account","").strip(),
            "owner": row.get("Pure Rep","").strip(),
            "type": row.get("Pure Type","").strip(),
            "source": "GovTop",
        }
    return {
        "account_name": row.get("Account Name","").strip() or row.get("Account","").strip(),
        "owner": row.get("Account Owner","").strip() or row.get("Pure Rep","").strip(),
        "type": row.get("Type","").strip() or row.get("Pure Type","").strip(),
        "source": "Corporate/Commercial/Enterprise/PubSec",
    }

def search_accounts(q: str, limit: int=25):
    path = _path()
    results = []
    if not q:
        return results
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            rdr = csv.DictReader(f)
            ql = q.lower()
            for row in rdr:
                norm = _normalize_row(row)
                if ql in (norm["account_name"] or "").lower():
                    results.append(norm)
                    if len(results) >= limit:
                        break
    except FileNotFoundError:
        return []
    return results
