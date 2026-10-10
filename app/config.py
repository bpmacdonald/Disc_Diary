from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    postgres_user: str
    postgres_password: str
    postgres_db: str
    database_url: str
    mb_user_agent: str
    secret_key: str = Field(min_length=32)
    cache_staleness_days: int = 30
    debug: bool = False  # False => Secure session cookies
    session_max_age_seconds: int = 14 * 24 * 3600


settings = Settings()
