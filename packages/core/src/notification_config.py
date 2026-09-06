"""Shared connection contract; credentials belong to each consuming service."""

from urllib.parse import urlsplit

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ChimelySettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="DEVFEED_", env_file=".env", extra="ignore", hide_input_in_errors=True
    )
    notifications_enabled: bool = False
    chimely_api_url: str | None = None
    chimely_admin_environment: str | None = None
    chimely_user_environment: str | None = None

    @model_validator(mode="after")
    def validate_chimely(self):
        if not self.notifications_enabled:
            return self
        environments = [self.chimely_admin_environment, self.chimely_user_environment]
        if not self.chimely_api_url or not any(environments):
            raise ValueError("Notifications require Chimely API URL and environment")
        url = urlsplit(self.chimely_api_url)
        if (
            url.scheme not in {"https", "http"}
            or not url.hostname
            or url.username
            or url.password
            or url.path not in {"", "/"}
            or url.query
            or url.fragment
            or any(char.isspace() for char in self.chimely_api_url)
        ):
            raise ValueError("Chimely API URL must be an HTTP(S) origin without credentials")
        for environment in environments:
            if environment is not None and (
                not environment
                or environment == "replace-me"
                or len(environment) > 100
                or not all(c.isascii() and (c.isalnum() or c in "-_") for c in environment)
            ):
                raise ValueError("Chimely environment must be an explicit environment slug")
        if (
            self.chimely_admin_environment
            and self.chimely_admin_environment == self.chimely_user_environment
        ):
            raise ValueError("Admin and user notifications must use separate Chimely environments")
        return self
