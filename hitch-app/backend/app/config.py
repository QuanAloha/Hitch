from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://hitch:hitch@localhost:5432/hitch"
    secret_key: str
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60

    valhalla_url: str = "http://localhost:8002"
    valhalla_costing: str = "auto"
    max_stops_per_shipment: int = 50


settings = Settings()
