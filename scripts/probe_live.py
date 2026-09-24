"""One-off live probe: confirm real response shapes for the unverified assumptions.

Makes at most ~11 calls (about 12 credits). Prints only field names, types and a few sample
values; never prints the API key and never writes responses to disk.
Run with NANSEN_MODE=live.
"""

import asyncio
import sys
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import ValidationError

from app.config import get_settings
from app.nansen.client import NansenClient
from app.nansen.models import (
    ProfilerAddressRelatedWalletsResponse,
    TGMDexTradesResponse,
    TGMHoldersResponse,
    TGMTokenInformationResponse,
    TokenScreenerResponse,
)

CREDIT_CAP = 30
SOL = "solana"


def short(value: str | None) -> str:
    return "None" if value is None else (value[:6] + "..." + value[-4:] if len(value) > 14 else value)


def validate(name: str, model: Any, payload: dict[str, Any]) -> None:
    try:
        model.model_validate(payload)
        print(f"  [{name}] parses with our model: OK")
    except ValidationError as exc:
        print(f"  [{name}] MODEL MISMATCH: {exc.error_count()} errors")
        for err in exc.errors()[:5]:
            print(f"    {err['loc']}: {err['msg']}")


async def main() -> None:
    settings = get_settings()
    if settings.nansen_mode != "live":
        print("Set NANSEN_MODE=live for this run.")
        sys.exit(1)

    now = datetime.now(UTC)
    async with NansenClient(settings) as client:

        async def call(endpoint: str, body: dict[str, Any]) -> dict[str, Any]:
            if client.credits_used_today >= CREDIT_CAP:
                print("credit cap reached, stopping")
                sys.exit(2)
            return await client.post(endpoint, body)

        print("1. token-screener (solana, <1 day old)")
        screener = await call(
            "/api/v1/token-screener",
            {
                "chains": [SOL],
                "timeframe": "24h",
                "pagination": {"page": 1, "per_page": 5},
                "filters": {"token_age_days": {"max": 1}, "market_cap_usd": {"min": 100000}},
                "order_by": [{"field": "volume", "direction": "DESC"}],
            },
        )
        validate("screener", TokenScreenerResponse, screener)
        tokens = screener.get("data", [])
        print(f"  {len(tokens)} tokens; first keys: {sorted(tokens[0]) if tokens else '-'}")
        if tokens:
            print("  age hours sample:", [t.get("token_age_hours") for t in tokens[:3]])

        chosen: dict[str, Any] | None = None
        trades: list[dict[str, Any]] = []
        for token in tokens[:3]:
            print(f"2. tgm/dex-trades only_smart_money for {short(token['token_address'])}")
            resp = await call(
                "/api/v1/tgm/dex-trades",
                {
                    "chain": SOL,
                    "token_address": token["token_address"],
                    "only_smart_money": True,
                    "date": {
                        "from": (now - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "to": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    },
                    "pagination": {"page": 1, "per_page": 50},
                    "order_by": [{"field": "block_timestamp", "direction": "ASC"}],
                },
            )
            validate("smart trades", TGMDexTradesResponse, resp)
            rows = resp.get("data", [])
            print(f"  {len(rows)} smart trades")
            if rows:
                chosen, trades = token, rows
                break
        if chosen is None:
            print("no token with smart money trades found; stopping")
            return

        print("  trader_address_label values:", Counter(r.get("trader_address_label") for r in trades))
        print("  actions:", Counter(r.get("action") for r in trades))
        print("  timestamp sample:", trades[0].get("block_timestamp"))
        print("  has slot/block field:", [k for k in trades[0] if "slot" in k or "block" in k])
        wallets = list(dict.fromkeys(r["trader_address"] for r in trades))

        print(f"3. related-wallets for {short(wallets[0])}")
        related = await call(
            "/api/v1/profiler/address/related-wallets",
            {
                "wallet_address": wallets[0],
                "chain": SOL,
                "pagination": {"page": 1, "per_page": 100},
            },
        )
        validate("related wallets", ProfilerAddressRelatedWalletsResponse, related)
        rel = related.get("data", [])
        print(f"  {len(rel)} rows; relation values:", Counter(r.get("relation") for r in rel))
        print("  address_label values:", Counter(r.get("address_label") for r in rel).most_common(5))

        print("4. tgm/token-information (supply for scale check)")
        info = await call(
            "/api/v1/tgm/token-information",
            {"chain": SOL, "token_address": chosen["token_address"], "timeframe": "1d"},
        )
        validate("token information", TGMTokenInformationResponse, info)
        details = (info.get("data") or {}).get("token_details") or {}
        total_supply = details.get("total_supply")
        circulating = details.get("circulating_supply")
        print("  total_supply:", total_supply, "circulating_supply:", circulating)

        print("5. tgm/holders filtered to the smart wallets")
        holders = await call(
            "/api/v1/tgm/holders",
            {
                "chain": SOL,
                "token_address": chosen["token_address"],
                "pagination": {"page": 1, "per_page": 100},
                "filters": {"address": wallets[:5], "value_usd": {"min": 0}},
            },
        )
        validate("holders", TGMHoldersResponse, holders)
        held = holders.get("data", [])
        print(f"  asked for {len(wallets[:5])} addresses, got {len(held)} rows")
        for row in held[:3]:
            amount, pct = row.get("token_amount"), row.get("ownership_percentage")
            implied = (amount / total_supply * 100) if amount and total_supply else None
            print(
                f"  holder {short(row.get('address'))}: token_amount={amount} "
                f"ownership_percentage={pct} implied_pct_of_total_supply={implied}"
            )
        returned = {row.get("address") for row in held}
        print("  returned addresses exactly match requested:", returned <= set(wallets[:5]))
        print("  requested addresses returned lowercase/unchanged:",
              all(a in set(wallets[:5]) for a in returned if a))

        print(f"\ncredits used this run: {client.credits_used_today}")


if __name__ == "__main__":
    asyncio.run(main())
