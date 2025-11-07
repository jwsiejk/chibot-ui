from app.services.asr.policies import to_sm_params


def test_punctuation_overrides_object_shape():
    policy = {
        "speechmatics": {
            "punctuation": {
                "overrides": {
                    "permitted_marks": ["?", "!"],
                    "sensitivity": 0.3,
                }
            }
        }
    }

    params = to_sm_params(policy)
    overrides = params["transcription_config"]["punctuation_overrides"]

    assert overrides == {"permitted_marks": ["?", "!"], "sensitivity": 0.3}
