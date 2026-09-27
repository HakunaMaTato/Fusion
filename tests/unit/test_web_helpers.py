from datetime import UTC, datetime, timedelta

import pytest

from app.demo import evm_address, solana_address
from app.web.format import (
    age,
    age_since,
    cluster_reason,
    component_label,
    money,
    pct,
    percent,
    short_address,
    usd,
)
from app.web.links import is_valid_address, token_explorer_url, wallet_explorer_url

NOW = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)


# --- links ---


def test_valid_addresses_by_chain() -> None:
    assert is_valid_address("solana", solana_address("x"))
    assert is_valid_address("base", evm_address("x"))
    assert is_valid_address("bnb", evm_address("x"))
    assert is_valid_address("ethereum", "0x" + "aB" * 20)


@pytest.mark.parametrize(
    ("chain", "address"),
    [
        ("solana", "short"),
        ("solana", "0" * 44),  # 0 is not base58
        ("solana", solana_address("x") + "!"),
        ("base", "0x123"),
        ("base", "0x" + "g" * 40),
        ("base", solana_address("x")),
        ("unknownchain", evm_address("x")),
        ("solana", 'a"><script>' + "a" * 40),
        ("solana", ""),
    ],
)
def test_invalid_addresses_are_rejected(chain: str, address: str) -> None:
    assert not is_valid_address(chain, address)
    assert token_explorer_url(chain, address) is None
    assert wallet_explorer_url(chain, address) is None


def test_explorer_urls() -> None:
    sol, evm = solana_address("a"), evm_address("a")

    assert token_explorer_url("solana", sol) == f"https://solscan.io/token/{sol}"
    assert wallet_explorer_url("solana", sol) == f"https://solscan.io/account/{sol}"
    assert token_explorer_url("base", evm) == f"https://basescan.org/token/{evm}"
    assert wallet_explorer_url("bnb", evm) == f"https://bscscan.com/address/{evm}"
    assert wallet_explorer_url("bsc", evm) == f"https://bscscan.com/address/{evm}"
    assert wallet_explorer_url("robinhood", evm) == (
        f"https://robinhoodchain.blockscout.com/address/{evm}"
    )
    assert token_explorer_url("robinhood", evm) == (
        f"https://robinhoodchain.blockscout.com/token/{evm}"
    )
    assert token_explorer_url("ethereum", evm) == f"https://etherscan.io/token/{evm}"


# --- formatting ---


def test_money() -> None:
    assert money(None) == "n/a"
    assert money(0) == "$0"
    assert money(950) == "$950"
    assert money(1500) == "$1.5K"
    assert money(2_400_000) == "$2.40M"
    assert money(3.2e9) == "$3.20B"
    assert money(-9000) == "-$9.0K"


def test_usd_matches_money() -> None:
    """usd() is the §4 name for the same helper as money(); later phases migrate call sites."""
    assert usd is money
    assert usd(1_330_000) == "$1.33M"
    assert usd(931_500) == "$931.5K"


def test_pct() -> None:
    """§4: one decimal, "<0.1%" for a small non-zero value, "0%" only for a true zero (issue 13)."""
    assert pct(None) == "n/a"
    assert pct(0) == "0%"
    assert pct(0.4) == "0.4%"
    assert pct(38.04) == "38.0%"
    assert pct(0.04) == "<0.1%"
    assert pct(-0.04) == "-<0.1%"
    assert pct(-38.04) == "-38.0%"


def test_percent_and_age() -> None:
    assert percent(None) == "n/a" and percent(38.04) == "38.0%"
    assert age(None) == "n/a"
    assert age(0.5) == "30m" and age(0) == "0m" and age(-1) == "0m"
    assert age(3.2) == "3h 12m"
    assert age(26) == "1d 2h"


def test_age_since() -> None:
    assert age_since(None, NOW) == "never"
    assert age_since(NOW - timedelta(seconds=7), NOW) == "7s ago"
    assert age_since(NOW - timedelta(minutes=2), NOW) == "2m ago"
    assert age_since(NOW + timedelta(seconds=5), NOW) == "0s ago"  # clock skew never goes negative


def test_labels() -> None:
    assert short_address("abc") == "abc"
    assert short_address("0123456789abcdef0123") == "012345…0123"  # a real ellipsis (§4)
    assert cluster_reason("same_second") == "bought in the same second"
    assert cluster_reason("something_new") == "something new"
    assert component_label("sm_participation") == "Smart money participation"
    assert component_label("new_component") == "New component"
