from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "sqlite:///./run_trainer.db"
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    strava_client_id: str = ""
    strava_client_secret: str = ""
    secret_key: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    frontend_url: str = "http://localhost:3000"
    garmin_proxy_url: str = ""  # e.g. http://user:pass@host:port

    class Config:
        env_file = ".env"


settings = Settings()
