"""
Developer helper: dump a live page's rendered HTML (and, if present, its
embedded __NEXT_DATA__-style JSON) to a local file so you can inspect the
real structure and fix providers/bookmyshow.py / providers/district.py
selectors if they've drifted from what's in this repo.

Usage:
    python scripts/inspect_provider.py "https://in.bookmyshow.com/explore/home"
    python scripts/inspect_provider.py "https://www.district.in/movies"

Output:
    Writes ./inspect_output.html (full rendered HTML) and, if a JSON blob
    was found, ./inspect_output.json (pretty-printed) next to this script's
    working directory.

This performs a normal page load with a standard browser UA — no fingerprint
spoofing, no CAPTCHA solving. If the target site blocks this, that's a
signal to slow down / stop, not to add evasion.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

# Allow running this script directly from the scripts/ directory.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.async_api import async_playwright  # noqa: E402

from utils import extract_json_blob  # noqa: E402


async def main(url: str) -> None:
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            )
        )
        print(f"Loading {url} ...")
        response = await page.goto(url, wait_until="networkidle")
        print(f"HTTP status: {response.status if response else 'unknown'}")
        html = await page.content()
        Path("inspect_output.html").write_text(html, encoding="utf-8")
        print(f"Wrote inspect_output.html ({len(html)} chars)")

        blob = extract_json_blob(html, "__NEXT_DATA__")
        if blob:
            Path("inspect_output.json").write_text(json.dumps(blob, indent=2), encoding="utf-8")
            print("Wrote inspect_output.json (found __NEXT_DATA__ blob)")
        else:
            print(
                "No __NEXT_DATA__ blob found. Open inspect_output.html and search for "
                "a <script id=\"...\"> tag containing JSON, then update "
                "_JSON_MARKER_ID in the relevant providers/*.py file."
            )
        await browser.close()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python scripts/inspect_provider.py <url>")
        sys.exit(1)
    asyncio.run(main(sys.argv[1]))
