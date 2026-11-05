from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    APP_ENV: str = "dev"
    PAYPAL_MODE: str = "fake"  # 'live' (sandbox API) or 'fake' (deterministic offline)
    API_KEY: str = "copilot-secret-key-12345"
    DATABASE_URL: str = "sqlite:///./copilot.db"
    PUBLIC_BASE_URL: str = "http://localhost:8000"

    PAYPAL_CLIENT_ID: Optional[str] = "mock_client_id"
    PAYPAL_CLIENT_SECRET: Optional[str] = "mock_client_secret"
    PAYPAL_BASE_URL: str = "https://api-m.sandbox.paypal.com"
    PAYPAL_WEBHOOK_ID: Optional[str] = "mock_webhook_id"
    PAYPAL_MERCHANT_EMAIL: Optional[str] = "sb-merchant@business.example.com"

    ANTHROPIC_API_KEY: Optional[str] = "mock_anthropic_key"
    LLM_MODEL: str = "claude-3-5-sonnet-20241022"
    LLM_MAX_TOOL_ITERATIONS: int = 8

    DEFAULT_CURRENCY: str = "USD"
    POLICY_AUTO_APPROVE_REMINDERS: bool = True
    POLICY_MAX_PAYOUT_PER_TX: float = 500.0
    POLICY_MAX_PAYOUT_PER_DAY: float = 1500.0
    POLICY_MAX_DISCOUNT_PCT: float = 10.0
    POLICY_REQUIRE_APPROVAL_ABOVE: float = 250.0
    KILL_SWITCH: bool = False

    DEFAULT_TENANT_ID: str = "tenant_default"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )


settings = Settings()

