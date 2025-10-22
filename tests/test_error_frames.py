"""Tests for WebSocket error frame helpers."""
from app.ws.errors import ErrorCode, make_error


def test_error_codes_have_expected_shape():
    message = "stub"
    for code in ErrorCode:
        frame = make_error(code, message, retryable=code in {ErrorCode.RATE_LIMITED, ErrorCode.PROVIDER_DOWN})
        assert frame["type"] == "error"
        assert frame["code"] == code.value
        assert frame["message"] == message
        assert isinstance(frame["retryable"], bool)
        assert ("retry_in_ms" in frame) is False


def test_retry_in_ms_optional():
    frame = make_error(ErrorCode.RATE_LIMITED, "Too many requests", retryable=True, retry_in_ms=1500)
    assert frame["retry_in_ms"] == 1500
    assert frame["retryable"] is True
