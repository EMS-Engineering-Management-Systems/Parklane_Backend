"""Typed settings, loaded from the environment and `.env`."""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # -- MongoDB ------------------------------------------------------------
    mongodb_uri: str = "mongodb://localhost:27017"
    mongodb_password: str = ""
    mongodb_db: str = "Atal"
    # Resident / community data lives in its own database on the same server.
    mongodb_users_db: str = "Atal_Users"

    # -- Server -------------------------------------------------------------
    # 4000 clashes with NoMachine's nxd service on Windows; 8000 is uvicorn's default.
    port: int = 8000
    host: str = "0.0.0.0"
    app_env: str = "development"
    cors_origin: str = "*"

    # -- Domain -------------------------------------------------------------
    # NoDecode: without it pydantic-settings tries to JSON-decode the raw
    # env string before the validator below gets a chance to split it.
    granularities: Annotated[list[str], NoDecode] = ["instant", "monthly", "yearly"]
    default_page_size: int = 100
    max_page_size: int = 1000

    @field_validator("granularities", mode="before")
    @classmethod
    def _split_granularities(cls, value: object) -> object:
        """`GRANULARITIES=instant,monthly,yearly` arrives as a bare string."""
        if isinstance(value, str):
            return [item.strip().lower() for item in value.split(",") if item.strip()]
        return value

    @property
    def raw_granularity(self) -> str:
        """The tier holding raw samples; rollups read from it."""
        return self.granularities[0] if self.granularities else "instant"

    @property
    def rolled_granularities(self) -> list[str]:
        return [g for g in self.granularities if g != self.raw_granularity]

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origin.split(",") if origin.strip()]

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
