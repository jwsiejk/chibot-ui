import pytest

from app.asgi_gateway import _derive_scope_origin, _validate_ws_origin


def _build_headers(pairs):
    return [(name.encode("latin1"), value.encode("latin1")) for name, value in pairs]


@pytest.mark.parametrize(
    "scheme, header_pairs, expected_origin, origin_header, expected_allowed",
    [
        (
            "http",
            [("host", "example.com")],
            "http://example.com",
            "http://example.com",
            True,
        ),
        (
            "http",
            [
                ("host", "chibot-ui.onrender.com"),
                ("x-forwarded-proto", "https"),
            ],
            "https://chibot-ui.onrender.com",
            "https://chibot-ui.onrender.com",
            True,
        ),
        (
            "http",
            [
                ("host", "chibot-ui.onrender.com"),
                ("forwarded", "proto=https; host=chibot-ui.onrender.com"),
            ],
            "https://chibot-ui.onrender.com",
            "https://chibot-ui.onrender.com",
            True,
        ),
        (
            "http",
            [
                ("host", "app.example.com"),
                ("x-forwarded-proto", "https"),
                ("x-forwarded-host", "app.example.com"),
                ("x-forwarded-port", "8443"),
            ],
            "https://app.example.com:8443",
            "https://app.example.com:8443",
            True,
        ),
        (
            "http",
            [
                ("host", "a.example.com"),
                ("x-forwarded-proto", "https"),
            ],
            "https://a.example.com",
            "https://b.example.com",
            False,
        ),
    ],
)
def test_derive_scope_origin_forwarded(monkeypatch, scheme, header_pairs, expected_origin, origin_header, expected_allowed):
    monkeypatch.delenv("ASKCHIP_WS_ALLOWED_ORIGINS", raising=False)

    headers = _build_headers(header_pairs)
    scope = {
        "type": "websocket",
        "scheme": scheme,
        "headers": headers,
    }

    derived = _derive_scope_origin(scope)
    assert derived == expected_origin

    headers_with_origin = list(headers)
    headers_with_origin.append((b"origin", origin_header.encode("latin1")))
    scope_with_origin = dict(scope)
    scope_with_origin["headers"] = headers_with_origin

    allowed, blocked_origin = _validate_ws_origin(scope_with_origin)
    assert allowed is expected_allowed
    if expected_allowed:
        assert blocked_origin is None
    else:
        assert blocked_origin == origin_header
