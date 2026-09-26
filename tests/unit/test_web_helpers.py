from datetime import UTC, datetime, timedelta

import pytest

from app.demo import evm_address, solana_address
from app.web.charts import ScorePoint, score_history_svg
from app.web.format import (
    age,
    age_since,
    cluster_reason,
    component_label,
    money,
    percent,
    short_address,
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
    assert token_explorer_url("ethereum", evm) == f"https://etherscan.io/token/{evm}"


# --- chart ---


def point(minutes: int, score: float, verdict: str = "WATCH") -> ScorePoint:
    return ScorePoint(NOW + timedelta(minutes=minutes), score, verdict)


def test_an_empty_history_draws_nothing() -> None:
    assert score_history_svg([], 40, 70) == ""


def test_a_single_snapshot_is_one_point_without_a_line() -> None:
    svg = str(score_history_svg([point(0, 55)], 40, 70))

    assert svg.count("<circle") == 1
    assert "<polyline" not in svg
    assert 'role="img"' in svg and "latest 55" in svg


def test_several_snapshots_are_joined_by_a_line_in_time_order() -> None:
    svg = str(
        score_history_svg([point(0, 40), point(10, 60, "WATCH"), point(30, 80, "GREEN")], 40, 70)
    )

    assert svg.count("<circle") == 3
    assert svg.count("<polyline") == 1
    assert "verdict-green" in svg and "verdict-watch" in svg
    assert "GREEN" in svg and "WATCH" in svg  # threshold guide labels


def test_scores_outside_the_axis_are_clamped() -> None:
    svg = str(score_history_svg([point(0, -20), point(5, 250)], 40, 70))

    assert 'cy="-' not in svg
    assert svg.count("<circle") == 2


def test_snapshots_at_the_same_moment_do_not_divide_by_zero() -> None:
    svg = str(score_history_svg([point(0, 50), point(0, 60)], 40, 70))

    assert svg.count("<circle") == 2


def test_the_chart_escapes_text_and_uses_no_inline_style() -> None:
    svg = str(score_history_svg([point(0, 50, '"><script>alert(1)</script>')], 40, 70))

    assert "<script>" not in svg
    assert " style=" not in svg


# --- formatting ---


def test_money() -> None:
    assert money(None) == "n/a"
    assert money(0) == "$0"
    assert money(950) == "$950"
    assert money(1500) == "$1.5K"
    assert money(2_400_000) == "$2.40M"
    assert money(3.2e9) == "$3.20B"
    assert money(-9000) == "-$9.0K"


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
    assert short_address("0123456789abcdef0123") == "012345...0123"
    assert cluster_reason("same_second") == "bought in the same second"
    assert cluster_reason("something_new") == "something new"
    assert component_label("sm_participation") == "Smart money participation"
    assert component_label("new_component") == "New component"
