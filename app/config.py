import os

class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "dev")
    DATABASE_URL = os.getenv("DATABASE_URL", "")
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
    ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
    MAX_RESPONSE_WORDS = int(os.getenv("MAX_RESPONSE_WORDS", "30"))
    JSON_SORT_KEYS = False

class DevConfig(Config):
    DEBUG = True

class ProdConfig(Config):
    DEBUG = False
