import json
from pathlib import Path

import pytest

from app.nansen.models import (
    ProfilerAddressBalancesResponse,
    ProfilerAddressRelatedWalletsResponse,
    SmartMoneyDexTradesResponse,
    TGMDexTradesResponse,
    TGMFlowIntelligenceResponse,
    TGMHoldersResponse,
    TGMTokenInformationResponse,
    TokenScreenerResponse,
)

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"

MODEL_BY_ENDPOINT_SLUG = {
    "token-screener": TokenScreenerResponse,
    "smart-money/dex-trades": SmartMoneyDexTradesResponse,
    "tgm/dex-trades": TGMDexTradesResponse,
    "profiler/address/related-wallets": ProfilerAddressRelatedWalletsResponse,
    "tgm/holders": TGMHoldersResponse,
    "profiler/address/current-balance": ProfilerAddressBalancesResponse,
    "tgm/token-information": TGMTokenInformationResponse,
    "tgm/flow-intelligence": TGMFlowIntelligenceResponse,
}


def _discover_fixtures() -> list[tuple[str, Path]]:
    discovered: list[tuple[str, Path]] = []
    for slug in MODEL_BY_ENDPOINT_SLUG:
        for path in sorted((FIXTURES_DIR / slug).glob("*.json")):
            discovered.append((slug, path))
    return discovered


@pytest.mark.parametrize("slug,path", _discover_fixtures(), ids=lambda v: str(v))
def test_fixture_parses_with_model(slug: str, path: Path) -> None:
    model = MODEL_BY_ENDPOINT_SLUG[slug]
    payload = json.loads(path.read_text())

    model.model_validate(payload)


def test_every_endpoint_has_at_least_one_fixture() -> None:
    slugs_with_fixtures = {slug for slug, _ in _discover_fixtures()}

    assert slugs_with_fixtures == set(MODEL_BY_ENDPOINT_SLUG)
