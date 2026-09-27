import hashlib
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.demo import evm_address, seed_demo, solana_address
from app.main import app
from app.storage import repo
from app.storage.db import get_session_factory, init_db, make_engine, make_session_factory

API_KEY = "nsn_TESTKEY_MUST_NEVER_APPEAR_123456"
HTMX_SHA256 = "e209dda5c8235479f3166defc7750e1dbcd5a5c1808b7792fc2e6733768fb447"
GREEN = f"/token/solana/{solana_address('green')}"
WATCH = f"/token/base/{evm_address('watch')}"
AVOID = f"/token/bnb/{evm_address('avoid')}"
HOSTILE = f"/token/solana/{solana_address('hostile')}"


@pytest.fixture
def engine(tmp_path: Path) -> Engine:
    engine = make_engine(f"sqlite:///{tmp_path / 'web.db'}")
    init_db(engine)
    with make_session_factory(engine)() as session:
        seed_demo(session, datetime.now(UTC))
    return engine


def use(sessions: Callable[[], Session]) -> None:
    app.dependency_overrides[get_session_factory] = lambda: sessions
    app.dependency_overrides[get_settings] = lambda: Settings(
        _env_file=None, nansen_api_key=API_KEY, daily_credit_budget=3000, nansen_mode="replay"
    )


@pytest.fixture
def client(engine: Engine) -> Iterator[TestClient]:
    use(make_session_factory(engine))
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


def symbols(html: str) -> list[str]:
    import re

    return re.findall(r'class="token-link"[^>]*>([^<]*)<', html)


# --- the token table ---


def test_index_lists_tokens_green_first_then_by_score(client: TestClient) -> None:
    body = client.get("/").text

    assert symbols(body) == [
        "DEMO-GREEN",
        "DEMO-WATCH",
        "&lt;img src=x onerror=alert(1)&gt;",  # AVOID, score 31
        "DEMO-AVOID",  # AVOID, score 22
    ]
    for column in (
        "Token",
        "Age",
        "Market cap",
        "Vol / liq",
        "Verdict",
        "Score",
        "Bundle",
        "Smart money",
        "Updated",
    ):
        assert f">{column}<" in body


def test_index_shows_the_row_values(client: TestClient) -> None:
    body = client.get("/").text

    assert "$2.40M" in body and "38.0%" in body
    assert 'class="pill pill-sm pill-green"' in body and ">Green<" in body
    assert 'class="pill pill-sm pill-avoid"' in body and ">Avoid<" in body
    assert 'class="badge verdict-' not in body  # UI-2: swapped for the verdict pill everywhere


def test_verdict_and_chain_filters(client: TestClient) -> None:
    assert symbols(client.get("/?verdict=GREEN").text) == ["DEMO-GREEN"]
    assert symbols(client.get("/?chain=base").text) == ["DEMO-WATCH"]
    assert symbols(client.get("/?verdict=AVOID&chain=bnb").text) == ["DEMO-AVOID"]
    assert symbols(client.get("/?verdict=GREEN&chain=bnb").text) == []
    assert "No tokens match" in client.get("/?verdict=GREEN&chain=bnb").text


def test_unknown_filter_values_are_ignored_not_echoed(client: TestClient) -> None:
    response = client.get('/?verdict=EVIL"><script>&chain=<b>x</b>')

    assert len(symbols(response.text)) == 4
    assert "<script>x" not in response.text and "EVIL" not in response.text


def test_the_table_refreshes_via_htmx_every_thirty_seconds(client: TestClient) -> None:
    body = client.get("/?verdict=AVOID&chain=bnb").text

    assert 'hx-trigger="every 30s"' in body
    assert 'hx-get="/partials/tokens?verdict=AVOID&amp;chain=bnb"' in body
    assert 'src="/static/htmx.min.js"' in body


def test_the_partial_is_only_the_table_and_keeps_filters(client: TestClient) -> None:
    response = client.get("/partials/tokens?verdict=WATCH")

    assert response.status_code == 200
    assert "<html" not in response.text and "<table" in response.text
    assert symbols(response.text) == ["DEMO-WATCH"]


