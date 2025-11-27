from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    jwt_algo: str | None = None
    jwt_verif_key: str = ""
    api_key: str | None = None

    model_config = SettingsConfigDict(env_ignore_empty=True)

    @model_validator(mode="after")
    def _check_jwt_verif_key(self):
        if self.jwt_algo is not None and self.jwt_verif_key.strip() == "":
            raise ValueError("`jwt_verif_key` cannot be empty when `jwt_algo` is set")
        return self


settings = Settings()
