from pathlib import Path
import re, sys
BASE = Path(__file__).resolve().parents[1]
def run():
    runbook = (BASE/"docs"/"runbook.md").read_text()
    assert "Rotate keys" in runbook
    envm = (BASE/"docs"/"env_matrix.md").read_text()
    assert "| staging |" in envm
    ts = (BASE/"docs"/"troubleshooting.md").read_text()
    assert "/api/v1/admin/db/health" in ts
    print("PHASE21: PASS")
if __name__=="__main__":
    run()


# Validate Admin UI links exist
admin_html = (BASE/"templates"/"admin.html").read_text()
assert "/api/v1/admin/db/health" in admin_html
assert "/api/v1/admin/outbox" in admin_html
assert "Runbook" in admin_html and "Environment Matrix" in admin_html and "Troubleshooting" in admin_html
