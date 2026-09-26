import asyncio
import logging
import statistics
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel

from app.config import BundleConfig
from app.nansen.client import NansenClient
from app.nansen.endpoints import (
    profiler_address_related_wallets,
    tgm_dex_trades,
    tgm_holders,
)
from app.nansen.exceptions import NansenAPIError
from app.nansen.models import (
    DateRange,
    NumericRangeFilter,
    PaginationRequest,
    ProfilerAddressRelatedWalletsRequest,
    ProfilerRelatedWallet,
    SortOrder,
    TGMDexTrade,
    TGMDexTradesFilters,
    TGMDexTradesRequest,
    TGMHolder,
    TGMHoldersFilters,
    TGMHoldersRequest,
)
from app.pipeline.timeutil import parse_timestamp

logger = logging.getLogger(__name__)

REASON_SAME_SECOND = "same_second"
REASON_COMMON_FUNDER = "common_funder"
REASON_DEPLOYER_LINK = "deployer_link"

OWNERSHIP_FRACTION_TO_PCT = 100.0  # tgm/holders returns ownership_percentage as a 0-1 fraction
RELATED_WALLETS_PER_PAGE = 100
HOLDERS_BATCH_SIZE = 100
HOLDERS_PER_PAGE = 1000

BundleStatus = Literal["none", "holding", "distributing", "exited"]


class EarlyBuy(BaseModel):
    wallet: str
    timestamp: datetime
    tokens: float
    usd: float


class Funder(BaseModel):
    address: str
    label: str | None = None


class Cluster(BaseModel, frozen=True):
    wallets: frozenset[str]
    reasons: frozenset[str]
    funders: frozenset[str] = frozenset()
    similar_size: bool = False


class BundleAnalysis(BaseModel):
    clusters: list[Cluster]
    bundled_wallets: frozenset[str]
    supply_pct: float
    sold_pct: float
    status: BundleStatus


def early_buys(
    trades: list[TGMDexTrade], deployed_at: datetime, cfg: BundleConfig
) -> list[EarlyBuy]:
    window_end = deployed_at + timedelta(minutes=cfg.early_window_minutes)
    buys: list[EarlyBuy] = []
    for trade in trades:
        if trade.action != "BUY":
            continue
        timestamp = parse_timestamp(trade.block_timestamp)
        if not deployed_at <= timestamp <= window_end:
            continue
        buys.append(
            EarlyBuy(
                wallet=trade.trader_address,
                timestamp=timestamp,
                tokens=trade.token_amount,
                usd=trade.estimated_value_usd,
            )
        )
    buys.sort(key=lambda buy: buy.timestamp)
    return buys[: cfg.max_early_buys]


def wallets_to_look_up(buys: list[EarlyBuy], cfg: BundleConfig) -> list[str]:
    """Earliest unique buyers, capped, because each funder lookup costs a credit."""
    seen: dict[str, None] = {}
    for buy in buys:
        seen.setdefault(buy.wallet)
    return list(seen)[: cfg.max_funder_lookups]


def same_second_clusters(buys: list[EarlyBuy], cfg: BundleConfig) -> list[Cluster]:
    by_second: dict[datetime, set[str]] = defaultdict(set)
    for buy in buys:
        by_second[buy.timestamp.replace(microsecond=0)].add(buy.wallet)
    return [
        Cluster(wallets=frozenset(wallets), reasons=frozenset({REASON_SAME_SECOND}))
        for wallets in by_second.values()
        if len(wallets) >= cfg.same_second_min_wallets
    ]


def funder_of(related: list[ProfilerRelatedWallet], cfg: BundleConfig) -> Funder | None:
    relations = {relation.lower() for relation in cfg.funder_relations}
    funders = [row for row in related if row.relation.lower() in relations]
    if not funders:
        return None
    first = min(funders, key=lambda row: row.order)
    return Funder(address=first.address, label=first.address_label)


def is_ignored_funder(funder: Funder, cfg: BundleConfig) -> bool:
    if funder.label is None:
        return False
    label = funder.label.lower()
    return any(ignored.lower() in label for ignored in cfg.ignore_funder_labels)


