"""UI_REDESIGN.md §4 shared components: each macro rendered directly, not through a page route."""

import pytest

from app.web.format import AVATAR_HUES, KNOWN_CHAINS, avatar_hue, chain_class, chain_label
from app.web.routes import templates

HOSTILE = "<img src=x onerror=alert(1)>\"'&"


def render(path: str, macro: str, *args: object) -> str:
    module = templates.env.get_template(path).module
    fn = getattr(module, macro)
    return str(fn(*args))


# --- verdict pill ---


@pytest.mark.parametrize(
    ("verdict", "word", "pill_class"),
    [
        ("GREEN", "Green", "pill-green"),
        ("WATCH", "Watch", "pill-watch"),
        ("AVOID", "Avoid", "pill-avoid"),
    ],
)
def test_verdict_pill_known_verdicts(verdict: str, word: str, pill_class: str) -> None:
    html = render("components/verdict.html", "verdict_pill", verdict, "sm")

    assert f'class="pill pill-sm {pill_class}"' in html
    assert f">{word}<" in html
    assert "<svg" in html  # every known verdict carries an icon, never colour alone


def test_verdict_pill_size() -> None:
    assert "pill-lg" in render("components/verdict.html", "verdict_pill", "GREEN", "lg")
    assert "pill-sm" in render("components/verdict.html", "verdict_pill", "GREEN", "sm")


def test_verdict_pill_unknown_verdict_falls_back_without_guessing() -> None:
    html = render("components/verdict.html", "verdict_pill", "PENDING", "sm")

    assert 'class="pill pill-sm pill-unknown"' in html
    assert ">PENDING<" in html
    assert "<svg" not in html  # no icon invented for a verdict the app doesn't recognise


def test_verdict_pill_escapes_a_hostile_verdict_string() -> None:
    html = render("components/verdict.html", "verdict_pill", HOSTILE, "sm")

    assert "<img" not in html  # no live tag; the escaped text may still say "onerror" harmlessly
    assert "&lt;img" in html


# --- chain chip ---


def test_chain_chip_known_chains_get_their_own_dot_and_label() -> None:
    html = render("components/chain.html", "chain_chip", "solana")

    assert 'class="chain-dot chain-solana"' in html
    assert "Solana" in html


def test_chain_chip_bnb_and_bsc_share_a_label_and_colour() -> None:
    bnb = render("components/chain.html", "chain_chip", "bnb")
    bsc = render("components/chain.html", "chain_chip", "bsc")

    assert ">BNB<" in bnb and ">BNB<" in bsc
    assert "chain-bnb" in bnb and "chain-bsc" in bsc  # bsc gets its own class, styled the same


def test_chain_chip_unknown_chain_falls_back_to_a_neutral_dot() -> None:
    html = render("components/chain.html", "chain_chip", "some-new-chain")

    assert 'class="chain-dot chain-default"' in html
    assert "Some-new-chain" in html


def test_chain_chip_escapes_a_hostile_chain_string() -> None:
    html = render("components/chain.html", "chain_chip", HOSTILE)

    assert "<img" not in html
    assert "&lt;img" in html


def test_chain_class_and_label_are_a_closed_allowlist() -> None:
    for chain in KNOWN_CHAINS:
        assert chain_class(chain) == f"chain-{chain}"
        assert chain_label(chain)  # never empty
    assert chain_class("nope") == "chain-default"
    assert chain_label("nope") == "Nope"


# --- token avatar ---


def test_token_avatar_shows_the_first_letter_upper_cased() -> None:
    html = render("components/avatar.html", "token_avatar", "addr1", "wired", "solana")

    assert ">W<" in html
    assert 'aria-hidden="true"' in html  # decorative; the symbol link nearby is the real label


def test_token_avatar_handles_an_empty_symbol() -> None:
    html = render("components/avatar.html", "token_avatar", "addr1", "", "solana")

    assert ">?<" in html


def test_token_avatar_carries_a_chain_dot() -> None:
    html = render("components/avatar.html", "token_avatar", "addr1", "X", "robinhood")

    assert "chain-robinhood" in html


def test_avatar_hue_is_deterministic_and_in_range() -> None:
    for address in ("a", "some-address", "0x1234", ""):
        hue = avatar_hue(address)
        assert 0 <= hue < AVATAR_HUES
        assert avatar_hue(address) == hue  # stable across calls


def test_two_different_addresses_can_land_on_different_hues() -> None:
    hues = {avatar_hue(f"addr{i}") for i in range(20)}
    assert len(hues) > 1  # not a constant function


# --- tier badge ---


def test_tier_badge_marks_a_fund_with_the_outline_class() -> None:
    fund = render("components/tier.html", "tier_badge", "Fund")
    trader = render("components/tier.html", "tier_badge", "Smart Trader")

    assert "tier-fund" in fund
    assert "tier-fund" not in trader
    assert ">Fund<" in fund and ">Smart Trader<" in trader


def test_tier_badge_escapes_a_hostile_tier_string() -> None:
    html = render("components/tier.html", "tier_badge", HOSTILE)

    assert "<img" not in html
    assert "&lt;img" in html


# --- address chip ---


def test_address_chip_shows_the_short_address_and_a_copy_button() -> None:
    address = "So11111111111111111111111111111111111111112"
    html = render("components/address.html", "address_chip", "solana", address)

    assert "So1111…" in html
    assert 'class="address-copy-btn"' in html
    assert f'data-copy-text="{address}"' in html


def test_address_chip_links_the_explorer_only_for_a_recognised_address() -> None:
    valid = render(
        "components/address.html", "address_chip", "solana",
        "So11111111111111111111111111111111111111112",
    )  # fmt: skip
    invalid = render("components/address.html", "address_chip", "solana", "not-a-real-address!!")

    assert "address-explorer-link" in valid
    assert "address-explorer-link" not in invalid
    assert "address-copy-btn" in invalid  # copying still works without a valid explorer link


def test_address_chip_escapes_a_hostile_address_everywhere_it_appears() -> None:
    html = render("components/address.html", "address_chip", "solana", HOSTILE)

    assert "<img" not in html  # no live tag anywhere, including inside data-copy-text
    assert html.count("&lt;img") >= 1  # in the visible text and in data-copy-text
