from datetime import date, datetime
from pathlib import Path

from pydantic import BaseModel

from backtest.client import CACHE_DIR

RESULTS_PATH = CACHE_DIR / "results.jsonl"
SKIPS_PATH = CACHE_DIR / "skips.jsonl"
CANDIDATES_PATH = CACHE_DIR / "candidates.json"


class OutcomeRow(BaseModel):
    hours: int
    complete: bool
    max_drawdown_pct: float | None
    price_change_pct: float | None
    dumped: bool | None


class ResultRow(BaseModel):
    chain: str
    token_address: str
    symbol: str
    day: date
    launch_at: datetime
    decision_at: datetime
    decision_market_cap: float
    verdict: str
    score: float
    vetoes: list[str]
    bundle_status: str
    bundle_supply_pct: float
    bundle_wallets: int
    sm_wallets: int
    sm_net_selling: bool
    smart_in_bundle: int
    outcomes: list[OutcomeRow]
    bundle_exit_fraction: float | None
    bundle_exited_24h: bool | None
    lookahead_rows_dropped: int
    credits_spent: int


class SkipRow(BaseModel):
    chain: str
    token_address: str
    reason: str


def append_jsonl(path: Path, row: BaseModel) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(row.model_dump_json() + "\n")


def load_results(path: Path = RESULTS_PATH) -> list[ResultRow]:
    if not path.exists():
        return []
    return [
        ResultRow.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def load_skips(path: Path = SKIPS_PATH) -> list[SkipRow]:
    if not path.exists():
        return []
    return [
        SkipRow.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
