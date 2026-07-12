from __future__ import annotations

import os

from pydantic_settings import BaseSettings


def _normalize_database_url(url: str) -> str:
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql://", 1)
    return url


class Settings(BaseSettings):
    telegram_bot_token: str = ""
    telegram_allowed_user_ids: str = ""
    telegram_webhook_secret: str = ""
    telegram_mode: str = "auto"  # auto | webhook | polling
    dashboard_url: str = "http://localhost:8000"
    host: str = "0.0.0.0"
    port: int = 8000
    database_url: str = "sqlite:///./house_hunt.db"
    run_mode: str = "all"  # all | api | bot

    class Config:
        env_file = ".env"
        extra = "ignore"

    @property
    def allowed_user_ids(self) -> set[int]:
        if not self.telegram_allowed_user_ids.strip():
            return set()
        return {int(x.strip()) for x in self.telegram_allowed_user_ids.split(",") if x.strip()}

    @property
    def resolved_database_url(self) -> str:
        url = os.getenv("POSTGRES_URL") or os.getenv("DATABASE_URL") or self.database_url
        if not url:
            url = "sqlite:///./house_hunt.db"
        return _normalize_database_url(url)

    @property
    def use_webhook(self) -> bool:
        mode = self.telegram_mode.lower()
        if mode == "polling":
            return False
        if mode == "webhook":
            return True
        return bool(os.getenv("VERCEL")) or self.run_mode == "api"


settings = Settings()
