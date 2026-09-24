from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    nansen_api_key: str = "replace-me"
    nansen_mode: Literal["replay", "live"] = "replay"
    daily_credit_budget: int = 3000
    chains: Annotated[list[str], NoDecode] = ["solana", "base", "bnb"]
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    database_url: str = "sqlite:////data/lp-radar.db"
    dashboard_basic_auth: str = ""
    log_level: str = "INFO"

    @field_validator("chains", mode="before")
    @classmethod
    def _split_chains(cls, value: object) -> object:
        if isinstance(value, str):
            return [chain.strip() for chain in value.split(",") if chain.strip()]
        return value


class DiscoveryConfig(BaseModel):
    max_age_hours: float
    pump_window_hours: float
    min_market_cap_usd: float
    screener_timeframe: str
    poll_seconds: int


class BundleConfig(BaseModel):
    early_window_minutes: int
    max_early_buys: int
    same_second_min_wallets: int
    common_funder_min_wallets: int
    similar_size_max_cv: float
    ignore_funder_labels: list[str]
    distributing_sold_pct: float
    exited_sold_pct: float


class SmartMoneyConfig(BaseModel):
    min_wallets_for_signal: int
    label_weights: dict[str, float]


class ScoringWeightsConfig(BaseModel):
    veto_bundle_supply_pct: float
    veto_sm_net_selling: bool
    veto_sm_in_bundle: bool
    green_min: float
    watch_min: float


class BudgetConfig(BaseModel):
    max_deep_analyses_per_hour: int
    related_wallets_cache_hours: float


class ScoringConfig(BaseModel):
    discovery: DiscoveryConfig
    bundle: BundleConfig
    smart_money: SmartMoneyConfig
    scoring: ScoringWeightsConfig
    budget: BudgetConfig


@lru_cache
def get_settings() -> Settings:
    return Settings()


@lru_cache
def load_scoring_config(path: Path = Path("config/scoring.yaml")) -> ScoringConfig:
    with path.open() as f:
        raw = yaml.safe_load(f)
    return ScoringConfig.model_validate(raw)