def test_attacker_controlled_names_and_reasons_are_escaped(client: TestClient) -> None:
    index = client.get("/").text
    page = client.get(HOSTILE).text

    for body in (index, page):
        assert "<img src=x" not in body
        assert "<script>alert" not in body
        assert "&lt;img src=x onerror=alert(1)&gt;" in body
    assert "&lt;script&gt;alert(&#39;reason&#39;)&lt;/script&gt;" in page


def test_an_empty_database_shows_an_empty_state(tmp_path: Path) -> None:
    engine = make_engine(f"sqlite:///{tmp_path / 'empty.db'}")
    init_db(engine)
    use(make_session_factory(engine))
    try:
        response = TestClient(app).get("/")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert "No tokens match" in response.text


def test_a_database_without_tables_shows_no_data_yet(tmp_path: Path) -> None:
    engine = make_engine(f"sqlite:///{tmp_path / 'blank.db'}")  # tables never created
    use(make_session_factory(engine))
    try:
        response = TestClient(app).get("/")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert "No data yet" in response.text
    assert "OperationalError" not in response.text and "no such table" not in response.text


# --- the token page ---


def test_token_page_explains_the_verdict(client: TestClient) -> None:
    body = client.get(AVOID).text

    assert "DEMO-AVOID" in body and 'class="pill pill-lg pill-avoid"' in body and ">Avoid<" in body
    assert "Vetoed: bundle supply, sm net selling" in body
    assert "bundle of 14 wallets holds 38% of supply (veto above 30%)" in body
    assert "Smart money participation" in body  # readable component names
    assert "<progress" in body


def test_token_page_has_a_score_history_chart(client: TestClient) -> None:
    body = client.get(GREEN).text

    assert '<svg class="chart"' in body
    assert "8 snapshots" in body
    assert body.count('class="chart-point verdict-') == 8


def test_token_page_shows_bundle_clusters_and_why(client: TestClient) -> None:
    body = client.get(AVOID).text

    assert "Cluster of 14 wallets" in body
    assert "shared funder" in body and "bought in the same second" in body
    assert "similar buy sizes" in body
    assert "distributing" in body
    assert 'href="https://bscscan.com/address/0x' in body


def test_token_page_shows_the_smart_money_wallets(client: TestClient) -> None:
    body = client.get(GREEN).text

    assert "Weighted score" in body and "24h net flow" in body
    assert "Fund" in body and "180D Smart Trader" in body
    assert 'href="https://solscan.io/account/' in body


def test_token_page_links_to_the_explorer_and_nansen(client: TestClient) -> None:
    solana = client.get(GREEN).text
    base = client.get(WATCH).text

    assert f'href="https://solscan.io/token/{solana_address("green")}"' in solana
    assert f'href="https://basescan.org/token/{evm_address("watch")}"' in base
    assert 'href="https://app.nansen.ai"' in solana
    assert 'rel="noopener noreferrer"' in solana


def test_a_token_without_a_valid_address_gets_no_explorer_link(engine: Engine) -> None:
    with make_session_factory(engine)() as session:
        from app.demo import _analysis, _candidate

        cand = _candidate("solana", "tok1", "ODD", datetime.now(UTC), 1e6)
        token = repo.upsert_token(session, cand, datetime.now(UTC))
        repo.save_snapshot(
            session,
            token,
            _analysis(cand, datetime.now(UTC), score=50, verdict="WATCH", reasons=["r"]),
        )
    use(make_session_factory(engine))
    try:
        body = TestClient(app).get("/token/solana/tok1").text
    finally:
        app.dependency_overrides.clear()

    assert "Block explorer" not in body and "Nansen app" in body


