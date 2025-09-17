
def test_schema_helpers():
    from app.ws.schema_v1 import parse_client_json, make_results, make_utterance_end, make_keepalive_ack
    # parse
    assert parse_client_json('{"type":"KeepAlive"}')["type"]=="KeepAlive"
    assert parse_client_json('{"type":"CloseStream"}')["type"]=="CloseStream"
    # results shape
    r = make_results(2, "hi")
    assert r["type"]=="Results"
    assert r["channel"]["alternatives"][0]["transcript"]=="hi"
    assert r["is_final"] is True and r["turn_id"]==2
    # utterance end
    ue = make_utterance_end(3); assert ue=={"type":"UtteranceEnd","turn_id":3}
    # keepalive ack
    ka = make_keepalive_ack(); assert ka=={"type":"KeepAliveAck"}
