# app/drain_state.py
_DRAINING = False

def start_draining():
    global _DRAINING
    _DRAINING = True

def is_draining():
    return _DRAINING
