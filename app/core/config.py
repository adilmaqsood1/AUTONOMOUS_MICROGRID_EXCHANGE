"""Application configuration loaded from environment variables (no hard-coded secrets)."""
from decimal import Decimal
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="MGX_", extra="ignore")

    app_name: str = "Autonomous Microgrid Exchange"
    environment: str = "development"

    database_url: str = "postgresql+asyncpg://mgx:mgx@localhost:5432/mgx"
    redis_url: str | None = "redis://localhost:6379/0"

    # No default secret: must be provided. Tests / dev may set MGX_JWT_SECRET.
    jwt_secret: str = Field(default="")
    jwt_algorithm: str = "HS256"
    access_token_minutes: int = 60

    rate_limit_per_minute: int = 600
    cors_origins: list[str] = ["*"]

    # Reserve policy (forecast-risk adjustment), configurable.
    uncertainty_reserve_factor: Decimal = Decimal("1.0")   # multiplier on (upper-lower)/2
    operational_reserve_fraction: Decimal = Decimal("0.08")  # of predicted energy
    min_reliability_for_trading: float = 0.3
    system_reserve_margin_fraction: float = 0.05

    grid_carbon_intensity_kg_per_kwh: float = 0.45
    renewable_carbon_intensity_kg_per_kwh: float = 0.02

    # Settlement
    imbalance_penalty_multiplier: Decimal = Decimal("1.5")
    collateral_fraction: Decimal = Decimal("1.0")  # of notional value

    enable_scheduler: bool = False

    # Blockchain (Phase 3)
    blockchain_mode: str = "mock"  # "mock" | "evm"
    evm_rpc_url: str = "http://127.0.0.1:8545"
    evm_chain_id: int = 31337
    block_confirmations_required: int = 1

    # Contract addresses
    grid_usd_address: str = ""
    market_registry_address: str = ""
    energy_settlement_address: str = ""
    delivery_oracle_address: str = ""
    eac_contract_address: str = ""
    reserve_settlement_address: str = ""

    # Sensitive dev keys (never logged or exposed in APIs)
    deployer_private_key: str = ""
    oracle_private_key: str = ""
    master_key_secret: str = Field(default="")

    # AI Agents & Intelligence (Phase 4)
    ai_provider: str = "mock"  # "mock" | "openai_compatible"
    ai_base_url: str = "https://api.openai.com/v1"
    ai_api_key: str = ""
    ai_model: str = "gpt-4o-mini"
    ai_timeout_seconds: float = 5.0
    ai_temperature: float = 0.2
    ai_max_tokens: int = 800
    ai_max_concurrency: int = 10
    ai_circuit_breaker_failure_threshold: int = 5
    ai_circuit_breaker_cooldown_seconds: float = 30.0
    default_agent_strategy_mode: str = "ai_assisted"

    def require_secret(self) -> str:
        if not self.jwt_secret:
            raise RuntimeError("MGX_JWT_SECRET must be set (see .env.example)")
        return self.jwt_secret


@lru_cache
def get_settings() -> Settings:
    return Settings()
