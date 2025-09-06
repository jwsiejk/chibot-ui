# test_da_mailer_policy.py
import os, importlib, sys, pathlib
ROOT = pathlib.Path(__file__).resolve().parents[2]  # repo root
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
m = importlib.import_module("app.services.mailer")

def test_mailer_disallowed_in_prod_without_smtp(monkeypatch):
    # Unset SMTP and disallow mocks
    for k in ["EMAIL_HOST","EMAIL_PORT","EMAIL_HOST_USER","EMAIL_HOST_PASSWORD","FROM_EMAIL"]:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("APP_ENV","production")
    monkeypatch.delenv("ALLOW_MOCK_PROVIDERS", raising=False)
    try:
        m.send_transcript("a@b.com","s","b")
        assert False, "should have raised"
    except RuntimeError as e:
        assert "mocks disallowed" in str(e)