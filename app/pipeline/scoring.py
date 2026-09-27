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


Polarity = Literal["positive", "risk"]


class Reason(BaseModel):
    """UI_REDESIGN.md §7 item 1: a score reason with its own polarity, so the dashboard can split
    "Why this verdict" into Strengths and Risks instead of one flat, unordered list."""

    text: str
    polarity: Polarity
    component: str | None = None  # one of ScoreResult.components' keys, when it maps to one


class ScoreResult(BaseModel):
    score: float
    verdict: Verdict
    vetoes: list[str]
    reasons: list[Reason]
    components: dict[str, float]


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def score_token(
    inputs: ScoreInputs,
    cfg: ScoringWeightsConfig,
    unavailable: frozenset[str] = frozenset(),
) -> ScoreResult:
    """Score a token. Components named in `unavailable` (data that does not exist, for example in
    a backtest) are left out of both the sum and the weights, so the score is re-weighted over the
    components that remain."""
    sm, bundle = inputs.smart_money, inputs.bundle
    reasons: list[Reason] = []
    notes: list[Reason] = []

    vetoes: list[str] = []
    bundle_size = len(bundle.bundled_wallets)
    if bundle.supply_pct > cfg.veto_bundle_supply_pct:
        vetoes.append(VETO_BUNDLE_SUPPLY)
        reasons.append(
            Reason(
                text=(
                    f"bundle of {bundle_size} wallets holds {bundle.supply_pct:.0f}% of supply "
                    f"(veto above {cfg.veto_bundle_supply_pct:g}%)"
                ),
                polarity="risk",
                component="bundle_supply",
            )
        )
    if cfg.veto_sm_net_selling and sm.net_selling:
        vetoes.append(VETO_SM_NET_SELLING)
        reasons.append(
            Reason(
                text=f"smart money net selling ${abs(sm.net_flow_usd):,.0f} in the last 24h",
                polarity="risk",
                component="sm_holding",
            )
        )
    if cfg.veto_sm_in_bundle and inputs.smart_wallets_in_bundle:
        vetoes.append(VETO_SM_IN_BUNDLE)
        reasons.append(
            Reason(
                text=f"{len(inputs.smart_wallets_in_bundle)} smart wallet(s) are inside a bundle",
                polarity="risk",
                component="sm_participation",
            )
        )

    participation = _clamp01(sm.weighted_score / cfg.full_credit_weighted_score)
    holding = _clamp01(sm.holding_ratio) if sm.holding_ratio is not None else 0.0
    supply = _clamp01(1 - bundle.supply_pct / cfg.veto_bundle_supply_pct)
    status = cfg.status_scores[bundle.status]

    volume_liquidity = 0.0
    if inputs.volume_usd is not None and inputs.liquidity_usd:
        ratio = inputs.volume_usd / inputs.liquidity_usd
        volume_liquidity = _clamp01(ratio / cfg.full_credit_volume_liquidity_ratio)
        notes.append(
            Reason(
                text=f"volume/liquidity {ratio:.1f}x",
                polarity="positive" if volume_liquidity >= 0.5 else "risk",
                component="volume_liquidity",
            )
        )
    elif "volume_liquidity" not in unavailable:
        notes.append(
            Reason(text="volume or liquidity unavailable", polarity="risk", component=None)
        )

    holder_growth = 0.0
    if sm.total_holders is not None and inputs.token_age_hours is not None:
        rate = sm.total_holders / max(inputs.token_age_hours, 1.0)
        holder_growth = _clamp01(rate / cfg.full_credit_holders_per_hour)
        notes.append(
            Reason(
                text=f"{rate:.0f} new holders per hour",
                polarity="positive" if holder_growth >= 0.5 else "risk",
                component="holder_growth",
            )
        )
    elif "holder_growth" not in unavailable:
        notes.append(
            Reason(text="holder count or token age unavailable", polarity="risk", component=None)
        )

    components = {
        "sm_participation": participation,
        "sm_holding": holding,
        "bundle_supply": supply,
        "bundle_status": status,
        "volume_liquidity": volume_liquidity,
        "holder_growth": holder_growth,
    }
    weights = {k: w for k, w in cfg.weights.model_dump().items() if k not in unavailable}
    score = (
        100
        * sum(weights[name] * value for name, value in components.items() if name in weights)
        / sum(weights.values())
    )

    if sm.wallet_count == 0:
        reasons.append(
            Reason(text="no smart money buyers", polarity="risk", component="sm_participation")
        )
    else:
        detail = f"{sm.wallet_count} smart wallets (weighted {sm.weighted_score:.1f})"
        if sm.holding_ratio is not None:
            detail += f", still holding {sm.holding_ratio:.0%}"
        reasons.append(Reason(text=detail, polarity="positive", component="sm_participation"))
    if bundle_size == 0:
        reasons.append(
            Reason(text="no bundle detected", polarity="positive", component="bundle_supply")
        )
    elif VETO_BUNDLE_SUPPLY not in vetoes:
        reasons.append(
            Reason(
                text=(
                    f"bundle of {bundle_size} wallets holds {bundle.supply_pct:.0f}% of supply, "
                    f"status {bundle.status}"
                ),
                polarity="positive",
                component="bundle_supply",
            )
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
