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
    mrkt_max_price: float = 35
    mrkt_research_max_price: float = 38
    mrkt_min_model_floor: float = 35
    mrkt_min_gift_floor: float = 0
    mrkt_premium_backdrops: str = (
        "Amber,Aquamarine,Azure Blue,Battleship Grey,Black,Burgundy,Carmine,Celtic Blue,"
        "Chestnut,Chocolate,Cobalt Blue,Copper,Crimson,Cyberpunk,Dark Green,Dark Lilac,"
        "Deep Cyan,Desert Sand,Electric Indigo,Electric Purple,Emerald,English Violet,"
        "Fandango,Feldgrau,Fire Engine,French Blue,French Violet,Fuchsia,Gold,Gunmetal,"
        "Hunter Green,Indigo Dye,Lavender,Magenta,Malachite,Midnight Blue,Mint Green,"
        "Mustard,Mystic Pearl,Neon Blue,Onyx Black,Orange,Pacific Green,Platinum,Pure Gold,"
        "Purple,Ruby,Sapphire,Satin Gold,Shamrock Green,Silver,Sky Blue,Steel Grey,Turquoise,"
        "Violet,White"
    )
    research_interval_seconds: int = 180
    keepalive_interval_seconds: int = 240

    telegram_api_id: int | None = None
    telegram_api_hash: str | None = None
    telegram_session: str | None = None
    telegram_bot_token: str | None = None
    telegram_alert_chat_id: str | None = None
    telegram_allowed_chat_ids: str | None = None
    telegram_allowed_user_ids: str | None = None
    telegram_webhook_secret: str | None = None
    public_base_url: str | None = None
    cron_secret: str | None = None
    mrkt_auth_token: str | None = Field(default=None, description="Optional cached MRKT token")

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def premium_backdrop_list(self) -> list[str]:
        return [item.strip() for item in self.mrkt_premium_backdrops.split(",") if item.strip()]

    @property
    def telegram_allowed_chat_id_set(self) -> set[int]:
        return self._parse_int_set(self.telegram_allowed_chat_ids)

    @property
    def telegram_allowed_user_id_set(self) -> set[int]:
        return self._parse_int_set(self.telegram_allowed_user_ids)

    def _parse_int_set(self, value: str | None) -> set[int]:
        if not value:
            return set()
        parsed: set[int] = set()
        for item in value.split(","):
            try:
                parsed.add(int(item.strip()))
            except ValueError:
                continue
        return parsed


@lru_cache
def get_settings() -> Settings:
    return Settings()
