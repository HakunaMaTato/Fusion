from datetime import datetime

from pydantic import BaseModel

from app.config import ScoringConfig
from app.nansen.client import NansenClient
from app.pipeline.bundle import BundleAnalysis, analyze_bundle
from app.pipeline.crosscheck import sm_in_bundle
from app.pipeline.discovery import Candidate
from app.pipeline.scoring import ScoreInputs, ScoreResult, score_token
from app.pipeline.smart_money import SmartMoneyMetrics, analyze_smart_money


class MissingDeploymentDate(Exception):
    """The candidate has no deployment time, so the early-buy window cannot be built."""


class TokenAnalysis(BaseModel):
    candidate: Candidate
    bundle: BundleAnalysis
    smart_money: SmartMoneyMetrics
    smart_wallets_in_bundle: frozenset[str]
    score: ScoreResult
    token_age_hours: float
    analyzed_at: datetime


async def analyze_token(
    client: NansenClient, cfg: ScoringConfig, candidate: Candidate, now: datetime
) -> TokenAnalysis:
    deployed_at = candidate.token_deployment_date
    if deployed_at is None:
        raise MissingDeploymentDate(f"{candidate.chain}/{candidate.token_address}")

    bundle = await analyze_bundle(
        client,
        cfg.bundle,
        chain=candidate.chain,
        token_address=candidate.token_address,
        deployed_at=deployed_at,
        deployer=None,
        related_wallets_ttl_seconds=cfg.budget.related_wallets_cache_hours * 3600,
    )
    smart_money = await analyze_smart_money(
        client,
        cfg.smart_money,
        chain=candidate.chain,
        token_address=candidate.token_address,
        deployed_at=deployed_at,
        now=now,
    )
    overlap = sm_in_bundle(smart_money.smart_wallets, bundle)
    age_hours = max(0.0, (now - deployed_at).total_seconds() / 3600)
    score = score_token(
        ScoreInputs(
            smart_money=smart_money,
            bundle=bundle,
            smart_wallets_in_bundle=overlap,
            volume_usd=candidate.volume_usd,
            liquidity_usd=candidate.liquidity_usd,
            token_age_hours=age_hours,
        ),
        cfg.scoring,
    )
    return TokenAnalysis(
        candidate=candidate,
        bundle=bundle,
        smart_money=smart_money,
        smart_wallets_in_bundle=overlap,
        score=score,
        token_age_hours=age_hours,
        analyzed_at=now,
    )
