"""Demo data for trying the dashboard without Nansen credits: `python scripts/seed_demo.py`."""

import hashlib
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.pipeline.analyze import TokenAnalysis
from app.pipeline.bundle import BundleAnalysis, Cluster
from app.pipeline.discovery import Candidate
from app.pipeline.scoring import Reason, ScoreResult
from app.pipeline.smart_money import SmartMoneyMetrics, SmartWalletSummary
from app.storage import repo

_BASE58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def solana_address(seed: str) -> str:
    digest = hashlib.sha256(seed.encode()).digest() + hashlib.sha256(seed.encode() + b"2").digest()
    return "".join(_BASE58[b % 58] for b in digest[:44])


def evm_address(seed: str) -> str:
    return "0x" + hashlib.sha256(seed.encode()).hexdigest()[:40]


def _wallets(chain: str, prefix: str, count: int) -> list[str]:
    make = solana_address if chain == "solana" else evm_address
    return [make(f"{prefix}-{i}") for i in range(count)]


def _analysis(
    candidate: Candidate,
    at: datetime,
    *,
    score: float,
    verdict: str,
    reasons: list[str],
    risk_reasons: list[str] | None = None,
    vetoes: list[str] | None = None,
    bundle_wallets: list[str] | None = None,
    bundle_supply: float = 0.0,
    bundle_status: str = "none",
    smart_wallets: list[SmartWalletSummary] | None = None,
    net_flow: float = 0.0,
) -> TokenAnalysis:
    smart = smart_wallets or []
    clusters = (
        [
            Cluster(
                wallets=frozenset(bundle_wallets),
                reasons=frozenset({"same_second", "common_funder"}),
                funders=frozenset({_wallets(candidate.chain, "funder", 1)[0]}),
                similar_size=True,
            )
        ]
        if bundle_wallets
        else []
    )
    return TokenAnalysis(
        candidate=candidate,
        bundle=BundleAnalysis(
            clusters=clusters,
            bundled_wallets=frozenset(bundle_wallets or []),
            supply_pct=bundle_supply,
            sold_pct=40.0 if bundle_status == "distributing" else 0.0,
            status=bundle_status,  # type: ignore[arg-type]
        ),
        smart_money=SmartMoneyMetrics(
            wallet_count=len(smart),
            signal=len(smart) >= 2,
            label_counts={},
            weighted_score=float(len(smart)) * 1.2,
            net_flow_usd=net_flow,
            net_selling=net_flow < 0,
            bought_tokens=1000.0 * len(smart),
            sold_tokens=0.0,
            holding_ratio=(
                sum(1 for w in smart if w.still_holding) / len(smart) if smart else None
            ),
            wallets_still_holding=sum(1 for w in smart if w.still_holding),
            first_entry_at=at - timedelta(minutes=50) if smart else None,
            minutes_after_launch=6.0 if smart else None,
            market_cap_at_entry=180_000.0 if smart else None,
            smart_wallets=frozenset(w.address for w in smart),
            total_holders=2400,
            wallets=smart,
            circulating_supply=200_000.0 if smart else None,
        ),
        smart_wallets_in_bundle=frozenset(),
        score=ScoreResult(
            score=score,
            verdict=verdict,  # type: ignore[arg-type]
            vetoes=vetoes or [],
            reasons=[Reason(text=r, polarity="positive") for r in reasons]
            + [Reason(text=r, polarity="risk") for r in (risk_reasons or [])],
            components={
                "sm_participation": min(1.0, len(smart) / 5),
                "sm_holding": 0.9 if smart and net_flow >= 0 else 0.1,
                "bundle_supply": max(0.0, 1 - bundle_supply / 30),
                "bundle_status": 1.0 if bundle_status == "none" else 0.2,
                "volume_liquidity": 0.7,
                "holder_growth": 0.6,
            },
        ),
        token_age_hours=(at - candidate.token_deployment_date).total_seconds() / 3600
        if candidate.token_deployment_date
        else 0.0,
        analyzed_at=at,
    )


def _candidate(chain: str, address: str, symbol: str, deployed: datetime, mcap: float) -> Candidate:
    return Candidate(
        chain=chain,
        token_address=address,
        token_symbol=symbol,
        token_age_hours=0.0,
        market_cap_usd=mcap,
        token_deployment_date=deployed,
        volume_usd=mcap * 0.4,
        liquidity_usd=mcap * 0.1,
    )


