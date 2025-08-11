# server/tts_bridge.py
import os, json, asyncio, websockets, base64

ELEVEN_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
ELEVEN_MODEL_ID = os.getenv("ELEVEN_MODEL_ID", "eleven_flash_v2_5")
ELEVEN_VOICE_ID = os.getenv("ELEVEN_VOICE_ID", "Xb7hH8MSUJpSbSDYk0k2")  # <- replace
ELEVEN_OUTPUT_FORMAT = os.getenv("ELEVEN_OUTPUT_FORMAT", "pcm_24000")      # PCM S16LE
ELEVEN_INACTIVITY = int(os.getenv("ELEVEN_INACTIVITY", "180"))             # keep-alive seconds
ELEVEN_CHUNK_SCHEDULE = os.getenv("ELEVEN_CHUNK_SCHEDULE", "120,160,250,290")

class ElevenLabsRealtimeClient:
    """
    Minimal ElevenLabs WS client for one active session.
    Call connect() once, then send_text(..., flush=True) per turn, and iterate audio via iter_audio().
    """
    def __init__(self,
                 xi_api_key: str = ELEVEN_API_KEY,
                 voice_id: str = ELEVEN_VOICE_ID,
                 model_id: str = ELEVEN_MODEL_ID,
                 output_format: str = ELEVEN_OUTPUT_FORMAT,
                 inactivity_timeout: int = ELEVEN_INACTIVITY,
                 chunk_length_schedule=None):
        if not xi_api_key:
            raise RuntimeError("ELEVENLABS_API_KEY not set")
        self.api_key = xi_api_key
        self.voice_id = voice_id
        self.model_id = model_id
        self.output_format = output_format   # pcm_16000 / pcm_22050 / pcm_24000 / pcm_44100
        self.inactivity_timeout = inactivity_timeout
        self.chunk_length_schedule = chunk_length_schedule or [
            int(x) for x in str(ELEVEN_CHUNK_SCHEDULE).split(",") if x.strip().isdigit()
        ]
        self.ws = None
        self._open = False

    async def connect(self):
        uri = (
            f"wss://api.elevenlabs.io/v1/text-to-speech/{self.voice_id}/stream-input"
            f"?model_id={self.model_id}"
            f"&output_format={self.output_format}"
            f"&inactivity_timeout={self.inactivity_timeout}"
        )
        # Official docs show passing xi_api_key in the first message; headers also work. :contentReference[oaicite:3]{index=3}
        self.ws = await websockets.connect(uri, max_size=None)
        init = {
            "text": " ",  # keep-alive (space keeps socket open; empty string would close it) :contentReference[oaicite:4]{index=4}
            "xi_api_key": self.api_key,
            "voice_settings": {
                "stability": 0.35, "similarity_boost": 0.85, "use_speaker_boost": True, "speed": 1.0
            },
            "generation_config": { "chunk_length_schedule": self.chunk_length_schedule }
        }
        await self.ws.send(json.dumps(init))
        self._open = True

    async def close(self):
        if self.ws:
            try:
                # Empty text closes and flushes buffered text per docs. :contentReference[oaicite:5]{index=5}
                await self.ws.send(json.dumps({"text": ""}))
            except:
                pass
            try:
                await self.ws.close()
            finally:
                self._open = False
                self.ws = None

    async def send_text(self, text: str, flush: bool = False):
        if not self._open:
            await self.connect()
        payload = {"text": text}
        if flush:
            payload["flush"] = True  # forces generation of buffered text. :contentReference[oaicite:6]{index=6}
        await self.ws.send(json.dumps(payload))

    async def iter_audio(self):
        """
        Yields tuples (b16_base64, is_final) where b16 is base64 of PCM S16LE frames.
        """
        if not self._open:
            raise RuntimeError("connect() first")
        async for message in self.ws:
            data = json.loads(message)
            b64 = data.get("audio")
            if b64:
                # ElevenLabs sends base64-encoded audio; with output_format=pcm_24000 this is S16LE mono.
                yield b64, bool(data.get("isFinal", False))
            if data.get("isFinal"):
                break
