import sys
from pathlib import Path
BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
from tests.e2e_sim.state_machine import SessionSim

def run():
    sim = SessionSim()
    sim.greet()
    sim.user_speaks()
    sim.assistant_replies()
    sim.drop_and_reconnect()
    # Assertions
    types = [f.type for f in sim.frames]
    assert types[0]=="state" and types[1]=="text" and types[2]=="audio_chunk"
    # viseme timing present
    visemes = [f for f in sim.frames if f.type=="visemes"][0].data["items"]
    assert all("t_ms" in v and "v" in v for v in visemes)
    # reconnect events
    assert {"event":"disconnect"} in [f.data for f in sim.frames if f.type=="ws"]
    assert {"event":"reconnect"} in [f.data for f in sim.frames if f.type=="ws"]
    print("PHASE19: PASS")

if __name__ == "__main__":
    run()
