
import os, base64

class MockTTS:
    def __init__(self, cfg=None):
        self.cfg = cfg or {}
    def synthesize_with_visemes(self, text: str):
        # Return a tiny silent WAV header or empty mp3; here just minimal bytes
        audio_bytes = b"MOCKAUDIO"
        # simple viseme schedule (fake)
        visemes = [{"t_ms": i*120, "v": v} for i, v in enumerate(["A","B","C","D","E"])]
        return audio_bytes, visemes

class ElevenLabsTTS:
    def __init__(self, cfg=None):
        self.cfg = cfg or {}
    def synthesize_with_visemes(self, text: str):
        # Real wiring stub (not executed in tests)
        # Would call ElevenLabs API, then generate alignment/visemes.
        # Kept as placeholder to show "real" prod path.
        
import os, json
import requests

api_key = os.environ.get("ELEVENLABS_API_KEY")
voice_id = os.environ.get("ELEVENLABS_VOICE_ID", "EXAVITQu4vr4xnSDxMaL")
model_id = os.environ.get("ELEVEN_MODEL_ID", None)
output_format = os.environ.get("ELEVEN_OUTPUT_FORMAT", "mp3_44100_128")

if not api_key:
    raise RuntimeError("ELEVENLABS_API_KEY is not set")

# Basic REST call (non-stream) — in production you may prefer WS streaming for lower latency
url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
headers = {
    "xi-api-key": api_key,
    "accept": "audio/mpeg",
    "Content-Type": "application/json"
}
payload = {"text": text}
if model_id:
    payload["model_id"] = model_id
# Request MP3 bytes
resp = requests.post(url, headers=headers, data=json.dumps(payload), timeout=60)
resp.raise_for_status()
audio_bytes = resp.content
# Approximate viseme schedule using token lengths (placeholder; real alignment can be wired with ElevenLabs alignments)
tokens = text.split()
visemes = []
t = 0
for tok in tokens:
    v = "ABCDE"[len(tok) % 5]
    visemes.append({"t_ms": t, "v": v})
    t += max(80, min(220, 20*len(tok)))
return audio_bytes, visemes


def get_tts_provider():
    use_mock = os.environ.get("USE_MOCK_VENDORS", "0") == "1"
    if use_mock or not os.environ.get("ELEVENLABS_API_KEY"):
        return MockTTS()
    return ElevenLabsTTS()
