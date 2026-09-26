from app.nansen.client import NansenClient
from app.nansen.models import (
    ProfilerAddressBalancesRequest,
    ProfilerAddressBalancesResponse,
    ProfilerAddressRelatedWalletsRequest,
    ProfilerAddressRelatedWalletsResponse,
    SmartMoneyDexTradesRequest,
    SmartMoneyDexTradesResponse,
    TGMDexTradesRequest,
    TGMDexTradesResponse,
    TGMFlowIntelligenceRequest,
    TGMFlowIntelligenceResponse,
    TGMHoldersRequest,
    TGMHoldersResponse,
    TGMTokenInformationRequest,
    TGMTokenInformationResponse,
    TokenScreenerRequest,
    TokenScreenerResponse,
)


async def token_screener(
    client: NansenClient, request: TokenScreenerRequest
) -> TokenScreenerResponse:
    body = request.model_dump(mode="json", exclude_none=True, by_alias=True)
    data = await client.post("/api/v1/token-screener", body)
    return TokenScreenerResponse.model_validate(data)


async def smart_money_dex_trades(
    client: NansenClient, request: SmartMoneyDexTradesRequest
) -> SmartMoneyDexTradesResponse:
    body = request.model_dump(mode="json", exclude_none=True, by_alias=True)
    data = await client.post("/api/v1/smart-money/dex-trades", body)
    return SmartMoneyDexTradesResponse.model_validate(data)


async def tgm_dex_trades(
    client: NansenClient, request: TGMDexTradesRequest
) -> TGMDexTradesResponse:
    body = request.model_dump(mode="json", exclude_none=True, by_alias=True)
    data = await client.post("/api/v1/tgm/dex-trades", body)
    return TGMDexTradesResponse.model_validate(data)


async def profiler_address_related_wallets(
    client: NansenClient,
    request: ProfilerAddressRelatedWalletsRequest,
    *,
    cache_ttl_seconds: float | None = None,
) -> ProfilerAddressRelatedWalletsResponse:
    body = request.model_dump(mode="json", exclude_none=True, by_alias=True)
    data = await client.post(
        "/api/v1/profiler/address/related-wallets", body, cache_ttl_seconds=cache_ttl_seconds
    )
    return ProfilerAddressRelatedWalletsResponse.model_validate(data)


async def tgm_holders(client: NansenClient, request: TGMHoldersRequest) -> TGMHoldersResponse:
    body = request.model_dump(mode="json", exclude_none=True, by_alias=True)
    data = await client.post("/api/v1/tgm/holders", body)
    return TGMHoldersResponse.model_validate(data)


async def profiler_address_current_balance(
    client: NansenClient, request: ProfilerAddressBalancesRequest
) -> ProfilerAddressBalancesResponse:
    body = request.model_dump(mode="json", exclude_none=True, by_alias=True)
    data = await client.post("/api/v1/profiler/address/current-balance", body)
    return ProfilerAddressBalancesResponse.model_validate(data)


async def tgm_token_information(
    client: NansenClient, request: TGMTokenInformationRequest
) -> TGMTokenInformationResponse:
    body = request.model_dump(mode="json", exclude_none=True, by_alias=True)
    data = await client.post("/api/v1/tgm/token-information", body)
    return TGMTokenInformationResponse.model_validate(data)


async def tgm_flow_intelligence(
    client: NansenClient, request: TGMFlowIntelligenceRequest
) -> TGMFlowIntelligenceResponse:
    body = request.model_dump(mode="json", exclude_none=True, by_alias=True)
    data = await client.post("/api/v1/tgm/flow-intelligence", body)
    return TGMFlowIntelligenceResponse.model_validate(data)