def funder_clusters(funders: dict[str, Funder | None], cfg: BundleConfig) -> list[Cluster]:
    by_funder: dict[str, set[str]] = defaultdict(set)
    for wallet, funder in funders.items():
        if funder is None or is_ignored_funder(funder, cfg):
            continue
        by_funder[funder.address].add(wallet)
    return [
        Cluster(
            wallets=frozenset(wallets),
            reasons=frozenset({REASON_COMMON_FUNDER}),
            funders=frozenset({funder}),
        )
        for funder, wallets in by_funder.items()
        if len(wallets) >= cfg.common_funder_min_wallets
    ]


def merge_clusters(clusters: list[Cluster]) -> list[Cluster]:
    """Union clusters that share any wallet, combining their reasons and funders."""
    merged: list[Cluster] = []
    for cluster in clusters:
        wallets, reasons, funders = set(cluster.wallets), set(cluster.reasons), set(cluster.funders)
        remaining: list[Cluster] = []
        for other in merged:
            if wallets & other.wallets:
                wallets |= other.wallets
                reasons |= other.reasons
                funders |= other.funders
            else:
                remaining.append(other)
        merged = [
            *remaining,
            Cluster(
                wallets=frozenset(wallets),
                reasons=frozenset(reasons),
                funders=frozenset(funders),
            ),
        ]
    return merged


def mark_similar_size(
    clusters: list[Cluster], buys: list[EarlyBuy], cfg: BundleConfig
) -> list[Cluster]:
    usd_by_wallet: dict[str, float] = defaultdict(float)
    for buy in buys:
        usd_by_wallet[buy.wallet] += buy.usd

    marked: list[Cluster] = []
    for cluster in clusters:
        sizes = [usd_by_wallet[wallet] for wallet in cluster.wallets if wallet in usd_by_wallet]
        similar = False
        if len(sizes) >= 2:
            mean = statistics.fmean(sizes)
            if mean > 0:
                similar = statistics.pstdev(sizes) / mean <= cfg.similar_size_max_cv
        marked.append(cluster.model_copy(update={"similar_size": similar}))
    return marked


def mark_deployer_link(
    clusters: list[Cluster], deployer: str | None, funders: dict[str, Funder | None]
) -> list[Cluster]:
    if deployer is None:
        return clusters
    marked: list[Cluster] = []
    for cluster in clusters:
        funded_by_deployer = any(
            (funder := funders.get(wallet)) is not None and funder.address == deployer
            for wallet in cluster.wallets
        )
        if deployer in cluster.wallets or deployer in cluster.funders or funded_by_deployer:
            cluster = cluster.model_copy(
                update={"reasons": cluster.reasons | {REASON_DEPLOYER_LINK}}
            )
        marked.append(cluster)
    return marked


def find_clusters(
    buys: list[EarlyBuy],
    funders: dict[str, Funder | None],
    deployer: str | None,
    cfg: BundleConfig,
) -> list[Cluster]:
    clusters = merge_clusters([*same_second_clusters(buys, cfg), *funder_clusters(funders, cfg)])
    clusters = mark_similar_size(clusters, buys, cfg)
    return mark_deployer_link(clusters, deployer, funders)


def measure_bundle(
    clusters: list[Cluster],
    buys: list[EarlyBuy],
    holders: list[TGMHolder],
    cfg: BundleConfig,
) -> BundleAnalysis:
    """Supply and sold share of all clustered wallets; a wallet absent from holders holds 0."""
    bundled = frozenset(wallet for cluster in clusters for wallet in cluster.wallets)
    if not bundled:
        return BundleAnalysis(
            clusters=[], bundled_wallets=frozenset(), supply_pct=0.0, sold_pct=0.0, status="none"
        )

    current_holders = [h for h in holders if h.address in bundled]
    supply_pct = sum(h.ownership_percentage or 0.0 for h in current_holders) * (
        OWNERSHIP_FRACTION_TO_PCT
    )
    current_tokens = sum(h.token_amount or 0.0 for h in current_holders)
    peak_tokens = sum(buy.tokens for buy in buys if buy.wallet in bundled)

    sold_pct = 0.0
    if peak_tokens > 0:
        sold_pct = min(100.0, max(0.0, (1 - current_tokens / peak_tokens) * 100))

    status: BundleStatus = "holding"
    if sold_pct >= cfg.exited_sold_pct:
        status = "exited"
    elif sold_pct >= cfg.distributing_sold_pct:
        status = "distributing"

    return BundleAnalysis(
        clusters=clusters,
        bundled_wallets=bundled,
        supply_pct=supply_pct,
        sold_pct=sold_pct,
        status=status,
    )


