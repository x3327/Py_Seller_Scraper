"""Trace exactly what get_seller_id_from_product_page does."""
import asyncio
import re
import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace', line_buffering=True)

from playwright.async_api import async_playwright
from playwright_stealth import stealth_async
from scraper import create_stealth_context
from human_behaviour import block_unnecessary_resources, simulate_page_entry

ASIN = "B0B3F8ZW3Z"
MARKETPLACE = "es"

async def debug():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await create_stealth_context(browser)
        page = await context.new_page()
        await stealth_async(page)

        url = f"https://www.amazon.{MARKETPLACE}/dp/{ASIN}"
        print(f"\n=== Testing seller ID lookup for {ASIN} on amazon.{MARKETPLACE} ===")
        print(f"URL: {url}")

        # Exactly what the scraper does
        await block_unnecessary_resources(page)
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)

        page_title = await page.title()
        print(f"Title after goto: {page_title!r}")

        # Skip simulate_page_entry for speed — use a fixed wait
        await page.wait_for_timeout(5000)
        print(f"Title after 5s wait: {await page.title()!r}")
        print(f"Final URL: {page.url}")

        # --- Try all selectors ---
        seller_selectors = [
            "#merchant-info a[href*='seller=']",
            "#tabular-buybox a[href*='seller=']",
            "#buybox a[href*='seller=']",
            "#sellerProfileTriggerId",
            "a[href*='at-a-glance.html'][href*='seller=']",
            "a[href*='/sp?seller=']",
            "a[href*='seller=']:not([href*='bestseller'])",
        ]
        print("\n--- Selector results ---")
        for sel in seller_selectors:
            els = await page.query_selector_all(sel)
            if els:
                for el in els[:3]:
                    href = await el.get_attribute("href") or ""
                    text = (await el.inner_text() or "").strip()[:40]
                    match = re.search(r'[?&]seller=([A-Z0-9]{8,20})', href)
                    print(f"  FOUND [{sel}]: href={href[:80]!r}  text={text!r}  extracted={match.group(1) if match else None}")
            else:
                print(f"  MISS  [{sel}]")

        # --- Try source patterns ---
        content = await page.content()
        print("\n--- Source pattern results ---")
        for pat in [
            r'"sellerId"\s*:\s*"([A-Z0-9]{8,20})"',
            r'sellerID\s*=\s*["\']([A-Z0-9]{8,20})["\']',
            r'/sp\?seller=([A-Z0-9]{8,20})',
            r'[?&]seller=([A-Z0-9]{8,20})',
            r'seller=([A-Z0-9]{8,20})',
        ]:
            matches = re.findall(pat, content)
            unique = list(set(m for m in matches if 8 <= len(m) <= 20))
            if unique:
                print(f"  FOUND [{pat}]: {unique[:5]}")
            else:
                print(f"  MISS  [{pat}]")

        # --- All <a> tags containing seller ---
        print("\n--- All links with 'seller' in href ---")
        all_links = await page.query_selector_all("a[href]")
        count = 0
        for link in all_links:
            href = await link.get_attribute("href") or ""
            if "seller" in href.lower() and "bestseller" not in href.lower():
                text = (await link.inner_text() or "").strip()[:40]
                print(f"  href={href[:100]!r}  text={text!r}")
                count += 1
                if count >= 5:
                    break
        if count == 0:
            print("  (none found)")

        await browser.close()
        print("\nDone.")

asyncio.run(debug())
