from functools import lru_cache

from devfeed_core.notification_config import ChimelySettings
from pydantic import SecretStr, model_validator


class Settings(ChimelySettings):
    chimely_admin_api_key: SecretStr | None = None
    chimely_user_api_key: SecretStr | None = None

    @model_validator(mode="after")
    def validate_key(self):
        if self.notifications_enabled:
            for audience in ("admin", "user"):
                environment = getattr(self, f"chimely_{audience}_environment")
                secret = getattr(self, f"chimely_{audience}_api_key")
                if environment and (
                    not secret or secret.get_secret_value().strip() in {"", "replace-me"}
                ):
                    raise ValueError(
                        f"Notification delivery requires DEVFEED_CHIMELY_{audience.upper()}_API_KEY"
                    )
            if (
                self.chimely_admin_api_key
                and self.chimely_user_api_key
                and self.chimely_admin_api_key.get_secret_value()
                == self.chimely_user_api_key.get_secret_value()
            ):
                raise ValueError(
                    "Admin and user destinations require different environment API keys"
                )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