def _sm(chain: str, prefix: str, tiers: list[str], holding: list[bool]) -> list[SmartWalletSummary]:
    addresses = _wallets(chain, prefix, len(tiers))
    return [
        SmartWalletSummary(
            address=a,
            tier=t,
            bought_usd=4200.0 - i * 700,
            sold_usd=0.0 if h else 5200.0 - i * 700,
            still_holding=h,
            current_tokens=3100.0 - i * 400 if h else 0.0,
            current_value_usd=3800.0 - i * 600 if h else 0.0,
        )
        for i, (a, t, h) in enumerate(zip(addresses, tiers, holding, strict=True))
    ]


def seed_demo(session: Session, now: datetime) -> None:
    """Four demo tokens (GREEN, WATCH, AVOID, and one with a hostile name) with history."""
    green = _candidate(
        "solana", solana_address("green"), "DEMO-GREEN", now - timedelta(hours=3), 2.4e6
    )
    smart_green = _sm(
        "solana",
        "sg",
        ["Fund", "180D Smart Trader", "Smart Trader", "30D Smart Trader"],
        [True] * 4,
    )
    scores = [48, 55, 62, 68, 72, 78, 81, 84]
    token = repo.upsert_token(session, green, now - timedelta(hours=2, minutes=40))
    for i, score in enumerate(scores):
        verdict = "GREEN" if score >= 70 else "WATCH"
        repo.save_snapshot(
            session,
            token,
            _analysis(
                green,
                now - timedelta(minutes=20 * (len(scores) - 1 - i)),
                score=score,
                verdict=verdict,
                reasons=[
                    "4 smart wallets (weighted 4.8), still holding 100%",
                    "no bundle detected",
                ],
                smart_wallets=smart_green,
                net_flow=12_500.0,
            ),
        )

    watch = _candidate(
        "base", evm_address("watch"), "DEMO-WATCH", now - timedelta(hours=1, minutes=30), 1.3e6
    )
    token = repo.upsert_token(session, watch, now - timedelta(hours=1, minutes=20))
    for i, score in enumerate([52, 55, 54, 57]):
        repo.save_snapshot(
            session,
            token,
            _analysis(
                watch,
                now - timedelta(minutes=20 * (3 - i)),
                score=score,
                verdict="WATCH",
                reasons=[
                    "1 smart wallets (weighted 1.2), still holding 100%",
                    "bundle of 4 wallets holds 8% of supply, status holding",
                ],
                bundle_wallets=_wallets("base", "wb", 4),
                bundle_supply=8.0,
                bundle_status="holding",
                smart_wallets=_sm("base", "sw", ["Smart Trader"], [True]),
                net_flow=900.0,
            ),
        )

    avoid = _candidate("bnb", evm_address("avoid"), "DEMO-AVOID", now - timedelta(hours=2), 1.1e6)
    token = repo.upsert_token(session, avoid, now - timedelta(hours=1, minutes=55))
    stages = [
        (76, "GREEN", 4.0, "holding", 5000.0, []),
        (58, "WATCH", 12.0, "holding", 800.0, []),
        (22, "AVOID", 38.0, "distributing", -9000.0, ["bundle_supply", "sm_net_selling"]),
    ]
    for i, (score, verdict, supply, status, flow, vetoes) in enumerate(stages):
        repo.save_snapshot(
            session,
            token,
            _analysis(
                avoid,
                now - timedelta(minutes=30 * (2 - i)),
                score=score,
                verdict=verdict,
                reasons=[] if vetoes else ["2 smart wallets (weighted 2.4), still holding 90%"],
                risk_reasons=(
                    [
                        "bundle of 14 wallets holds 38% of supply (veto above 30%)",
                        "smart money net selling $9,000 in the last 24h",
                    ]
                    if vetoes
                    else None
                ),
                vetoes=vetoes,
                bundle_wallets=_wallets("bnb", "wa", 14),
                bundle_supply=supply,
                bundle_status=status,
                smart_wallets=_sm("bnb", "sa", ["Fund", "Smart Trader"], [flow >= 0, flow >= 0]),
                net_flow=flow,
            ),
        )

    hostile = _candidate(
        "solana",
        solana_address("hostile"),
        "<img src=x onerror=alert(1)>",
        now - timedelta(hours=4),
        1.0e6,
    )
    token = repo.upsert_token(session, hostile, now - timedelta(hours=3, minutes=50))
    repo.save_snapshot(
        session,
        token,
        _analysis(
            hostile,
            now - timedelta(minutes=10),
            score=31,
            verdict="AVOID",
            reasons=["<script>alert('reason')</script>"],
            risk_reasons=["no smart money buyers"],
        ),
    )

    repo.set_credits_used(session, now.date(), 61)
    repo.beat(session, now)
    repo.record_discovery(session, now - timedelta(seconds=45))
