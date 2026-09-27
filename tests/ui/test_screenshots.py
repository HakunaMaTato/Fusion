"""UI_REDESIGN.md §10: screenshots at three breakpoints, plus an axe-core accessibility pass with
zero serious/critical violations. Run with `pytest tests/ui` (needs the `ui` extra installed and
`playwright install chromium` -- both are outside the default `pytest` invocation and its
`requirements-dev.lock`; see the dedicated `ui-tests` CI job).

This intentionally does not do pixel-diff visual regression against committed baseline images:
the project has no established workflow for reviewing and updating baselines when a phase
deliberately changes the design, so a screenshot that differs for an *intended* change would just
be manually re-approved anyway. Screenshots are saved as this run's own artifacts
(`tests/ui/screenshots/`, gitignored) for a human to look at, which is what every phase's PR
description has done manually so far -- this automates capturing them, not judging them.
"""

from pathlib import Path

import pytest
from axe_core_python.sync_playwright import Axe
from playwright.sync_api import Page

VIEWPORTS = {
    "desktop": {"width": 1440, "height": 900},
    "tablet": {"width": 1024, "height": 768},
    "mobile": {"width": 390, "height": 844},
}
SCREENSHOT_DIR = Path(__file__).parent / "screenshots"
SERIOUS_IMPACTS = {"serious", "critical"}


def pages(live_server: str, green_token_path: str) -> dict[str, str]:
    return {"list": f"{live_server}/", "token": f"{live_server}{green_token_path}"}


@pytest.mark.parametrize("viewport_name", list(VIEWPORTS))
@pytest.mark.parametrize("page_name", ["list", "token"])
def test_screenshot(
    page: Page,
    live_server: str,
    green_token_path: str,
    page_name: str,
    viewport_name: str,
) -> None:
    page.set_viewport_size(VIEWPORTS[viewport_name])
    page.goto(pages(live_server, green_token_path)[page_name])
    page.wait_for_load_state("networkidle")

    SCREENSHOT_DIR.mkdir(exist_ok=True)
    page.screenshot(path=SCREENSHOT_DIR / f"{page_name}-{viewport_name}.png", full_page=True)


@pytest.mark.parametrize("page_name", ["list", "token", "status"])
def test_no_serious_accessibility_violations(
    page: Page, live_server: str, green_token_path: str, page_name: str
) -> None:
    url = {**pages(live_server, green_token_path), "status": f"{live_server}/status"}[page_name]
    page.goto(url)
    page.wait_for_load_state("networkidle")

    results = Axe().run(page)
    serious = [v for v in results["violations"] if v["impact"] in SERIOUS_IMPACTS]
    summary = [f"{v['id']} ({v['impact']}): {v['help']}" for v in serious]
    assert not serious, f"{page_name}: {summary}"


def test_keyboard_navigation_reaches_the_filters_and_table(page: Page, live_server: str) -> None:
    """A slice of the §10 "keyboard-only walkthrough": tabbing from the top of the list page
    reaches the search input and then a real link, each with a visible focus ring, without
    getting stuck (issue: everything must work with a keyboard, not just a mouse)."""
    page.goto(f"{live_server}/")
    page.wait_for_load_state("networkidle")

    seen_search = False
    focused_tag = None
    for _ in range(30):
        page.keyboard.press("Tab")
        handle = page.evaluate_handle("document.activeElement")
        focused_tag = handle.evaluate("el => el.tagName")
        if page.evaluate("document.activeElement === document.querySelector('input[name=q]')"):
            seen_search = True
            break

    assert seen_search, f"never reached the search input by keyboard (last focus: {focused_tag})"

    # The search input itself suppresses its native outline (list.css) in favour of its wrapping
    # `.toolbar-search` pill changing border colour on `:focus-within` -- still a real, visible
    # indicator (WCAG 2.4.7), just not the `outline` property, so check whichever one applies.
    has_indicator = page.evaluate(
        """() => {
            const el = document.activeElement;
            if (getComputedStyle(el).outlineStyle !== "none") return true;
            const wrap = el.closest(".toolbar-search");
            if (!wrap) return false;
            const probe = document.createElement("span");
            probe.style.color = getComputedStyle(document.documentElement)
                .getPropertyValue("--accent").trim();
            document.body.appendChild(probe);
            const accentRgb = getComputedStyle(probe).color;
            probe.remove();
            return getComputedStyle(wrap).borderColor === accentRgb;
        }"""
    )
    assert has_indicator, "the focused search input has no visible focus indicator"