def _iso(moment: datetime) -> str:
    return moment.isoformat().replace("+00:00", "Z")


async def _lookup_funder(
    client: NansenClient,
    chain: str,
    wallet: str,
    cfg: BundleConfig,
    cache_ttl_seconds: float,
) -> Funder | None:
    request = ProfilerAddressRelatedWalletsRequest(
        wallet_address=wallet,
        chain=chain,
        pagination=PaginationRequest(page=1, per_page=RELATED_WALLETS_PER_PAGE),
        order_by=[SortOrder(field="order", direction="ASC")],
    )
    try:
        response = await profiler_address_related_wallets(
            client, request, cache_ttl_seconds=cache_ttl_seconds
        )
    except NansenAPIError as exc:
        logger.warning(
            "funder lookup failed, treating funder as unknown",
            extra={"chain": chain, "wallet": wallet, "status": exc.status},
        )
        return None
    return funder_of(response.data, cfg)


async def _fetch_holders(
    client: NansenClient, chain: str, token_address: str, wallets: list[str]
) -> list[TGMHolder]:
    holders: list[TGMHolder] = []
    for start in range(0, len(wallets), HOLDERS_BATCH_SIZE):
        request = TGMHoldersRequest(
            chain=chain,
            token_address=token_address,
            pagination=PaginationRequest(page=1, per_page=HOLDERS_PER_PAGE),
            filters=TGMHoldersFilters(
                address=wallets[start : start + HOLDERS_BATCH_SIZE],
                value_usd=NumericRangeFilter(min=0),
            ),
        )
        holders.extend((await tgm_holders(client, request)).data)
    return holders


async def analyze_bundle(
    client: NansenClient,
    cfg: BundleConfig,
    *,
    chain: str,
    token_address: str,
    deployed_at: datetime,
    deployer: str | None,
    related_wallets_ttl_seconds: float,
) -> BundleAnalysis:
    window_end = deployed_at + timedelta(minutes=cfg.early_window_minutes)
    trades_request = TGMDexTradesRequest(
        chain=chain,
        token_address=token_address,
        date=DateRange.model_validate({"from": _iso(deployed_at), "to": _iso(window_end)}),
        pagination=PaginationRequest(page=1, per_page=cfg.max_early_buys),
        filters=TGMDexTradesFilters(action="BUY"),
        order_by=[SortOrder(field="block_timestamp", direction="ASC")],
    )
    trades = await tgm_dex_trades(client, trades_request)
    buys = early_buys(trades.data, deployed_at, cfg)

    wallets = wallets_to_look_up(buys, cfg)
    # Wait for every lookup before re-raising, so a budget error never leaves tasks running.
    found = await asyncio.gather(
        *(_lookup_funder(client, chain, w, cfg, related_wallets_ttl_seconds) for w in wallets),
        return_exceptions=True,
    )
    funders: dict[str, Funder | None] = {}
    for wallet, result in zip(wallets, found, strict=True):
        if isinstance(result, BaseException):
            raise result
        funders[wallet] = result

    clusters = find_clusters(buys, funders, deployer, cfg)
    if not clusters:
        return measure_bundle([], buys, [], cfg)

    bundled = sorted({wallet for cluster in clusters for wallet in cluster.wallets})
    holders = await _fetch_holders(client, chain, token_address, bundled)
    return measure_bundle(clusters, buys, holders, cfg)