@pytest.mark.parametrize(
    "path",
    [
        "/token/solana/doesnotexist",
        "/token/nochain/abc",
        "/token/solana/../../etc/passwd",
        "/token/SOLANA!/x",
    ],
)
def test_unknown_or_malformed_tokens_are_a_generic_404(client: TestClient, path: str) -> None:
    response = client.get(path)

    assert response.status_code == 404
    assert "Token not found" in response.text or "Page not found" in response.text
    assert "Traceback" not in response.text


# --- status ---


def test_status_shows_credits_and_worker_health(client: TestClient) -> None:
    body = client.get("/status").text

    assert "61 / 3000" in body
    assert "worker is healthy" in body
    assert "Worker heartbeat" in body and "Last discovery poll" in body and "Last analysis" in body
    assert "Hourly credit share" in body and ">125<" in body


def test_status_reports_a_stale_or_missing_heartbeat(engine: Engine) -> None:
    from datetime import timedelta

    sessions = make_session_factory(engine)
    with sessions() as session:
        repo.beat(session, datetime.now(UTC) - timedelta(minutes=6))
    use(sessions)
    try:
        stale = TestClient(app).get("/status").text
    finally:
        app.dependency_overrides.clear()
    assert "worker heartbeat is stale" in stale

    blank = make_engine(f"sqlite:///{engine.url.database}.other")
    init_db(blank)
    use(make_session_factory(blank))
    try:
        missing = TestClient(app).get("/status").text
    finally:
        app.dependency_overrides.clear()
    assert "no worker heartbeat" in missing


def test_status_survives_an_unreadable_database(tmp_path: Path) -> None:
    use(make_session_factory(make_engine(f"sqlite:///{tmp_path / 'blank.db'}")))
    try:
        response = TestClient(app).get("/status")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert "temporarily unavailable" in response.text
    assert "no such table" not in response.text


# --- security, errors and presentation ---


def test_no_page_ever_contains_the_api_key(client: TestClient) -> None:
    paths = [
        "/",
        "/status",
        GREEN,
        AVOID,
        HOSTILE,
        "/partials/tokens",
        "/nope",
        "/token/solana/nope",
    ]

    for path in paths:
        response = client.get(path)
        assert API_KEY not in response.text, path
        assert "nsn_" not in response.text, path
        assert "apikey" not in response.text.lower(), path


