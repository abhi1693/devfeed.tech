"""User inbox credentials are independent of admin identity and management keys."""

from functools import lru_cache

from devfeed_core.notification_config import ChimelySettings
from pydantic import SecretStr, model_validator


class Settings(ChimelySettings):
    chimely_user_hmac_secret: SecretStr | None = None

    @model_validator(mode="after")
    def validate_inbox(self):
        if (
            self.notifications_enabled
            and self.chimely_user_environment
            and (
                not self.chimely_user_hmac_secret
                or self.chimely_user_hmac_secret.get_secret_value().strip() in {"", "replace-me"}
            )
        ):
            raise ValueError("User inbox requires DEVFEED_CHIMELY_USER_HMAC_SECRET")
        return self


@lru_cache
def get_settings():
    return Settings()
