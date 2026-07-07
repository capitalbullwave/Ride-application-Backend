"""Application configuration — single source of truth for all environment variables."""
import os
from functools import lru_cache
from typing import List

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Application
    app_name: str = "RideBooking"
    app_env: str = "development"
    debug: bool = True
    secret_key: str = "change-me-in-production"
    api_v1_prefix: str = "/api/v1"

    # Database
    database_url: str = "postgresql+asyncpg://rideuser:ridepass@localhost:5432/ridebooking"
    database_sync_url: str = "postgresql://rideuser:ridepass@localhost:5432/ridebooking"

    # Redis & Celery
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "amqp://guest:guest@localhost:5672//"
    celery_result_backend: str = "redis://localhost:6379/2"

    # JWT
    jwt_secret_key: str = "change-me-jwt-secret"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 7

    # CORS
    cors_origins: str = "http://localhost:3000,http://localhost:3001,http://localhost:3002"

    # Google Maps
    google_maps_api_key: str = ""

    # AWS S3
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""
    aws_s3_bucket: str = "ridebooking-uploads"
    aws_region: str = "us-east-1"
    upload_dir: str = "uploads"

    # Firebase
    firebase_credentials_path: str = "./firebase-credentials.json"

    # Twilio (Verify API for SMS OTP)
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_verify_service_sid: str = ""
    twilio_phone_number: str = ""  # optional; Verify API does not require it

    # Email
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = "noreply@ridebooking.com"

    # Stripe
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""

    # Razorpay
    razorpay_key_id: str = ""
    razorpay_key_secret: str = ""

    # Cashfree
    cashfree_app_id: str = ""
    cashfree_secret_key: str = ""

    # PhonePe
    phonepe_merchant_id: str = ""
    phonepe_salt_key: str = ""

    # Rate limiting
    rate_limit_per_minute: int = 60

    # Driver matching
    driver_search_radius_km: float = 5.0
    driver_request_timeout_seconds: int = 180

    # Pricing
    platform_fee_percent: float = 10.0
    tax_percent: float = 5.0
    night_charge_start_hour: int = 22
    night_charge_end_hour: int = 6
    night_charge_multiplier: float = 1.25
    peak_hour_multiplier: float = 1.5

    # Logging
    log_level: str = "INFO"
    log_json: bool = False

    @field_validator("database_url", mode="before")
    @classmethod
    def normalize_database_url(cls, value: str) -> str:
        """Accept Render/Heroku-style postgres:// and plain postgresql:// URLs."""
        if not isinstance(value, str):
            return value
        if value.startswith("postgres://"):
            value = "postgresql://" + value[len("postgres://") :]
        if value.startswith("postgresql://") and "+" not in value.split("://", 1)[0]:
            value = value.replace("postgresql://", "postgresql+asyncpg://", 1)
        return value

    @model_validator(mode="after")
    def derive_sync_database_url(self) -> "Settings":
        if not os.getenv("DATABASE_SYNC_URL"):
            sync_url = self.database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
            object.__setattr__(self, "database_sync_url", sync_url)
        return self

    @property
    def cors_origins_list(self) -> List[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def is_development(self) -> bool:
        return self.app_env == "development"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