def test_an_unexpected_error_shows_a_generic_page_without_details(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("internal detail /srv/app/secret.py")

    monkeypatch.setattr(repo, "list_tokens", boom)

    response = client.get("/")

    assert response.status_code == 500
    assert "Something went wrong" in response.text
    assert "internal detail" not in response.text and "secret.py" not in response.text


def test_a_database_failure_on_the_token_page_is_a_generic_503(
    tmp_path: Path,
) -> None:
    use(make_session_factory(make_engine(f"sqlite:///{tmp_path / 'blank.db'}")))
    try:
        response = TestClient(app).get(f"/token/solana/{solana_address('green')}")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503
    assert "temporarily unavailable" in response.text
    assert "OperationalError" not in response.text


def test_wrong_method_is_a_generic_page(client: TestClient) -> None:
    response = client.post("/")

    assert response.status_code == 405
    assert "could not be handled" in response.text


def test_security_headers(client: TestClient) -> None:
    response = client.get("/")

    csp = response.headers["content-security-policy"]
    assert (
        "default-src 'self'" in csp and "script-src 'self'" in csp and "'unsafe-inline'" not in csp
    )
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["cache-control"] == "no-store"
    assert "cache-control" not in client.get("/static/app.css").headers or (
        client.get("/static/app.css").headers["cache-control"] != "no-store"
    )


def test_pages_carry_the_disclaimer_and_a_mobile_viewport(client: TestClient) -> None:
    for path in ("/", "/status", GREEN, "/nope"):
        body = client.get(path).text
        assert "Research tool, not financial advice." in body, path
        assert 'href="https://nansen.ai/"' in body and "Nansen API" in body, path
        assert '<meta name="viewport" content="width=device-width, initial-scale=1">' in body, path


def test_pages_use_no_inline_styles_or_scripts(client: TestClient) -> None:
    for path in ("/", "/status", GREEN, AVOID):
        body = client.get(path).text
        assert " style=" not in body, path
        assert "<style" not in body, path
        assert "onclick=" not in body, path
        # the vendored htmx and the address-chip copy-button handler (both external files)
        assert body.count("<script") == 2, path
        assert '<script src="/static/js/address-chip.js" defer></script>' in body, path


def test_the_stylesheet_has_a_phone_breakpoint(client: TestClient) -> None:
    css = client.get("/static/app.css").text

    assert "@media (max-width: 640px)" in css
    assert "overflow-x" not in css or "hidden" not in css.split("overflow-x")[1][:12]


def test_dark_is_the_only_theme_for_now(client: TestClient) -> None:
    """UI_REDESIGN.md §3.1: dark is the default (and, until UI-6, only) theme, not conditional on
    the visitor's OS preference."""
    body = client.get("/").text
    tokens_css = client.get("/static/css/tokens.css").text

    assert '<meta name="color-scheme" content="dark">' in body
    assert "color-scheme: dark" in tokens_css
    assert "prefers-color-scheme" not in tokens_css
    assert "prefers-color-scheme" not in client.get("/static/app.css").text


def test_the_new_stylesheets_and_fonts_are_linked_and_served(client: TestClient) -> None:
    body = client.get("/").text
    for href in (
        "/static/css/tokens.css",
        "/static/css/type.css",
        "/static/css/layout.css",
    ):
        assert f'href="{href}"' in body, href
        assert client.get(href).status_code == 200, href
    for path in (
        "/static/fonts/ibm-plex-sans-400.woff2",
        "/static/fonts/ibm-plex-sans-500.woff2",
        "/static/fonts/ibm-plex-sans-600.woff2",
        "/static/fonts/ibm-plex-mono-400.woff2",
    ):
        response = client.get(path)
        assert response.status_code == 200, path
        assert response.content[:4] == b"wOF2", path  # a real woff2 file, not a placeholder


def test_the_vendored_htmx_is_the_pinned_release(client: TestClient) -> None:
    response = client.get("/static/htmx.min.js")

    assert response.status_code == 200
    assert hashlib.sha256(response.content).hexdigest() == HTMX_SHA256


def test_tokens_not_updated_within_a_day_are_hidden(engine: Engine) -> None:
    from datetime import timedelta

    with make_session_factory(engine)() as session:
        from app.demo import _analysis, _candidate

        cand = _candidate(
            "solana", solana_address("stale"), "STALE", datetime.now(UTC) - timedelta(days=2), 1e6
        )
        token = repo.upsert_token(session, cand, datetime.now(UTC))
        repo.save_snapshot(
            session,
            token,
            _analysis(
                cand,
                datetime.now(UTC) - timedelta(hours=30),
                score=80,
                verdict="GREEN",
                reasons=["r"],
            ),
        )
    use(make_session_factory(engine))
    try:
        body = TestClient(app).get("/").text
    finally:
        app.dependency_overrides.clear()

    assert "STALE" not in body and "DEMO-GREEN" in body


# --- UI-3: list page (§5) ---


def _add_token(
    engine: Engine, address: str, symbol: str, *, deployed: datetime, updated: datetime
) -> None:
    from app.demo import _analysis, _candidate

    with make_session_factory(engine)() as session:
        cand = _candidate("solana", solana_address(address), symbol, deployed, 1e6)
        token = repo.upsert_token(session, cand, datetime.now(UTC))
        repo.save_snapshot(
            session, token, _analysis(cand, updated, score=80, verdict="GREEN", reasons=["r"])
        )


def test_issue_16_a_fresh_snapshot_does_not_hide_a_token_s_real_age(engine: Engine) -> None:
    """The exact issue 16 bug: a re-evaluation minutes ago must not mask a >24h-old token."""
    now = datetime.now(UTC)
    _add_token(
        engine, "old-fresh-snap", "OLDFRESH", deployed=now - timedelta(hours=30), updated=now
    )
    use(make_session_factory(engine))
    try:
        default_body = TestClient(app).get("/").text
        aged_body = TestClient(app).get("/?aged=1").text
    finally:
        app.dependency_overrides.clear()

    assert "OLDFRESH" not in default_body
    assert "OLDFRESH" in aged_body


def test_the_aged_toggle_link_is_present_and_reflects_state(client: TestClient) -> None:
    off = client.get("/").text
    on = client.get("/?aged=1").text

    assert 'href="/?aged=1"' in off and 'aria-pressed="false"' in off
    assert 'href="/"' in on and 'aria-pressed="true"' in on


def test_search_matches_symbol_and_preserves_the_term_in_the_input(client: TestClient) -> None:
    found = client.get("/?q=green").text
    empty = client.get("/?q=nonexistent-xyz").text

    assert "DEMO-GREEN" in found and "DEMO-WATCH" not in found
    assert 'value="green"' in found
    assert "No tokens match these filters." in empty
    assert 'href="/">' in empty and "Clear filters" in empty


def test_clear_filters_link_only_appears_when_a_filter_is_active(client: TestClient) -> None:
    plain = client.get("/").text
    filtered = client.get("/?verdict=GREEN").text

    assert "Clear filters" not in plain
    assert "Clear filters" in filtered


def test_sortable_headers_have_aria_sort_and_toggle_direction(client: TestClient) -> None:
    unsorted = client.get("/").text
    assert 'aria-sort="none"' in unsorted
    assert 'href="/?sort=score&amp;dir=desc"' in unsorted

    descending = client.get("/?sort=score&dir=desc").text
    assert 'aria-sort="descending"' in descending
    assert 'href="/?sort=score&amp;dir=asc"' in descending  # clicking again flips it

    ascending = client.get("/?sort=score&dir=asc").text
    assert 'aria-sort="ascending"' in ascending


def test_sort_by_age_orders_the_rows(client: TestClient) -> None:
    oldest_first = symbols(client.get("/?sort=age&dir=desc").text)
    youngest_first = symbols(client.get("/?sort=age&dir=asc").text)

    assert oldest_first == list(reversed(youngest_first))


def test_the_summary_strip_shows_verdict_counts_and_a_credit_meter(client: TestClient) -> None:
    body = client.get("/").text

    assert "verdict-segment-green" in body and "verdict-segment-watch" in body
    assert "verdict-segment-avoid" in body
    assert "Credits" in body and "/ 3000" in body


def test_a_verdict_segment_click_applies_the_same_filter_as_the_chip(client: TestClient) -> None:
    body = client.get("/").text

    assert 'href="/?verdict=GREEN"' in body  # both the segment and the plain chip use it


def test_a_stale_row_is_flagged_with_the_watch_colour_and_a_clock_icon(engine: Engine) -> None:
    now = datetime.now(UTC)
    _add_token(
        engine,
        "stale-row",
        "STALEROW",
        deployed=now - timedelta(hours=1),
        updated=now - timedelta(minutes=45),
    )
    use(make_session_factory(engine))
    try:
        body = TestClient(app).get("/").text
    finally:
        app.dependency_overrides.clear()

    assert '<td data-label="Updated" class="stale">' in body
    assert "icon" in body[body.index('data-label="Updated" class="stale"') :][:200]


def test_chain_filter_chips_show_a_coloured_dot(client: TestClient) -> None:
    body = client.get("/").text

    assert 'href="/?chain=solana"' in body
    assert "chain-dot chain-solana" in body


# --- UI-3: live status indicator (§5.1), on every page ---


def test_the_live_status_indicator_appears_on_every_page(client: TestClient) -> None:
    for path in ("/", "/status", GREEN):
        body = client.get(path).text
        assert "live-dot live-dot-" in body, path
        assert "Last scan" in body, path


def test_no_page_has_an_inline_onclick_handler(client: TestClient) -> None:
    for path in ("/", "/status", GREEN):
        assert "onclick=" not in client.get(path).text, path
