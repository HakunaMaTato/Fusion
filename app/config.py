from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator
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
    dashboard_base_url: str = ""

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
    smart_money_poll_seconds: int


class BundleConfig(BaseModel):
    early_window_minutes: int
    max_early_buys: int
    max_funder_lookups: int
    funder_relations: list[str]
    same_second_min_wallets: int
    common_funder_min_wallets: int
    similar_size_max_cv: float
    ignore_funder_labels: list[str]
    distributing_sold_pct: float
    exited_sold_pct: float


class SmartMoneyConfig(BaseModel):
    min_wallets_for_signal: int
    default_label_weight: float
    flow_window_hours: float
    label_weights: dict[str, float]


class ScoreWeights(BaseModel):
    sm_participation: float
    sm_holding: float
    bundle_supply: float
    bundle_status: float
    volume_liquidity: float
    holder_growth: float


class ScoringWeightsConfig(BaseModel):
    veto_bundle_supply_pct: float = Field(gt=0)
    veto_sm_net_selling: bool
    veto_sm_in_bundle: bool
    green_min: float
    watch_min: float
    weights: ScoreWeights
    status_scores: dict[str, float]
    full_credit_weighted_score: float = Field(gt=0)
    full_credit_volume_liquidity_ratio: float = Field(gt=0)
    full_credit_holders_per_hour: float = Field(gt=0)

    @model_validator(mode="after")
    def _check_consistency(self) -> "ScoringWeightsConfig":
        missing = {"none", "holding", "distributing", "exited"} - set(self.status_scores)
        if missing:
            raise ValueError(f"status_scores is missing {sorted(missing)}")
        if sum(self.weights.model_dump().values()) <= 0:
            raise ValueError("scoring weights must sum to more than 0")
        return self


class BudgetConfig(BaseModel):
    max_deep_analyses_per_hour: int
    related_wallets_cache_hours: float
    estimated_analysis_credits: float
    estimated_reeval_credits: float


class MonitorConfig(BaseModel):
    reeval_seconds: int


class AlertsConfig(BaseModel):
    cooldown_minutes: float


class BacktestConfig(BaseModel):
    days: int
    lag_days: int
    min_volume_usd: float
    max_candidates_per_day_chain: int
    candle_timeframe: str
    max_trade_pages: int
    tier_pages: int
    exit_trade_pages: int
    dump_threshold_pct: float
    horizons_hours: list[int]


class ScoringConfig(BaseModel):
    discovery: DiscoveryConfig
    bundle: BundleConfig
    smart_money: SmartMoneyConfig
    scoring: ScoringWeightsConfig
    budget: BudgetConfig
    monitor: MonitorConfig
    alerts: AlertsConfig
    backtest: BacktestConfig


@lru_cache
def get_settings() -> Settings:
    return Settings()


DEFAULT_SCORING_PATH = Path(__file__).resolve().parents[1] / "config" / "scoring.yaml"


@lru_cache
def load_scoring_config(path: Path = DEFAULT_SCORING_PATH) -> ScoringConfig:
    with path.open() as f:
        raw = yaml.safe_load(f)
    return ScoringConfig.model_validate(raw)
