from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import load_scoring_config
from app.pipeline.bundle import BundleAnalysis, BundleStatus
from app.pipeline.crosscheck import sm_in_bundle
from app.pipeline.scoring import (
    VETO_BUNDLE_SUPPLY,
    VETO_SM_IN_BUNDLE,
    VETO_SM_NET_SELLING,
    ScoreInputs,
    score_token,
)
from app.pipeline.smart_money import SmartMoneyMetrics
from tests.factories import scoring_config

CFG = scoring_config()


def smart(
    wallets: int = 3,
    weighted: float = 3.0,
    holding: float | None = 0.95,
    net_flow: float = 5000.0,
    holders: int | None = 3000,
) -> SmartMoneyMetrics:
    return SmartMoneyMetrics(
        wallet_count=wallets,
        signal=wallets >= 2,
        label_counts={},
        weighted_score=weighted,
        net_flow_usd=net_flow,
        net_selling=net_flow < 0,
        bought_tokens=1000.0,
        sold_tokens=0.0,
        holding_ratio=holding,
        wallets_still_holding=wallets,
        first_entry_at=None,
        minutes_after_launch=None,
        market_cap_at_entry=None,
        smart_wallets=frozenset(f"sm{i}" for i in range(wallets)),
        total_holders=holders,
    )


def bundle(supply: float = 0.0, status: BundleStatus = "none", size: int = 0) -> BundleAnalysis:
    return BundleAnalysis(
        clusters=[],
        bundled_wallets=frozenset(f"b{i}" for i in range(size)),
        supply_pct=supply,
        sold_pct=0.0,
        status=status,
    )


def inputs(
    sm: SmartMoneyMetrics | None = None,
    b: BundleAnalysis | None = None,
    overlap: frozenset[str] = frozenset(),
    volume: float | None = 300_000.0,
    liquidity: float | None = 100_000.0,
    age: float | None = 50.0,
) -> ScoreInputs:
    return ScoreInputs(
        smart_money=sm or smart(),
        bundle=b or bundle(),
        smart_wallets_in_bundle=overlap,
        volume_usd=volume,
        liquidity_usd=liquidity,
        token_age_hours=age,
    )


# --- the four quadrants from SPEC.md Phase 5 ---


def test_sm_holding_and_low_bundle_is_green() -> None:
    result = score_token(inputs(), CFG)

    assert result.verdict == "GREEN"
    assert result.vetoes == []
    assert result.score == pytest.approx(83.25)
    assert result.reasons[0] == "3 smart wallets (weighted 3.0), still holding 95%"


def test_sm_holding_and_high_bundle_is_avoid_by_veto() -> None:
    result = score_token(inputs(b=bundle(supply=38.0, status="holding", size=14)), CFG)

    assert result.verdict == "AVOID"
    assert result.vetoes == [VETO_BUNDLE_SUPPLY]
    assert result.reasons[0] == "bundle of 14 wallets holds 38% of supply (veto above 30%)"


def test_sm_selling_and_high_bundle_is_avoid() -> None:
    sm = smart(holding=0.1, net_flow=-8000.0)

    result = score_token(inputs(sm=sm, b=bundle(supply=45.0, status="distributing", size=9)), CFG)

    assert result.verdict == "AVOID"
    assert result.vetoes == [VETO_BUNDLE_SUPPLY, VETO_SM_NET_SELLING]
    assert "smart money net selling $8,000 in the last 24h" in result.reasons


def test_no_sm_and_low_bundle_is_watch() -> None:
    sm = smart(wallets=0, weighted=0.0, holding=None, net_flow=0.0)

    result = score_token(inputs(sm=sm), CFG)

    assert result.verdict == "WATCH"
    assert result.vetoes == []
    assert result.score == pytest.approx(54.0)
    assert "no smart money buyers" in result.reasons


# --- vetoes on their own ---


def test_bundle_supply_veto_is_strictly_above_the_threshold() -> None:
    at = score_token(inputs(b=bundle(supply=30.0, status="holding", size=5)), CFG)
    above = score_token(inputs(b=bundle(supply=30.01, status="holding", size=5)), CFG)

    assert VETO_BUNDLE_SUPPLY not in at.vetoes
    assert above.vetoes == [VETO_BUNDLE_SUPPLY]


def test_sm_net_selling_veto_alone() -> None:
    result = score_token(inputs(sm=smart(net_flow=-1.0)), CFG)

    assert result.vetoes == [VETO_SM_NET_SELLING]
    assert result.verdict == "AVOID"


def test_sell_only_smart_wallet_still_triggers_the_net_selling_veto() -> None:
    sm = smart(wallets=0, weighted=0.0, holding=None, net_flow=-2500.0)

    result = score_token(inputs(sm=sm), CFG)

    assert result.vetoes == [VETO_SM_NET_SELLING]


def test_sm_in_bundle_veto_alone() -> None:
    result = score_token(inputs(overlap=frozenset({"sm0", "sm1"})), CFG)

    assert result.vetoes == [VETO_SM_IN_BUNDLE]
    assert "2 smart wallet(s) are inside a bundle" in result.reasons
    assert result.verdict == "AVOID"


