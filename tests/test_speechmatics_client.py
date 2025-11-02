import unittest

from app.services.streaming_asr.speechmatics_client import (
    _coerce_transcript_text,
    _extract_text,
    _is_fatal_concurrency_notice,
)


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


class TestSpeechmaticsExtractText(unittest.TestCase):
    def test_extracts_from_results_alternatives(self) -> None:
        payload = {
            "results": [
                {
                    "alternatives": [
                        {
                            "transcript": "Hello there",
                            "confidence": 0.92,
                        }
                    ]
                }
            ]
        }
        self.assertEqual(_extract_text(payload), "Hello there")

    def test_extracts_from_metadata_tokens(self) -> None:
        payload = {
            "message": "AddPartialTranscript",
            "metadata": {
                "content": [
                    {"type": "word", "text": "Pure"},
                    {"type": "word", "text": " "},
                    {"type": "word", "text": "Storage"},
                ]
            },
        }
        self.assertEqual(_extract_text(payload), "Pure Storage")

    def test_extracts_from_metadata_transcript(self) -> None:
        payload = {
            "message": "AddTranscript",
            "metadata": {"transcript": "flasharray"},
        }
        self.assertEqual(_extract_text(payload), "flasharray")

    def test_token_value_preferred_over_text(self) -> None:
        payload = {
            "message": "AddPartialTranscript",
            "metadata": {
                "content": [
                    {"type": "word", "text": "Hello", "value": "Hello"},
                    {"type": "punctuation", "text": "Slash", "value": "/"},
                    {"type": "word", "text": "Pure", "value": "Pure"},
                ]
            },
        }
        self.assertEqual(_extract_text(payload), "Hello/Pure")

    def test_coerce_transcript_text_handles_tokenized_results(self) -> None:
        payload = {
            "message": "AddTranscript",
            "results": [
                {
                    "alternatives": [
                        {
                            "content": [
                                {"type": "word", "text": "By"},
                                {"type": "punctuation", "text": "."},
                            ],
                        }
                    ]
                }
            ],
        }

        self.assertEqual(_coerce_transcript_text(payload), "By.")


if __name__ == "__main__":
    unittest.main()
