#!/usr/bin/env python3
"""Embed logo as base64 data URI into docs/redmuse-intro.html (portable single file)."""

from __future__ import annotations

import base64
import io
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
HTML = ROOT / "docs" / "redmuse-intro.html"
LOGO_SRC = ROOT / "frontend" / "public" / "logo.png"
PLACEHOLDER = "%%LOGO%%"


def main() -> None:
    img = Image.open(LOGO_SRC).convert("RGBA")
    im = img.copy()
    im.thumbnail((88, 88), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, format="PNG", optimize=True)
    uri = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")

    html = HTML.read_text(encoding="utf-8")
    if PLACEHOLDER in html:
        html = html.replace(PLACEHOLDER, uri)
    else:
        import re

        html, n = re.subn(
            r'(<img src=")data:image/png;base64,[^"]+(" alt="Red Muse")',
            rf"\1{uri}\2",
            html,
        )
        if n == 0:
            raise SystemExit(f"no logo slot found in {HTML}")
    HTML.write_text(html, encoding="utf-8")
    print(f"Embedded logo into {HTML} ({len(html):,} chars, {html.count(uri)} uses)")


if __name__ == "__main__":
    main()
