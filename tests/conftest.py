import pytest
@pytest.fixture(autouse=True)
def reset_state():
    from app.db import db, seed_default_persona
    db.memory = {
        'configs': {
            'csrf_enforced': False, 'profile_gate_enabled': False,
            'show_instruction_strip': True, 'show_state_dots': True, 'theme':'light',
            'suggestions_enabled': True, 'suggestions_max_items': 4, 'suggestions_max_words': 7,
            'nudges_enabled': True, 'nudge_delay_ms': 4200, 'nudge_backoff_after_ignored': 2,
            'confirm_ms': 420, 'echo_threshold_boost': 1.9, 'min_speech_ms': 220, 'voice_command_hints': True,
            'language_lock': 'en', 'max_turn_seconds': 90, 'normalization_table_version': 1,
            'nebraska_persona_level': 0.13, 'nebraska_quotes_enabled': True,
            'ws_ping_interval_ms': 25000, 'ws_idle_timeout_ms': 30000, 'reconnect_policy': '1_attempt_5s',
            'redact_email_in_logs': True
        },
        'users': {}, 'profiles': {}, 'sessions': {}, 'emails': [], 'logs': [], 'layouts': {}, 'personas': {}
    }
    seed_default_persona()
    yield


# Ensure our local 'app' package is used (avoid collision with any installed 'app')
import sys, importlib.util, pathlib
_root = pathlib.Path(__file__).resolve().parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))
pkg_init = _root / "app" / "__init__.py"
spec = importlib.util.spec_from_file_location("app", pkg_init, submodule_search_locations=[str(_root / "app")])
module = importlib.util.module_from_spec(spec)
sys.modules['app'] = module
spec.loader.exec_module(module)


import pytest
try:
    from starlette.testclient import TestClient
except Exception:
    TestClient = None

@pytest.fixture(scope="session")
def client():
    # lazily import to avoid side effects at collection time
    from app.asgi_gateway import asgi
    if TestClient is None:
        pytest.skip("starlette TestClient not available")
    with TestClient(asgi) as c:
        yield c
