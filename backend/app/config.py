from functools import lru_cache
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    backend_host: str = "0.0.0.0"
    backend_port: int = 8000
    database_url: str = "sqlite:///./data/app.sqlite3"
    cors_origins: str = "http://localhost:5173"
    mrkt_api_url: str = "https://api.tgmrkt.io/api/v1"
    mrkt_max_price: float = 50
    mrkt_min_model_floor: float = 50
    mrkt_min_gift_floor: float = 0
    mrkt_premium_backdrops: str = (
        "Black,White,Platinum,Silver,Electric Purple,Cyberpunk,Electric Indigo,Neon Blue,"
        "Azure Blue,Sapphire,Sky Blue,Mint Green,Emerald,Malachite,Aquamarine,Pacific Green,"
        "Lavender,Purple,Violet,Gold,Pure Gold,Satin Gold,Ruby,Crimson,Fuchsia,Magenta"
    )
    research_interval_seconds: int = 180

    telegram_api_id: int | None = None
    telegram_api_hash: str | None = None
    telegram_session: str | None = None
    telegram_bot_token: str | None = None
    telegram_alert_chat_id: str | None = None
    telegram_webhook_secret: str | None = None
    public_base_url: str | None = None
    mrkt_auth_token: str | None = Field(default=None, description="Optional cached MRKT token")

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def premium_backdrop_list(self) -> list[str]:
        return [item.strip() for item in self.mrkt_premium_backdrops.split(",") if item.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
