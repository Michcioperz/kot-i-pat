from pydantic import BaseSettings


class Settings(BaseSettings):
    federation_host: str
    db_path: str = "db.sqlite3"

settings = Settings()