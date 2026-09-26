"""Record a small, fixed set of live Nansen API calls as replay fixtures.

Costs real credits (see the estimate printed below). Asks for confirmation
first. Run with NANSEN_MODE=live and a real NANSEN_API_KEY in .env.
"""

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.nansen.client import NansenClient, fixture_path

FIXTURES_DIR = Path("tests/fixtures")

# (endpoint, request body, documented credit cost on the Pro plan)
CALLS: list[tuple[str, dict[str, Any], int]] = [
    (
        "/api/v1/token-screener",
        {"chains": ["solana"], "timeframe": "1h", "pagination": {"page": 1, "per_page": 5}},
        1,
    ),
    (
        "/api/v1/smart-money/dex-trades",
        {"chains": ["solana"], "pagination": {"page": 1, "per_page": 5}},
        5,
    ),
    (
        "/api/v1/tgm/dex-trades",
        {
            "chain": "solana",
            "token_address": "So11111111111111111111111111111111111111112",
            "date": {"from": "2026-09-20T00:00:00Z", "to": "2026-09-20T00:10:00Z"},
            "pagination": {"page": 1, "per_page": 5},
        },
        1,
    ),
    (
        "/api/v1/profiler/address/related-wallets",
        {
            "wallet_address": "So11111111111111111111111111111111111111112",
            "chain": "solana",
            "pagination": {"page": 1, "per_page": 5},
        },
        1,
    ),
    (
        "/api/v1/tgm/holders",
        {
            "chain": "solana",
            "token_address": "So11111111111111111111111111111111111111112",
            "pagination": {"page": 1, "per_page": 5},
        },
        5,
    ),
    (
        "/api/v1/profiler/address/current-balance",
        {"chain": "solana", "address": "So11111111111111111111111111111111111111112"},
        1,
    ),
    (
        "/api/v1/tgm/token-information",
        {
            "chain": "solana",
            "token_address": "So11111111111111111111111111111111111111112",
            "timeframe": "1d",
        },
        1,
    ),
    (
        "/api/v1/tgm/flow-intelligence",
        {
            "chain": "solana",
            "token_address": "So11111111111111111111111111111111111111112",
            "timeframe": "1d",
        },
        1,
    ),
]


async def main() -> None:
    settings = get_settings()
    if settings.nansen_mode != "live":
        print("Refusing to run: set NANSEN_MODE=live in .env first.")
        sys.exit(1)

    total_cost = sum(cost for _, _, cost in CALLS)
    print(f"About to make {len(CALLS)} live calls, an estimated {total_cost} credits total:")
    for endpoint, _, cost in CALLS:
        print(f"  {endpoint}: ~{cost} credits")
    confirmation = input("Proceed? [y/N] ").strip().lower()
    if confirmation != "y":
        print("Aborted.")
        return

    async with NansenClient(settings, fixtures_dir=FIXTURES_DIR) as client:
        for endpoint, body, _ in CALLS:
            data = await client.post(endpoint, body)
            path = fixture_path(endpoint, body, FIXTURES_DIR)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data, indent=2, sort_keys=True))
            print(f"Recorded {path}")

    print(f"Done. Credits used today: {client.credits_used_today}")


if __name__ == "__main__":
    asyncio.run(main())
