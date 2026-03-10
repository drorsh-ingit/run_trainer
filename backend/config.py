from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "sqlite:///./run_trainer.db"
    anthropic_api_key: str = ""
    strava_client_id: str = ""
    strava_client_secret: str = ""
    secret_key: str = "change-me-in-production"
    frontend_url: str = "http://localhost:3000"

    class Config:
        env_file = ".env"


settings = Settings()
