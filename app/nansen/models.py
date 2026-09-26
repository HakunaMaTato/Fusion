from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# --- Shared building blocks (identical shape across every endpoint's docs) ---


class NumericRangeFilter(BaseModel):
    min: float | None = None
    max: float | None = None


class IntegerRangeFilter(BaseModel):
    min: int | None = None
    max: int | None = None


class DateRange(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    from_: str | None = Field(default=None, alias="from")
    to: str | None = None


class DateRangeFilter(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    from_: str | None = Field(default=None, alias="from")
    to: str | None = None


class SortOrder(BaseModel):
    field: str
    direction: Literal["ASC", "DESC"]


class PaginationRequest(BaseModel):
    page: int = 1
    per_page: int = 10


class PaginationInfo(BaseModel):
    model_config = ConfigDict(extra="allow")

    page: int
    per_page: int
    is_last_page: bool


class ErrorEnvelope(BaseModel):
    model_config = ConfigDict(extra="allow")

    error: str
    message: str
    code: str
    status: int
    request_id: str | None = None
    doc_url: str
    param: str | None = None
    retry_after: int | None = None


# --- Token Screener: POST /api/v1/token-screener ---


class TokenScreenerFilters(BaseModel):
    token_address: str | list[str] | None = None
    token_symbol: str | list[str] | None = None
    trader_type: str | None = None
    sectors: list[str] | None = None
    exclude_sectors: list[str] | None = None
    token_age_days: NumericRangeFilter | None = None
    market_cap_usd: NumericRangeFilter | None = None
    liquidity: NumericRangeFilter | None = None
    price_usd: NumericRangeFilter | None = None
    price_change: NumericRangeFilter | None = None
    fdv: NumericRangeFilter | None = None
    fdv_mc_ratio: NumericRangeFilter | None = None
    nof_buyers: IntegerRangeFilter | None = None
    nof_traders: IntegerRangeFilter | None = None
    nof_sellers: IntegerRangeFilter | None = None
    nof_buys: IntegerRangeFilter | None = None
    nof_sells: IntegerRangeFilter | None = None
    buy_volume: NumericRangeFilter | None = None
    sell_volume: NumericRangeFilter | None = None
    volume: NumericRangeFilter | None = None
    netflow: NumericRangeFilter | None = None
    inflow_fdv_ratio: NumericRangeFilter | None = None
    outflow_fdv_ratio: NumericRangeFilter | None = None
    include_stablecoins: bool | None = None
    include_native_tokens: bool | None = None
    include_smart_money_labels: list[str] | None = None
    exclude_smart_money_labels: list[str] | None = None


class TokenScreenerRequest(BaseModel):
    chains: list[str]
    timeframe: str
    pagination: PaginationRequest | None = None
    filters: TokenScreenerFilters | None = None
    order_by: list[SortOrder] | None = None


class TokenScreenerToken(BaseModel):
    model_config = ConfigDict(extra="allow")

    chain: str
    token_address: str
    token_symbol: str
    token_age_days: float | None = None
    token_age_hours: float | None = None
    token_deployment_date: str | None = None
    market_cap_usd: float | None = None
    liquidity: float | None = None
    price_usd: float | None = None
    price_change: float | None = None
    fdv: float | None = None
    fdv_mc_ratio: float | None = None
    buy_volume: float | None = None
    sell_volume: float | None = None
    volume: float | None = None
    netflow: float | None = None
    inflow_fdv_ratio: float | None = None
    outflow_fdv_ratio: float | None = None
    nof_traders: int | None = None
    nof_buyers: int | None = None
    nof_sellers: int | None = None
    nof_buys: int | None = None
    nof_sells: int | None = None


class TokenScreenerResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    data: list[TokenScreenerToken]
    pagination: PaginationInfo


# --- Smart Money DEX Trades: POST /api/v1/smart-money/dex-trades ---


class SmartMoneyDexTradesFilters(BaseModel):
    include_smart_money_labels: list[str] | None = None
    exclude_smart_money_labels: list[str] | None = None
    chain: str | list[str] | None = None
    transaction_hash: str | list[str] | None = None
    trader_address: str | list[str] | None = None
    trader_address_label: str | list[str] | None = None
    token_bought_address: str | list[str] | None = None
    token_sold_address: str | list[str] | None = None
    token_bought_amount: NumericRangeFilter | None = None
    token_sold_amount: NumericRangeFilter | None = None
    token_bought_symbol: str | list[str] | None = None
    token_sold_symbol: str | list[str] | None = None
    token_bought_age_days: NumericRangeFilter | None = None
    token_sold_age_days: NumericRangeFilter | None = None
    token_bought_market_cap: NumericRangeFilter | None = None
    token_sold_market_cap: NumericRangeFilter | None = None
    token_bought_fdv: NumericRangeFilter | None = None
    token_sold_fdv: NumericRangeFilter | None = None
    trade_value_usd: NumericRangeFilter | None = None


class SmartMoneyDexTradesRequest(BaseModel):
    chains: list[str]
    filters: SmartMoneyDexTradesFilters | None = None
    pagination: PaginationRequest | None = None
    order_by: list[SortOrder] | None = None


class SmartMoneyDexTrade(BaseModel):
    model_config = ConfigDict(extra="allow")

    chain: str
    block_timestamp: str
    transaction_hash: str
    trader_address: str
    trader_address_label: str
    token_bought_address: str
    token_sold_address: str
    token_bought_symbol: str
    token_sold_symbol: str
    token_bought_age_days: int
    token_sold_age_days: int
    token_bought_amount: float | None = None
    token_sold_amount: float | None = None
    token_bought_market_cap: float | None = None
    token_sold_market_cap: float | None = None
    token_bought_fdv: float | None = None
    token_sold_fdv: float | None = None
    trade_value_usd: float | None = None


class SmartMoneyDexTradesResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    data: list[SmartMoneyDexTrade]
    pagination: PaginationInfo


# --- TGM DEX Trades: POST /api/v1/tgm/dex-trades ---


class TGMDexTradesFilters(BaseModel):
    include_smart_money_labels: list[str] | None = None
    exclude_smart_money_labels: list[str] | None = None
    block_timestamp: DateRangeFilter | None = None
    transaction_hash: str | list[str] | None = None
    trader_address: str | list[str] | None = None
    trader_address_label: str | list[str] | None = None
    token_address: str | list[str] | None = None
    action: Literal["BUY", "SELL"] | None = None
    token_name: str | None = None
    token_amount: NumericRangeFilter | None = None
    traded_token_address: str | list[str] | None = None
    traded_token_name: str | None = None
    traded_token_amount: NumericRangeFilter | None = None
    estimated_swap_price_usd: NumericRangeFilter | None = None
    estimated_value_usd: NumericRangeFilter | None = None


class TGMDexTradesRequest(BaseModel):
    chain: str
    token_address: str
    only_smart_money: bool = False
    date: DateRange
    pagination: PaginationRequest | None = None
    filters: TGMDexTradesFilters | None = None
    order_by: list[SortOrder] | None = None


class TGMDexTrade(BaseModel):
    model_config = ConfigDict(extra="allow")

    block_timestamp: str
    transaction_hash: str
    trader_address: str
    action: Literal["BUY", "SELL"]
    token_address: str
    token_name: str
    token_amount: float
    traded_token_address: str
    traded_token_name: str
    traded_token_amount: float
    estimated_swap_price_usd: float
    estimated_value_usd: float
    trader_address_label: str | None = None


class TGMDexTradesResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    data: list[TGMDexTrade]
    pagination: PaginationInfo


# --- Address Related Wallets: POST /api/v1/profiler/address/related-wallets ---


class ProfilerAddressRelatedWalletsRequest(BaseModel):
    wallet_address: str
    chain: str
    pagination: PaginationRequest | None = None
    order_by: list[SortOrder] | None = None


class ProfilerRelatedWallet(BaseModel):
    model_config = ConfigDict(extra="allow")

    address: str
    relation: str
    transaction_hash: str
    block_timestamp: str
    order: int
    chain: str
    address_label: str | None = None


class ProfilerAddressRelatedWalletsResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    data: list[ProfilerRelatedWallet]
    pagination: PaginationInfo


# --- TGM Holders: POST /api/v1/tgm/holders ---


class TGMHoldersFilters(BaseModel):
    include_smart_money_labels: list[str] | None = None
    exclude_smart_money_labels: list[str] | None = None
    address: str | list[str] | None = None
    address_label: str | list[str] | None = None
    token_amount: NumericRangeFilter | None = None
    total_outflow: NumericRangeFilter | None = None
    total_inflow: NumericRangeFilter | None = None
    balance_change_24h: NumericRangeFilter | None = None
    balance_change_7d: NumericRangeFilter | None = None
    balance_change_30d: NumericRangeFilter | None = None
    ownership_percentage: NumericRangeFilter | None = None
    value_usd: NumericRangeFilter | None = None


class TGMHoldersRequest(BaseModel):
    chain: str
    token_address: str
    aggregate_by_entity: bool = False
    label_type: Literal["whale", "public_figure", "smart_money", "all_holders", "exchange"] = (
        "all_holders"
    )
    premium_labels: bool | None = None
    pagination: PaginationRequest | None = None
    filters: TGMHoldersFilters | None = None
    order_by: list[SortOrder] | None = None


class TGMHolder(BaseModel):
    model_config = ConfigDict(extra="allow")

    address: str | None = None
    address_label: str | None = None
    token_amount: float | None = None
    total_outflow: float | None = None
    total_inflow: float | None = None
    balance_change_24h: float | None = None
    balance_change_7d: float | None = None
    balance_change_30d: float | None = None
    ownership_percentage: float | None = None
    value_usd: float | None = None


class TGMHoldersResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    data: list[TGMHolder]
    pagination: PaginationInfo
    warnings: list[str] | None = None


# --- Address Current Balance: POST /api/v1/profiler/address/current-balance ---


class ProfilerAddressBalancesFilters(BaseModel):
    value_usd: NumericRangeFilter | None = None
    price_usd: NumericRangeFilter | None = None
    token_amount: IntegerRangeFilter | None = None
    token_symbol: str | list[str] | None = None
    token_address: str | list[str] | None = None
    token_name: str | list[str] | None = None


class ProfilerAddressBalancesRequest(BaseModel):
    chain: str
    address: str | None = None
    entity_name: str | None = None
    hide_spam_token: bool = True
    filters: ProfilerAddressBalancesFilters | None = None
    pagination: PaginationRequest | None = None
    order_by: list[SortOrder] | None = None


class ProfilerBalance(BaseModel):
    model_config = ConfigDict(extra="allow")

    chain: str
    address: str
    token_address: str
    token_symbol: str
    token_name: str | None = None
    token_amount: float | None = None
    price_usd: float | None = None
    value_usd: float | None = None


class ProfilerAddressBalancesResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    data: list[ProfilerBalance]
    pagination: PaginationInfo


# --- TGM Token Information: POST /api/v1/tgm/token-information ---


class TGMTokenInformationRequest(BaseModel):
    chain: str
    token_address: str
    timeframe: Literal["5m", "1h", "6h", "12h", "1d", "7d"]


class TokenDetails(BaseModel):
    model_config = ConfigDict(extra="allow")

    token_deployment_date: str | None = None
    website: str | None = None
    x: str | None = None
    telegram: str | None = None
    market_cap_usd: float | None = None
    fdv_usd: float | None = None
    circulating_supply: float | None = None
    total_supply: float | None = None


class SpotMetrics(BaseModel):
    model_config = ConfigDict(extra="allow")

    volume_total_usd: float | None = None
    buy_volume_usd: float | None = None
    sell_volume_usd: float | None = None
    total_buys: int | None = None
    total_sells: int | None = None
    unique_buyers: int | None = None
    unique_sellers: int | None = None
    liquidity_usd: float | None = None
    total_holders: int | None = None


class TGMTokenInformation(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str | None = None
    symbol: str | None = None
    contract_address: str | None = None
    logo: str | None = None
    token_details: TokenDetails | None = None
    spot_metrics: SpotMetrics | None = None


class TGMTokenInformationResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    data: TGMTokenInformation


# --- TGM Flow Intelligence: POST /api/v1/tgm/flow-intelligence ---


class TGMFlowIntelligenceFilters(BaseModel):
    public_figure_net_flow_usd: NumericRangeFilter | None = None
    public_figure_avg_flow_usd: NumericRangeFilter | None = None
    public_figure_wallet_count: IntegerRangeFilter | None = None
    top_pnl_net_flow_usd: NumericRangeFilter | None = None
    top_pnl_avg_flow_usd: NumericRangeFilter | None = None
    top_pnl_wallet_count: IntegerRangeFilter | None = None
    whale_net_flow_usd: NumericRangeFilter | None = None
    whale_avg_flow_usd: NumericRangeFilter | None = None
    whale_wallet_count: IntegerRangeFilter | None = None
    smart_trader_net_flow_usd: NumericRangeFilter | None = None
    smart_trader_avg_flow_usd: NumericRangeFilter | None = None
    smart_trader_wallet_count: IntegerRangeFilter | None = None
    exchange_net_flow_usd: NumericRangeFilter | None = None
    exchange_avg_flow_usd: NumericRangeFilter | None = None
    exchange_wallet_count: IntegerRangeFilter | None = None
    fresh_wallets_net_flow_usd: NumericRangeFilter | None = None
    fresh_wallets_avg_flow_usd: NumericRangeFilter | None = None
    fresh_wallets_wallet_count: IntegerRangeFilter | None = None


class TGMFlowIntelligenceRequest(BaseModel):
    chain: str
    token_address: str
    timeframe: Literal["5m", "1h", "6h", "12h", "1d", "7d"] = "1d"
    filters: TGMFlowIntelligenceFilters | None = None


class TGMFlowIntelligence(BaseModel):
    model_config = ConfigDict(extra="allow")

    public_figure_net_flow_usd: float | None = None
    public_figure_avg_flow_usd: float | None = None
    public_figure_wallet_count: int | None = None
    top_pnl_net_flow_usd: float | None = None
    top_pnl_avg_flow_usd: float | None = None
    top_pnl_wallet_count: int | None = None
    whale_net_flow_usd: float | None = None
    whale_avg_flow_usd: float | None = None
    whale_wallet_count: int | None = None
    smart_trader_net_flow_usd: float | None = None
    smart_trader_avg_flow_usd: float | None = None
    smart_trader_wallet_count: int | None = None
    exchange_net_flow_usd: float | None = None
    exchange_avg_flow_usd: float | None = None
    exchange_wallet_count: int | None = None
    fresh_wallets_net_flow_usd: float | None = None
    fresh_wallets_avg_flow_usd: float | None = None
    fresh_wallets_wallet_count: int | None = None


class TGMFlowIntelligenceResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    data: list[TGMFlowIntelligence]
    warnings: list[str] | None = None
