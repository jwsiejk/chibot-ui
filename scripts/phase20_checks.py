import os, sys, json, sqlite3
from pathlib import Path
BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))

from app.api_v1 import admin as admin_mod

def run():
    # Preview (invalid) should fail
    admin_mod.request = type("R",(object,), {"get_json":lambda self,force=True: {"confirm_ms":0}})()
    resp = admin_mod.config_preview()
    assert isinstance(resp, tuple) and resp[1]==400
    # Commit valid
    admin_mod.request = type("R",(object,), {"get_json":lambda self,force=True: {"confirm_ms":420,"language_lock":"en","suggestions_max_items":3}})()
    resp = admin_mod.config_commit()
    assert resp[0].json["ok"] is True
    # Rollback should succeed after at least two commits
    admin_mod.request = type("R",(object,), {"get_json":lambda self,force=True: {"confirm_ms":421,"language_lock":"en","suggestions_max_items":3}})()
    admin_mod.config_commit()
    rb = admin_mod.config_rollback()
    assert rb[0].json["ok"] is True
    print("PHASE20: PASS")

if __name__ == "__main__":
    run()
