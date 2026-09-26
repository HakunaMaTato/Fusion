from typing import Literal

from pydantic import BaseModel

from app.config import ScoringWeightsConfig
from app.pipeline.bundle import BundleAnalysis
from app.pipeline.smart_money import SmartMoneyMetrics

Verdict = Literal["GREEN", "WATCH", "AVOID"]

VETO_BUNDLE_SUPPLY = "bundle_supply"
VETO_SM_NET_SELLING = "sm_net_selling"
VETO_SM_IN_BUNDLE = "sm_in_bundle"


class ScoreInputs(BaseModel):
    smart_money: SmartMoneyMetrics
    bundle: BundleAnalysis
    smart_wallets_in_bundle: frozenset[str]
    volume_usd: float | None
    liquidity_usd: float | None
    token_age_hours: float | None


class ScoreResult(BaseModel):
    score: float
    verdict: Verdict
    vetoes: list[str]
    reasons: list[str]
    components: dict[str, float]


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def score_token(inputs: ScoreInputs, cfg: ScoringWeightsConfig) -> ScoreResult:
    sm, bundle = inputs.smart_money, inputs.bundle
    reasons: list[str] = []
    notes: list[str] = []

    vetoes: list[str] = []
    bundle_size = len(bundle.bundled_wallets)
    if bundle.supply_pct > cfg.veto_bundle_supply_pct:
        vetoes.append(VETO_BUNDLE_SUPPLY)
        reasons.append(
            f"bundle of {bundle_size} wallets holds {bundle.supply_pct:.0f}% of supply "
            f"(veto above {cfg.veto_bundle_supply_pct:g}%)"
        )
    if cfg.veto_sm_net_selling and sm.net_selling:
        vetoes.append(VETO_SM_NET_SELLING)
        reasons.append(f"smart money net selling ${abs(sm.net_flow_usd):,.0f} in the last 24h")
    if cfg.veto_sm_in_bundle and inputs.smart_wallets_in_bundle:
        vetoes.append(VETO_SM_IN_BUNDLE)
        reasons.append(f"{len(inputs.smart_wallets_in_bundle)} smart wallet(s) are inside a bundle")

    participation = _clamp01(sm.weighted_score / cfg.full_credit_weighted_score)
    holding = _clamp01(sm.holding_ratio) if sm.holding_ratio is not None else 0.0
    supply = _clamp01(1 - bundle.supply_pct / cfg.veto_bundle_supply_pct)
    status = cfg.status_scores[bundle.status]

    volume_liquidity = 0.0
    if inputs.volume_usd is not None and inputs.liquidity_usd:
        ratio = inputs.volume_usd / inputs.liquidity_usd
        volume_liquidity = _clamp01(ratio / cfg.full_credit_volume_liquidity_ratio)
        notes.append(f"volume/liquidity {ratio:.1f}x")
    else:
        notes.append("volume or liquidity unavailable")

    holder_growth = 0.0
    if sm.total_holders is not None and inputs.token_age_hours is not None:
        rate = sm.total_holders / max(inputs.token_age_hours, 1.0)
        holder_growth = _clamp01(rate / cfg.full_credit_holders_per_hour)
        notes.append(f"{rate:.0f} new holders per hour")
    else:
        notes.append("holder count or token age unavailable")

    components = {
        "sm_participation": participation,
        "sm_holding": holding,
        "bundle_supply": supply,
        "bundle_status": status,
        "volume_liquidity": volume_liquidity,
        "holder_growth": holder_growth,
    }
    weights = cfg.weights.model_dump()
    score = (
        100
        * sum(weights[name] * value for name, value in components.items())
        / sum(weights.values())
    )

    if sm.wallet_count == 0:
        reasons.append("no smart money buyers")
    else:
        detail = f"{sm.wallet_count} smart wallets (weighted {sm.weighted_score:.1f})"
        if sm.holding_ratio is not None:
            detail += f", still holding {sm.holding_ratio:.0%}"
        reasons.append(detail)
    if bundle_size == 0:
        reasons.append("no bundle detected")
    elif VETO_BUNDLE_SUPPLY not in vetoes:
        reasons.append(
            f"bundle of {bundle_size} wallets holds {bundle.supply_pct:.0f}% of supply, "
            f"status {bundle.status}"
        )
    reasons.extend(notes)

    verdict: Verdict
    if vetoes:
        verdict = "AVOID"
    elif score >= cfg.green_min:
        verdict = "GREEN"
    elif score >= cfg.watch_min:
        verdict = "WATCH"
    else:
        verdict = "AVOID"

    return ScoreResult(
        score=score, verdict=verdict, vetoes=vetoes, reasons=reasons, components=components
    )
