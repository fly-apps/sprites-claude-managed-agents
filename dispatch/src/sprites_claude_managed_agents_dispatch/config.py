"""Dispatcher configuration."""

from functools import cache

from pydantic import Field, FilePath, field_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    anthropic_environment_id: str
    anthropic_environment_key: str
    anthropic_webhook_secret: str
    anthropic_base_url: str = "https://api.anthropic.com"

    sprite_token: str
    sprites_api_url: str = "https://api.sprites.dev"

    # The prebuilt worker pushed to each Sprite. This should be set by Docker.
    # For local development, see the README.
    vendor_tar_path: FilePath = Field(default=None, validate_default=True)

    @field_validator("vendor_tar_path", mode="before")
    @classmethod
    def _vendor_tar_path_set(cls, value: object) -> object:
        if value is None:
            raise ValueError(
                "VENDOR_TAR_PATH is not set. See the README for instructions"
                " on building the worker closure."
            )
        return value


@cache
def get_settings() -> Settings:
    return Settings()
