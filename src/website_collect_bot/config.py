from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    telegram_bot_token: str = Field(default="", alias="TELEGRAM_BOT_TOKEN")
    telegram_allowed_chat_id: int | None = Field(default=None, alias="TELEGRAM_ALLOWED_CHAT_ID")

    deepseek_api_key: str = Field(default="", alias="DEEPSEEK_API_KEY")
    deepseek_base_url: str = Field(default="https://api.deepseek.com", alias="DEEPSEEK_BASE_URL")
    deepseek_model: str = Field(default="deepseek-v4-flash", alias="DEEPSEEK_MODEL")

    database_path: Path = Field(default=Path("data/sites.sqlite3"), alias="DATABASE_PATH")
    bot_reply_mode: str = Field(default="brief", alias="BOT_REPLY_MODE")
    web_host: str = Field(default="0.0.0.0", alias="WEB_HOST")
    web_port: int = Field(default=8080, alias="WEB_PORT")
    web_workers: int = Field(default=2, alias="WEB_WORKERS")
    web_dashboard_token: str = Field(default="", alias="WEB_DASHBOARD_TOKEN")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    @field_validator("telegram_allowed_chat_id", mode="before")
    @classmethod
    def empty_chat_id_as_none(cls, value: object) -> object:
        if value == "":
            return None
        return value

    @property
    def is_brief_reply(self) -> bool:
        return self.bot_reply_mode.strip().lower() == "brief"


@lru_cache
def get_settings() -> Settings:
    return Settings()