def test_veto_flags_can_be_switched_off() -> None:
    cfg = scoring_config(veto_sm_net_selling=False, veto_sm_in_bundle=False)

    result = score_token(inputs(sm=smart(net_flow=-500.0), overlap=frozenset({"sm0"})), cfg)

    assert result.vetoes == []
    assert result.verdict == "GREEN"


def test_supply_veto_cannot_be_switched_off_by_the_other_flags() -> None:
    cfg = scoring_config(veto_sm_net_selling=False, veto_sm_in_bundle=False)

    result = score_token(inputs(b=bundle(supply=50.0, status="holding", size=8)), cfg)

    assert result.vetoes == [VETO_BUNDLE_SUPPLY]


# --- thresholds and edge cases ---


def test_green_and_watch_boundaries_are_inclusive() -> None:
    score = score_token(inputs(), CFG).score

    assert score_token(inputs(), scoring_config(green_min=score)).verdict == "GREEN"
    assert score_token(inputs(), scoring_config(green_min=score + 0.01)).verdict == "WATCH"
    low = scoring_config(green_min=101, watch_min=score)
    assert score_token(inputs(), low).verdict == "WATCH"
    assert score_token(inputs(), scoring_config(green_min=101, watch_min=score + 0.01)).verdict == (
        "AVOID"
    )


def test_a_token_without_smart_money_can_never_be_green() -> None:
    sm = smart(wallets=0, weighted=0.0, holding=None, net_flow=0.0, holders=100_000)

    result = score_token(inputs(sm=sm, volume=10_000_000.0), CFG)

    assert result.score == pytest.approx(60.0)
    assert result.verdict == "WATCH"


def test_missing_data_scores_zero_and_is_reported() -> None:
    result = score_token(inputs(sm=smart(holders=None), liquidity=None, age=None), CFG)

    assert result.components["volume_liquidity"] == 0.0
    assert result.components["holder_growth"] == 0.0
    assert "volume or liquidity unavailable" in result.reasons
    assert "holder count or token age unavailable" in result.reasons


def test_zero_liquidity_does_not_divide_by_zero() -> None:
    result = score_token(inputs(liquidity=0.0), CFG)

    assert result.components["volume_liquidity"] == 0.0


def test_components_are_capped_at_full_credit() -> None:
    sm = smart(weighted=50.0, holders=10_000_000)

    result = score_token(inputs(sm=sm, volume=1e12), CFG)

    assert result.components["sm_participation"] == 1.0
    assert result.components["volume_liquidity"] == 1.0
    assert result.components["holder_growth"] == 1.0


def test_very_young_token_uses_a_one_hour_age_floor() -> None:
    result = score_token(inputs(sm=smart(holders=25), age=0.1), CFG)

    assert result.components["holder_growth"] == pytest.approx(0.5)


def test_distributing_bundle_scores_lower_than_a_holding_one() -> None:
    holding = score_token(inputs(b=bundle(supply=5.0, status="holding", size=4)), CFG)
    distributing = score_token(inputs(b=bundle(supply=5.0, status="distributing", size=4)), CFG)

    assert distributing.score < holding.score


def test_score_stays_within_zero_to_one_hundred() -> None:
    best = score_token(inputs(sm=smart(weighted=99, holding=1.0, holders=10**7), volume=1e12), CFG)
    worst = score_token(
        inputs(
            sm=smart(wallets=0, weighted=0, holding=None, net_flow=-1, holders=None),
            b=bundle(supply=99, status="distributing", size=9),
            volume=None,
            age=None,
        ),
        CFG,
    )

    assert 0 <= worst.score < best.score <= 100


# --- cross-check ---


def test_sm_in_bundle_returns_the_overlap() -> None:
    result = sm_in_bundle(frozenset({"a", "b", "b0"}), bundle(size=2))

    assert result == frozenset({"b0"})


def test_sm_in_bundle_is_empty_without_a_bundle() -> None:
    assert sm_in_bundle(frozenset({"a"}), bundle()) == frozenset()


# --- the real defaults in config/scoring.yaml ---


def test_shipped_defaults_reproduce_the_quadrants() -> None:
    real = load_scoring_config(Path(__file__).parents[2] / "config" / "scoring.yaml").scoring

    assert score_token(inputs(), real).verdict == "GREEN"
    assert score_token(inputs(b=bundle(supply=38.0, status="holding", size=14)), real).verdict == (
        "AVOID"
    )
    no_sm = smart(wallets=0, weighted=0.0, holding=None, net_flow=0.0)
    assert score_token(inputs(sm=no_sm), real).verdict == "WATCH"
    assert sum(real.weights.model_dump().values()) == 100


@pytest.mark.parametrize(
    "override",
    [
        {"veto_bundle_supply_pct": 0},
        {"full_credit_weighted_score": 0},
        {"full_credit_volume_liquidity_ratio": -1},
        {"full_credit_holders_per_hour": 0},
        {"status_scores": {"none": 1.0, "holding": 0.4}},
        {
            "weights": {
                "sm_participation": 0,
                "sm_holding": 0,
                "bundle_supply": 0,
                "bundle_status": 0,
                "volume_liquidity": 0,
                "holder_growth": 0,
            }
        },
    ],
)
def test_invalid_scoring_config_is_rejected_at_load_time(override: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        scoring_config(**override)
