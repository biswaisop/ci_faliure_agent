import os
from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

load_dotenv()

class Settings(BaseSettings):
    GITHUB_APP_ID: str = os.getenv("GITHUB_APP_ID")
    GITHUB_PRIVATE_KEY_PATH: str = os.getenv("GITHUB_PRIVATE_KEY_PATH")
    GITHUB_WEBHOOK_SECRET: str = os.getenv("GITHUB_WEBHOOK_SECRET")
    GITHUB_APP_SLUG: str = os.getenv("GITHUB_APP_SLUG", "")
    GITHUB_APP_CLIENT_ID: str = os.getenv("GITHUB_APP_CLIENT_ID", "")
    GITHUB_APP_CLIENT_SECRET: str = os.getenv("GITHUB_APP_CLIENT_SECRET", "")
    

settings = Settings()
