from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "desk-curfew-server"
    debug: bool = False
    log_level: str = "INFO"

    server_timezone: str = "UTC"

    database_url: str
    database_url_sync: str

    mqtt_host: str
    mqtt_port: int = 9992
    mqtt_use_ssl: bool = True
    mqtt_username: str
    mqtt_password: str
    mqtt_topic_prefix: str = "user_cd570e40/curfew/kids"
    mqtt_client_id: str = "desk-curfew-server"
    mqtt_keepalive: int = 60

    # clusterfly.ru просит не чаще 1 обращения в секунду
    mqtt_publish_delay: float = 1.1

    heartbeat_interval: int = 30
    heartbeat_timeout: int = 90

    default_pcs: str = "evgeny_pc,andrey_pc"

    # Auth / Sessions
    secret_key: str = "change_me_to_random_string"
    session_cookie_name: str = "curfew_session"
    session_max_age: int = 604800


@lru_cache
def get_settings() -> Settings:
    return Settings()