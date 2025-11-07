import logging

import pytest

from app.services.asr.policies import to_sm_params


def test_max_delay_clamped_emits_log(caplog):
    policy = {"speechmatics": {"max_final_latency_ms": 400}}

    logger_name = "app.services.asr.policies"
    with caplog.at_level(logging.INFO, logger=logger_name):
        params = to_sm_params(policy)

    transcription = params["transcription_config"]

    assert pytest.approx(transcription["max_delay"], rel=0, abs=1e-6) == 0.7
    assert any("sm.max_delay.clamped" in message for message in caplog.messages)
