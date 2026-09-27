"""UI_REDESIGN.md §3.2: self-hosted, OFL-licensed IBM Plex fonts, no runtime CDN request."""

import hashlib
import re
from pathlib import Path

FONTS_DIR = Path(__file__).parents[2] / "app" / "web" / "static" / "fonts"
TYPE_CSS = Path(__file__).parents[2] / "app" / "web" / "static" / "css" / "type.css"

# docs/fonts.md documents how to update these (source, subset, and how to re-derive the hashes).
SHA256 = {
    "ibm-plex-sans-400.woff2": "3b646991d30055a93a4ecc499713d4347953a74a947ecab435ab72070cbdab0e",
    "ibm-plex-sans-500.woff2": "0717336fb31fcdcde4b8deb3675bb4a0f7f6d484864afcd6751ac29975962203",
    "ibm-plex-sans-600.woff2": "8960851d691c054ed38e259bdcf1a6190d157b4203ed5bb32c632a863fb8ec2f",
    "ibm-plex-mono-400.woff2": "08949f728dc52d528e69b1667d15c89a5686a4ee9a296ff90983985f99c380f7",
}
FILES = tuple(SHA256)


def test_every_declared_font_file_matches_its_pinned_hash() -> None:
    for name, digest in SHA256.items():
        path = FONTS_DIR / name
        assert path.exists(), name
        data = path.read_bytes()
        assert data[:4] == b"wOF2", f"{name} is not a woff2 file"
        assert hashlib.sha256(data).hexdigest() == digest, f"{name} does not match docs/fonts.md"


def test_the_licence_is_committed_and_is_the_sil_open_font_license() -> None:
    licence = (FONTS_DIR / "OFL.txt").read_text(encoding="utf-8")

    assert "SIL Open Font License" in licence
    assert "IBM" in licence


def test_type_css_only_references_local_font_files() -> None:
    css = TYPE_CSS.read_text(encoding="utf-8")

    assert "fonts.googleapis.com" not in css
    assert "fonts.gstatic.com" not in css
    for name in FILES:
        assert f"/static/fonts/{name}" in css, name


def test_no_stylesheet_or_template_requests_a_font_cdn_at_runtime() -> None:
    root = Path(__file__).parents[2]
    offenders = []
    for pattern in ("app/web/static/**/*.css", "app/web/templates/**/*.html"):
        for path in root.glob(pattern):
            text = path.read_text(encoding="utf-8")
            if re.search(r"fonts\.(googleapis|gstatic)\.com", text):
                offenders.append(str(path.relative_to(root)))
    assert not offenders, offenders
