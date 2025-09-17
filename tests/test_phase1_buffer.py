
def test_turn_buffer():
    from app.ws.turn_buffer import TurnBuffer
    tb = TurnBuffer()
    tb.append(b'\x01\x02')
    tb.append(b'\x03')
    tid, data = tb.close_turn()
    assert tid==1 and data==b'\x01\x02\x03'
    tb.append(b'\x04')
    tid2, data2 = tb.close_turn()
    assert tid2==2 and data2==b'\x04'
