from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
import yaml

from .service import ProcessWrapper


class Settings(BaseSettings):
    jwt_algo: str | None = None
    jwt_verif_key: str = ""
    api_key: str | None = None
    init_procs: list[ProcessWrapper] | None = None

    model_config = SettingsConfigDict(env_ignore_empty=True)

    @model_validator(mode="after")
    def _check_jwt_verif_key(self):
        if self.jwt_algo is not None and self.jwt_verif_key.strip() == "":
            raise ValueError("`jwt_verif_key` cannot be empty when `jwt_algo` is set")
        return self

    @model_validator(mode="after")
    def _load_init_procs(self):
        if self.init_procs is not None:
            return self

        yaml_path = Path("init_procs.yaml")
        if not yaml_path.exists():
            return self

        with yaml_path.open("r") as f:
            self.init_procs = [ProcessWrapper.model_validate(item) for item in yaml.safe_load(f)]

        return self


settings = Settings()
