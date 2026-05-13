from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Application configuration loaded from environment variables.
    In production (Docker) the variables are injected via env_file/compose,
    so we do NOT require a local .env file inside the container.
    """
    model_config = SettingsConfigDict(
        # env_file = ".env",          # Removed – Docker provides env vars directly
        env_file_encoding = "utf-8",
        case_sensitive = True,
    )

    BOT_TOKEN: str
    DATABASE_URL: str
    BALE_API_URL: str = "https://tapi.bale.ai"


settings = Settings()