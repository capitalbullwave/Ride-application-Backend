"""Application configuration — single source of truth for all environment variables."""
from functools import lru_cache
from typing import List

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

    # Firebase (prefer Backend/app/serviceAccountKey.json in production)
    firebase_credentials_path: str = "./app/serviceAccountKey.json"

    # Twilio (Verify API for SMS OTP)
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_verify_service_sid: str = ""
    twilio_phone_number: str = ""  # optional; Verify API does not require it
    # OTP delivery: auto | twilio | local
    # - auto/twilio: send real SMS via Twilio to the entered phone number
    # - local: only for emergency offline testing (hardcoded 123456, no SMS)
    otp_delivery_mode: str = "auto"

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
