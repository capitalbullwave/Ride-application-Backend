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

    # CORS (comma-separated origins; www/non-www auto-expanded in cors_origins_list)
    admin_panel_url: str = "https://bullwaverides.in"
    cors_origins: str = (
        "http://localhost:3000,http://localhost:3001,http://localhost:3002,"
        "https://bullwaverides.in,https://www.bullwaverides.in"
    )

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

    @staticmethod
    def _normalize_origin(origin: str) -> str:
        origin = origin.strip().rstrip("/")
        if not origin:
            return ""
        if not origin.startswith(("http://", "https://")):
            origin = f"https://{origin}"
        return origin

    @property
    def admin_panel_origins(self) -> List[str]:
        """Always allow the production admin panel (https://bullwaverides.in)."""
        origins: List[str] = []
        for raw in (self.admin_panel_url, "https://www.bullwaverides.in"):
            origin = self._normalize_origin(raw)
            if not origin:
                continue
            origins.append(origin)
            if origin.startswith("https://www."):
                origins.append(origin.replace("https://www.", "https://", 1))
            elif origin.startswith("https://"):
                host = origin[len("https://") :]
                if "localhost" not in host and "127.0.0.1" not in host:
                    origins.append(f"https://www.{host}")
        return list(dict.fromkeys(origins))

    @property
    def cors_origins_list(self) -> List[str]:
        origins: List[str] = []
        for raw in self.cors_origins.split(","):
            origin = self._normalize_origin(raw)
            if not origin:
                continue
            origins.append(origin)
            if origin.startswith("https://www."):
                origins.append(origin.replace("https://www.", "https://", 1))
            elif origin.startswith("https://") and "localhost" not in origin and "127.0.0.1" not in origin:
                host = origin[len("https://") :]
                origins.append(f"https://www.{host}")
            elif origin.startswith("http://www."):
                origins.append(origin.replace("http://www.", "http://", 1))
            elif origin.startswith("http://") and "localhost" not in origin and "127.0.0.1" not in origin:
                host = origin[len("http://") :]
                origins.append(f"http://www.{host}")
        return list(dict.fromkeys(origins))

    @property
    def all_cors_origins(self) -> List[str]:
        return list(dict.fromkeys(self.cors_origins_list + self.admin_panel_origins))

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
