def validate_pack(pack: dict) -> tuple[bool, str]:
    if not isinstance(pack, dict):
        return False, "pack_must_be_object"
    allowed_top = {
        "id","public_title","persona_intensity","prompt","policy",
        "lexicon_tweaks","tts","quotes","features"
    }
    forbidden = {'code','script','py','js','exec','eval'}
    if any(k in pack for k in forbidden):
        return False, 'forbidden'
    extra = set(pack.keys()) - allowed_top
    if extra:
        return False, f"unknown_keys: {sorted(list(extra))}"
    if "id" not in pack or not isinstance(pack["id"], str):
        return False, "missing_or_invalid_id"
    # prompt
    pr = pack.get("prompt", {})
    if not isinstance(pr, dict): return False, "prompt_must_be_object"
    if "system" in pr and not isinstance(pr["system"], str): return False, "prompt.system_must_be_string"
    if "guidelines" in pr and not (isinstance(pr["guidelines"], list) and all(isinstance(x,str) for x in pr["guidelines"])):
        return False, "prompt.guidelines_must_be_string_list"
    # policy
    pol = pack.get("policy", {})
    if not isinstance(pol, dict): return False, "policy_must_be_object"
    # lexicon
    if "lexicon_tweaks" in pack and not (isinstance(pack["lexicon_tweaks"], list) and all(isinstance(x,str) for x in pack["lexicon_tweaks"])):
        return False, "lexicon_tweaks_must_be_string_list"
    # tts
    tts = pack.get("tts", {})
    if not isinstance(tts, dict): return False, "tts_must_be_object"
    if "provider" in tts and not isinstance(tts["provider"], str): return False, "tts.provider_must_be_string"
    if "voice_id" in tts and not isinstance(tts["voice_id"], str): return False, "tts.voice_id_must_be_string"
    # quotes
    qt = pack.get("quotes", {})
    if not isinstance(qt, dict): return False, "quotes_must_be_object"
    if "enabled" in qt and not isinstance(qt["enabled"], (bool,)): return False, "quotes.enabled_must_be_bool"
    # features
    feat = pack.get("features", {})
    if not isinstance(feat, dict): return False, "features_must_be_object"
    # no arbitrary code keys allowed
    forbidden = {"code","script","py","js","exec","eval"}
    if any(k in pack for k in forbidden):
        return False, "forbidden_code_key"
    return True, ""
