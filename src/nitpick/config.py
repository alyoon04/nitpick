from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # GitHub App
    github_app_id: str = ""
    github_private_key_path: str = "./github-private-key.pem"
    github_webhook_secret: str = ""

    # Database
    database_url: str = "postgresql+psycopg://nitpick:nitpick@localhost:5433/nitpick"

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # Anthropic
    anthropic_api_key: str = ""

    # App
    log_level: str = "info"


settings = Settings()
