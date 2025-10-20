import os
from dataclasses import dataclass
from typing import Optional


def _optional_bool_from_env(name: str) -> Optional[bool]:
    raw = os.getenv(name)
    if raw is None:
        return None
    lowered = raw.strip().lower()
    if lowered in ("1", "true", "yes", "on"):
        return True
    if lowered in ("0", "false", "no", "off"):
        return False
    return None


def _float_from_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _bool_from_env(default: bool, *names: str) -> bool:
    for name in names:
        raw = os.getenv(name)
        if raw is None:
            continue
        lowered = raw.strip().lower()
        return lowered not in ("0", "false", "no", "off")
    return default


_ADVANCED_LOGGING_DEFAULT = _bool_from_env(
    True,
    "VOICE_ADVANCED_LOGGING_ENABLED",
    "ADVANCED_LOGGING_ENABLED",
)

@dataclass
class Settings:
    admin_emails: str = os.getenv("ADMIN_EMAILS", "")
    database_url: str = os.getenv("DATABASE_URL", "postgresql://user:pass@localhost:5432/ask_chip?sslmode=require")
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "mock-openai-key")
    openai_model: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    openai_stt_language: str = os.getenv("OPENAI_STT_LANGUAGE", "en")
    elevenlabs_api_key: str = os.getenv("ELEVENLABS_API_KEY", "mock-eleven-key")
    eleven_voice_id: str = os.getenv("ELEVENLABS_VOICE_ID", "mock-voice")
    eleven_output_format: str = os.getenv("ELEVEN_OUTPUT_FORMAT", "mp3_44100_128")
    eleven_output_format_ws: str = os.getenv("ELEVEN_OUTPUT_FORMAT_WS", "mp3_44100_128")
    email_host: str = os.getenv("EMAIL_HOST", "mock.smtp.local")
    email_port: int = int(os.getenv("EMAIL_PORT", "587"))
    email_use_tls: bool = os.getenv("EMAIL_USE_TLS", "true").lower() == "true"
    email_host_user: str = os.getenv("EMAIL_HOST_USER", "mock-user")
    email_host_password: str = os.getenv("EMAIL_HOST_PASSWORD", "mock-pass")
    email_from_name: str = os.getenv("EMAIL_FROM_NAME", "Chip")
    from_email: str = os.getenv("FROM_EMAIL", "chip@example.com")
    feature_admin_ui: bool = os.getenv("FEATURE_ADMIN_UI", "true").lower() == "true"
    feature_audio: bool = os.getenv("FEATURE_AUDIO", "true").lower() == "true"
    feature_tools: bool = os.getenv("FEATURE_TOOLS", "false").lower() == "true"
    enable_chip_foundation: bool = os.getenv("ENABLE_CHIP_FOUNDATION", "1").strip().lower() not in ("0", "false", "no")
    enable_policy_chips: bool = os.getenv("ENABLE_POLICY_CHIPS", "1").strip().lower() not in ("0", "false", "no")
    enable_nlu_logging: Optional[bool] = _optional_bool_from_env("ENABLE_NLU_LOGGING")
    advanced_logging_enabled: bool = _ADVANCED_LOGGING_DEFAULT
    suggestion_max: int = int(os.getenv("SUGGESTION_MAX", "4"))
    secret_key: str = os.getenv("SECRET_KEY", "dev-secret")
    session_type: str = os.getenv("SESSION_TYPE", "filesystem")
    ws_ping_interval_ms: int = int(os.getenv("WS_PING_INTERVAL_MS", "25000"))
    vad_base_threshold_db: float = _float_from_env("VAD_BASE_THRESHOLD", 10.0)
    vad_exit_threshold_db: float = _float_from_env("VAD_EXIT_THRESHOLD", 6.0)
    vad_tts_boost_db: float = _float_from_env("VAD_TTS_BOOST", 6.0)
    vad_min_speech_ms: int = int(os.getenv("VAD_MIN_SPEECH_MS", "360"))

def load_settings() -> Settings:
    return Settings()


# Usage caps / rate limits (Phase 3)
MAX_TURN_SEC = int(os.getenv("MAX_TURN_SEC", "30"))
MAX_SESSION_MIN = int(os.getenv("MAX_SESSION_MIN", "15"))
RATE_LIMIT_PER_MIN = int(os.getenv("RATE_LIMIT_PER_MIN", "30"))  # requests per minute
