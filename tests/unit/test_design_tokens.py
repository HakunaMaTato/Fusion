"""UI_REDESIGN.md §3: every colour and spacing value used anywhere is a token from tokens.css.

Only tokens.css itself may define a raw hex colour or a raw px spacing/radius number; everything
else must reference it with var(...). This is the "grep check" the phase's acceptance criterion
names, scoped as follows:

- hex colours: flagged anywhere in a stylesheet other than tokens.css.
- raw px numbers: flagged only in spacing-shaped declarations (margin*, padding*, gap/row-gap/
  column-gap, border-radius) — not in border-width (1-2px hairlines and the focus ring are
  explicitly allowed by §3.3), not in font-size (SVG chart labels are superseded by ECharts in
  UI-5), and not in sizing properties like width/height (the spec itself later hands out
  non-scale pixel sizes for specific components, e.g. a 56px table row in §5.4).
"""

import re
from pathlib import Path

CSS_DIR = Path(__file__).parents[2] / "app" / "web" / "static" / "css"
TOKENS_FILE = CSS_DIR / "tokens.css"

HEX_COLOUR = re.compile(r"#[0-9a-fA-F]{3,8}\b")
SPACING_PROPERTY = re.compile(
    r"\b(margin[a-z-]*|padding[a-z-]*|gap|row-gap|column-gap|border-radius)\s*:\s*([^;]+);"
)
RAW_PX = re.compile(r"(?<![\w.])\d+(\.\d+)?px\b")


def _other_css_files() -> list[Path]:
    return sorted(p for p in CSS_DIR.glob("*.css") if p != TOKENS_FILE)


def test_tokens_file_exists_and_declares_the_spec_variables() -> None:
    assert TOKENS_FILE.exists()
    css = TOKENS_FILE.read_text(encoding="utf-8")
    for name in (
        "--ink",
        "--surface",
        "--surface-raised",
        "--line",
        "--text",
        "--text-muted",
        "--text-faint",
        "--accent",
        "--green",
        "--watch",
        "--avoid",
        "--green-tint",
        "--watch-tint",
        "--avoid-tint",
        "--series-smart",
        "--series-bundle",
        "--series-holders",
        "--series-rest",
        "--font-sans",
        "--font-mono",
        "--s-1",
        "--s-2",
        "--s-3",
        "--s-4",
        "--s-5",
        "--s-6",
        "--s-7",
        "--s-8",
        "--radius-chip",
        "--radius-control",
        "--radius-panel",
        "--radius-pill",
        "--content-max-width",
        "--grid-columns",
        "--grid-gutter",
    ):
        assert f"{name}:" in css, f"tokens.css is missing {name}"


def test_no_stylesheet_other_than_tokens_css_defines_a_raw_hex_colour() -> None:
    for path in _other_css_files():
        css = path.read_text(encoding="utf-8")
        found = HEX_COLOUR.findall(css)
        assert not found, f"{path.name} has a raw hex colour outside tokens.css: {found}"


def test_no_stylesheet_other_than_tokens_css_has_a_raw_px_spacing_value() -> None:
    for path in _other_css_files():
        css = path.read_text(encoding="utf-8")
        for _, value in SPACING_PROPERTY.findall(css):
            assert not RAW_PX.search(value), (
                f"{path.name} has a raw px spacing value: {value.strip()!r}"
            )


def test_tokens_css_itself_is_the_only_place_hex_colours_live() -> None:
    # A sanity check on the check itself: tokens.css DOES have hex colours (it defines them).
    assert HEX_COLOUR.search(TOKENS_FILE.read_text(encoding="utf-8"))
