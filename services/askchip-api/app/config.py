from pydantic import BaseModel


class Settings(BaseModel):
    host: str = '127.0.0.1'
    port: int = 8000
    app_name: str = 'AskChip Local API'


settings = Settings()
