# Fonts (UI redesign, §3.2)

IBM Plex Sans (400, 500, 600) and IBM Plex Mono (400), self-hosted under
`app/web/static/fonts/`. Nothing is requested from Google Fonts or any other CDN at runtime
(`@font-face` in `app/web/static/css/type.css` points only at local `/static/fonts/...` paths);
`tests/unit/test_fonts.py` asserts this on every run.

## Licence

SIL Open Font License 1.1 (Copyright © 2017 IBM Corp., Reserved Font Name "Plex"). The licence
text is committed at `app/web/static/fonts/OFL.txt`, fetched from the upstream
[IBM/plex](https://github.com/IBM/plex) repository. The OFL permits embedding and redistributing
the font files as part of an application, including modification (the subsetting below), as long
as the font is not sold on its own and the licence accompanies it.

## Source and subset

The files are IBM Plex as served by Google Fonts (`fonts.googleapis.com` → `fonts.gstatic.com`),
downloaded once and committed here — this is "self-hosting", not a CDN dependency, because the
running application never talks to Google. Each file is the **Latin subset** (`unicode-range:
U+0000-00FF, ...` — plain ASCII, Latin-1, and common punctuation), which covers the dashboard's
English UI text, addresses and numbers. Cyrillic, Greek, Vietnamese, etc. are not included, so the
files stay small: about 15–24 KB each.

| File | Weight | Source version | SHA-256 |
|---|---|---|---|
| `ibm-plex-sans-400.woff2` | 400 | ibmplexsans v23 | `3b646991d30055a93a4ecc499713d4347953a74a947ecab435ab72070cbdab0e` |
| `ibm-plex-sans-500.woff2` | 500 | ibmplexsans v23 | `0717336fb31fcdcde4b8deb3675bb4a0f7f6d484864afcd6751ac29975962203` |
| `ibm-plex-sans-600.woff2` | 600 | ibmplexsans v23 | `8960851d691c054ed38e259bdcf1a6190d157b4203ed5bb32c632a863fb8ec2f` |
| `ibm-plex-mono-400.woff2` | 400 | ibmplexmono v20 | `08949f728dc52d528e69b1667d15c89a5686a4ee9a296ff90983985f99c380f7` |

`tests/unit/test_fonts.py` pins these hashes, the same way `tests/unit/test_web.py` pins the
vendored htmx release, so an accidental or malicious file swap fails CI.

## Updating

To pick up a new IBM Plex release, or add a weight or a wider subset:

1. Request the CSS with a normal browser user agent, one weight at a time (requesting several
   weights of IBM Plex Sans in one call has been observed to make Google collapse them all to the
   same file — verified 2026-09-27):
   `curl -A "<a modern desktop Chrome UA>" "https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@600&display=swap"`
2. Take the `src: url(...)` from the `/* latin */` block (`unicode-range: U+0000-00FF, ...`).
3. Download that file into `app/web/static/fonts/`, update `type.css`'s `unicode-range` if it
   changed, and update the hashes above and in `test_fonts.py`.
