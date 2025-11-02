import unittest

from app.services.streaming_asr.speechmatics_client import _is_fatal_concurrency_notice


class TestSpeechmaticsConcurrencyNotices(unittest.TestCase):
    def test_info_notice_not_fatal(self) -> None:
        payload = {"message": "Info", "type": "concurrent_session_usage"}
        self.assertFalse(_is_fatal_concurrency_notice(payload))

    def test_warning_notice_not_fatal(self) -> None:
        payload = {"message": "Warning", "severity": "warning", "type": "concurrent_session_limit"}
        self.assertFalse(_is_fatal_concurrency_notice(payload))

    def test_error_notice_fatal(self) -> None:
        payload = {"message": "Error", "type": "concurrent_session_usage"}
        self.assertTrue(_is_fatal_concurrency_notice(payload))

    def test_explicit_severity_error_fatal(self) -> None:
        payload = {"severity": "critical", "type": "concurrent_session_usage"}
        self.assertTrue(_is_fatal_concurrency_notice(payload))


if __name__ == "__main__":
    unittest.main()
