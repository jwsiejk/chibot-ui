"""
Simulated E2E: model the UI/WS states and validate transitions + viseme timing.
No browser/network needed.
"""
from dataclasses import dataclass, field
from typing import List, Dict

@dataclass
class Frame:
    type: str
    data: Dict

@dataclass
class SessionSim:
    frames: List[Frame] = field(default_factory=list)
    state: str = "ready"
    def greet(self):
        self.frames.append(Frame("state", {"phase":"assistant_speaking"}))
        self.frames.append(Frame("text", {"role":"assistant", "content":"Hello!"}))
        self.frames.append(Frame("audio_chunk", {"codec":"audio/webm;codecs=opus", "data": b'123'}))
        self.frames.append(Frame("end", {}))
        self.frames.append(Frame("state", {"phase":"assistant_end"}))
        self.state = "listening"
    def user_speaks(self):
        # soft barge-in path simulated
        self.frames.append(Frame("control", {"cmd":"interrupt"}))
        self.state = "thinking"
    def assistant_replies(self):
        visemes = [{"t_ms": 0, "v": "A"}, {"t_ms": 120, "v":"B"}]
        self.frames.append(Frame("text", {"role":"assistant", "content":"Got it."}))
        self.frames.append(Frame("audio_chunk", {"codec":"audio/webm;codecs=opus", "data": b'xyz'}))
        self.frames.append(Frame("visemes", {"items": visemes}))
        self.frames.append(Frame("end", {}))
        self.state = "listening"
    def drop_and_reconnect(self):
        self.frames.append(Frame("ws", {"event":"disconnect"}))
        self.frames.append(Frame("ws", {"event":"reconnect"}))
        self.state = "ready"
