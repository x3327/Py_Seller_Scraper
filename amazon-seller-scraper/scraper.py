# scraper.py

import asyncio
import random
import re
from playwright.async_api import async_playwright, Page, BrowserContext
from playwright_stealth import stealth_async

from proxy_manager import get_proxy_for_playwright
from human_behaviour import (
    simulate_page_entry,
    block_unnecessary_resources,
    random_delay,
    micro_delay,
    simulate_scroll,
    simulate_reading_pause,
)


# ─────────────────────────────────────────
# Browser Fingerprint Configs
# ─────────────────────────────────────────
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
]

VIEWPORTS = [
    {"width": 1920, "height": 1080},
    {"width": 1440, "height": 900},
    {"width": 1366, "height": 768},
    {"width": 1280, "height": 800},
]

LOCALES = ["en-US", "en-GB", "en-CA"]
TIMEZONES = ["America/New_York", "America/Chicago", "America/Los_Angeles", "Europe/London"]

BROWSER_LAUNCH_ARGS = [
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-dev-shm-usage",
    "--disable-blink-features=AutomationControlled",
    "--disable-extensions",
    "--no-first-run",
    "--disable-default-apps",
]


async def create_stealth_context(browser) -> BrowserContext:
    """Create a browser context that looks like a real human user."""
    proxy = get_proxy_for_playwright()
    context = await browser.new_context(
        proxy=proxy,
        user_agent=random.choice(USER_AGENTS),
        viewport=random.choice(VIEWPORTS),
        locale=random.choice(LOCALES),
        timezone_id=random.choice(TIMEZONES),
        java_script_enabled=True,
        accept_downloads=False,
        extra_http_headers={
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Cache-Control": "max-age=0",
        },
    )
    return context


def extract_text_safe(text: str | None) -> str | None:
    """Clean and return text, or None if empty."""
    if not text:
        return None
    cleaned = text.strip()
    return cleaned if cleaned else None


def parse_seller_info(raw_text: str) -> dict:
    """Parse structured seller fields from raw page text."""
    result = {
        "vat_number": None,
        "phone": None,
        "email": None,
    }

    # VAT / Tax ID patterns (covers EU formats: GB/DE/FR/ES/IT, alphanumeric codes)
    vat_patterns = [
        r'VAT(?:\s+Number)?[:\s#]*([A-Z]{2}[A-Z0-9]{5,15})',  # VAT: GB123 / VAT Number: ESN4009908G
        r'Trade Register Number[:\s]*([A-Z0-9\-\/]{6,20})',
        r'Tax ID[:\s]*([A-Z0-9\-]{8,20})',
        r'NIF[:\s]*([A-Z0-9]{8,12})',
        r'CIF[:\s]*([A-Z][0-9A-Z]{7,10})',
        r'Steuer[^\n:]*[:\s]*([0-9/]{8,15})',  # German Steuernummer
    ]
    for pattern in vat_patterns:
        match = re.search(pattern, raw_text, re.IGNORECASE)
        if match:
            result["vat_number"] = match.group(1).strip()
            break

    # Phone patterns
    phone_patterns = [
        r'(?:Phone|Tel|Telephone|Teléfono|Telefon)[:\s]*([+\d\s\-\(\)]{8,20})',
        r'(\+\d{1,3}[\s\-]?\d{3,4}[\s\-]?\d{3,4}[\s\-]?\d{3,4})',
    ]
    for pattern in phone_patterns:
        match = re.search(pattern, raw_text, re.IGNORECASE)
        if match:
            result["phone"] = match.group(1).strip()
            break

    # Email (most available on EU marketplaces)
    email_match = re.search(
        r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}',
        raw_text
    )
    if email_match:
        result["email"] = email_match.group(0)

    return result


# ─────────────────────────────────────────
# ASIN COLLECTION FROM SEARCH/CATEGORY PAGES
# ─────────────────────────────────────────

async def scrape_asins_from_url(
    page: Page,
    url: str,
    category_name: str = "",
    category_path: str = "",
    max_items: int = 0
) -> list[dict]:
    """Scrape ASINs from an Amazon search or category URL."""
    asins = []
    seen = set()

    try:
        await block_unnecessary_resources(page)
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)

        page_title = await page.title()
        if "robot" in page_title.lower() or "captcha" in page_title.lower():
            print(f"  [WARN]  CAPTCHA on category page")
            return []

        await simulate_page_entry(page)

        # Amazon search result cards carry data-asin on their container div
        product_els = await page.query_selector_all(
            'div[data-asin]:not([data-asin=""]):not([data-asin=" "])'
        )

        for el in product_els:
            asin = (await el.get_attribute("data-asin") or "").strip().upper()
            if not asin or len(asin) < 8 or asin in seen:
                continue

            seen.add(asin)

            # Extract title (best effort)
            title = ""
            for sel in ["h2 .a-text-normal", "h2 a span", ".a-size-medium.a-text-normal"]:
                title_el = await el.query_selector(sel)
                if title_el:
                    title = ((await title_el.inner_text()) or "").strip()[:150]
                    if title:
                        break

            asins.append({
                "asin": asin,
                "title": title,
                "category": category_name,
                "category_path": category_path,
            })

            if max_items > 0 and len(asins) >= max_items:
                break

    except Exception as e:
        print(f"  [ERR] Error scraping {url}: {e}")

    return asins


async def scrape_asins_batch(
    category_urls: list[dict],
    marketplace: str = "com",
    max_items: int = 0
) -> list[dict]:
    """
    Scrape ASINs from multiple Amazon category/search URLs.

    category_urls: [{"url": "...", "category_name": "...", "category_path": "..."}]
    max_items: 0 = no limit
    """
    all_asins: list[dict] = []
    seen: set[str] = set()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=BROWSER_LAUNCH_ARGS)

        for i, cat in enumerate(category_urls):
            url = cat.get("url", "")
            if not url:
                continue

            cat_name = cat.get("category_name", "")
            cat_path = cat.get("category_path", "")
            print(f"[{i+1}/{len(category_urls)}] Category: {cat_name or url[:60]}")

            # Check limit before launching browser context
            if max_items > 0 and len(all_asins) >= max_items:
                break

            context = await create_stealth_context(browser)
            page = await context.new_page()
            await stealth_async(page)

            try:
                items_needed = (max_items - len(all_asins)) if max_items > 0 else 0
                batch = await scrape_asins_from_url(page, url, cat_name, cat_path, max_items=items_needed)

                added = 0
                for a in batch:
                    if a["asin"] not in seen:
                        seen.add(a["asin"])
                        all_asins.append(a)
                        added += 1
                        if max_items > 0 and len(all_asins) >= max_items:
                            break

                print(f"  [OK] Got {added} new ASINs (total: {len(all_asins)})")

            except Exception as e:
                print(f"  [ERR] Error: {e}")
            finally:
                await context.close()

            if i < len(category_urls) - 1 and not (max_items > 0 and len(all_asins) >= max_items):
                delay = random.uniform(3, 7)
                print(f"  [WAIT] Waiting {delay:.1f}s...")
                await asyncio.sleep(delay)

        await browser.close()

    return all_asins[:max_items] if max_items > 0 else all_asins


# ─────────────────────────────────────────
# SELLER SCRAPING
# ─────────────────────────────────────────

async def get_seller_id_from_product_page(
    page: Page, asin: str, marketplace: str
) -> tuple[str | None, bool]:
    """
    Navigate to Amazon product page and extract the seller ID.
    Returns (seller_id_or_None, page_was_valid).
    page_was_valid=False means the proxy got a bot-detection stub — caller should retry.
    """
    url = f"https://www.amazon.{marketplace}/dp/{asin}"

    try:
        await block_unnecessary_resources(page)
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)

        page_title = await page.title()
        if "robot" in page_title.lower() or "captcha" in page_title.lower():
            print(f"  [WARN]  CAPTCHA/robot page detected")
            return None, False

        # Wait for actual product content to appear (handles JS-rendered pages)
        try:
            await page.wait_for_selector(
                "#productTitle, #dp-container, #buybox, #merchant-info, #tabular-buybox",
                timeout=15000,
            )
        except Exception:
            pass  # Proceed anyway — we'll check page size next

        await simulate_page_entry(page)

        # Validate page actually loaded — bot-detection stubs are tiny (<15 KB)
        content = await page.content()
        if len(content) < 15000:
            print(f"  [WARN]  Page too small ({len(content)} bytes) — proxy likely blocked, title={await page.title()!r}")
            return None, False

        # Try DOM selectors for "Sold by" / seller link
        # Amazon uses different URL formats per marketplace:
        #   ES/DE/FR: /gp/help/seller/at-a-glance.html?seller=ID
        #   US/UK:    /sp?seller=ID
        seller_selectors = [
            "#merchant-info a[href*='seller=']",
            "#tabular-buybox a[href*='seller=']",
            "#buybox a[href*='seller=']",
            "#sellerProfileTriggerId",
            "a[href*='at-a-glance.html'][href*='seller=']",   # ES/DE/FR
            "a[href*='/sp?seller=']",                          # US/UK
            "a[href*='seller=']:not([href*='bestseller'])",    # generic
        ]

        for selector in seller_selectors:
            el = await page.query_selector(selector)
            if el:
                href = await el.get_attribute("href") or ""
                match = re.search(r'[?&]seller=([A-Z0-9]{8,20})', href)
                if match:
                    return match.group(1), True

        # Fallback: scan raw page source for seller ID patterns
        for pattern in [
            r'"sellerId"\s*:\s*"([A-Z0-9]{8,20})"',
            r'sellerID\s*=\s*["\']([A-Z0-9]{8,20})["\']',
            r'/sp\?seller=([A-Z0-9]{8,20})',
            r'[?&]seller=([A-Z0-9]{8,20})',   # covers ES (&seller=) and US (?seller=)
            r'seller=([A-Z0-9]{8,20})',        # most permissive
        ]:
            match = re.search(pattern, content)
            if match:
                cand = match.group(1)
                if 8 <= len(cand) <= 20:
                    return cand, True

    except Exception as e:
        print(f"  [WARN]  seller ID lookup failed for {asin}: {e}")

    return None, True  # Page loaded OK but genuinely has no seller link


async def scrape_seller_page(page: Page, seller_id: str, marketplace: str) -> dict:
    """Scrape the Amazon seller info page given a seller ID."""
    url = f"https://www.amazon.{marketplace}/sp?seller={seller_id}"
    data: dict = {
        "seller_id": seller_id,
        "marketplace": marketplace,
        "storefront_url": url,
        "business_name": None,
        "address": None,
        "raw_info": "",
        "vat_number": None,
        "phone": None,
        "email": None,
        "rating": None,
    }

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)

        page_title = await page.title()
        if "robot" in page_title.lower() or "captcha" in page_title.lower():
            data["status"] = "captcha"
            return data

        # Wait for actual seller page content to render
        try:
            await page.wait_for_selector(
                "h1, #page-section-detail-seller-info, #seller-about-section, .a-section",
                timeout=15000,
            )
        except Exception:
            pass

        await simulate_page_entry(page)

        # Validate page loaded — bot-detection stubs are tiny
        sp_content = await page.content()
        if len(sp_content) < 15000:
            print(f"  [WARN]  Seller page too small ({len(sp_content)} bytes) — blocked")
            data["status"] = "blocked_page"
            return data

        # Business name
        for name_sel in ["h1.a-size-large", ".a-section h1", "h1"]:
            name_el = await page.query_selector(name_sel)
            if name_el:
                data["business_name"] = extract_text_safe(await name_el.inner_text())
                if data["business_name"]:
                    break

        # Raw info block (address, VAT, phone, etc.)
        raw_text = ""
        for sel in [
            "#page-section-detail-seller-info .a-box-inner",
            "#seller-about-section",
            ".a-section.a-spacing-base",
            "[data-feature-name='baSeller']",
            "#sellerInformation",
        ]:
            el = await page.query_selector(sel)
            if el:
                raw_text = ((await el.inner_text()) or "").strip()
                if len(raw_text) > 30:
                    break

        data["raw_info"] = raw_text

        # Parse structured fields
        parsed = parse_seller_info(raw_text)
        data.update(parsed)

        # Address: Business Address block from raw_info is most reliable — try it first
        if raw_text:
            addr_match = re.search(
                r'Business Address[:\s]+([\s\S]+?)'
                r'(?=\n\n|\nThis seller|\nVAT|\nPhone|\nEmail|\nTrade|\nBusiness Name|\Z)',
                raw_text, re.IGNORECASE
            )
            if addr_match:
                addr_lines = [l.strip() for l in addr_match.group(1).split("\n") if l.strip()]
                if addr_lines:
                    data["address"] = ", ".join(addr_lines)

        # Seller rating
        rating_el = await page.query_selector(".a-icon-alt")
        if rating_el:
            data["rating"] = extract_text_safe(await rating_el.inner_text())

        data["status"] = "success"

    except Exception as e:
        data["status"] = "error"
        data["error"] = str(e)

    return data


async def scrape_sellers_from_asins_batch(
    asin_list: list[dict],
    marketplace: str = "com"
) -> list[dict]:
    """
    Scrape seller info for a list of ASINs.
    Per ASIN:
      1. Navigate product page → extract seller ID
      2. Navigate seller page → extract business info
    """
    results: list[dict] = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=BROWSER_LAUNCH_ARGS)

        for i, asin_info in enumerate(asin_list):
            asin = (asin_info.get("asin", "") if isinstance(asin_info, dict) else str(asin_info)).upper().strip()
            category = asin_info.get("category", "") if isinstance(asin_info, dict) else ""
            category_path = asin_info.get("category_path", "") if isinstance(asin_info, dict) else ""

            if not asin:
                continue

            print(f"[{i+1}/{len(asin_list)}] ASIN: {asin}")

            # Fresh context per ASIN = fresh identity + fresh proxy
            context = await create_stealth_context(browser)
            page = await context.new_page()
            await stealth_async(page)

            try:
                # Step 1 — get seller ID from product page (retry up to 3× on proxy block)
                print(f"  [>>] Fetching seller ID...")
                seller_id = None
                for attempt in range(5):
                    if attempt > 0:
                        # Close old context, open fresh one with a different proxy
                        await context.close()
                        wait_s = random.uniform(8, 20)
                        print(f"  [WAIT] Proxy blocked — waiting {wait_s:.1f}s then retrying (attempt {attempt+1}/5)...")
                        await asyncio.sleep(wait_s)
                        context = await create_stealth_context(browser)
                        page = await context.new_page()
                        await stealth_async(page)

                    seller_id, page_ok = await get_seller_id_from_product_page(page, asin, marketplace)
                    if page_ok:
                        break  # Page loaded fine — no point retrying even if no seller
                    # page not ok → loop continues with fresh proxy

                if not seller_id:
                    print(f"  [WARN]  No seller ID found")
                    results.append({
                        "asin": asin,
                        "seller_id": None,
                        "status": "no_seller_found",
                        "category": category,
                        "category_path": category_path,
                        "marketplace": marketplace,
                    })
                    continue

                print(f"  [OK] Seller ID: {seller_id}")
                await asyncio.sleep(random.uniform(2, 4))

                # Step 2 — scrape seller info page, retry up to 3× on proxy block
                print(f"  [>>] Scraping seller page...")
                seller_data = None
                for sp_attempt in range(5):
                    if sp_attempt > 0:
                        wait_s = random.uniform(10, 25)
                        print(f"  [WAIT] Seller page blocked — waiting {wait_s:.1f}s, retry {sp_attempt+1}/5...")
                        await asyncio.sleep(wait_s)
                        # Fresh context + fresh proxy for seller page
                        await context.close()
                        context = await create_stealth_context(browser)
                        page = await context.new_page()  # keep page ref valid for finally
                        await stealth_async(page)

                    seller_page = await context.new_page()
                    await stealth_async(seller_page)
                    seller_data = await scrape_seller_page(seller_page, seller_id, marketplace)
                    await seller_page.close()

                    if seller_data.get("status") != "blocked_page":
                        break  # Loaded fine (success, captcha, or error — all valid)
                    # Fresh proxy context already created at top of loop for next iteration

                seller_data["asin"] = asin
                seller_data["category"] = category
                seller_data["category_path"] = category_path
                results.append(seller_data)

                print(f"  [OK] {seller_data['status']} | {seller_data.get('business_name', 'N/A')}")

            except Exception as e:
                results.append({
                    "asin": asin,
                    "seller_id": None,
                    "status": "error",
                    "error": str(e),
                    "category": category,
                    "category_path": category_path,
                    "marketplace": marketplace,
                })
                print(f"  [ERR] Error: {e}")

            finally:
                await context.close()

            # Human-like inter-ASIN delay
            if i < len(asin_list) - 1:
                base_delay = random.uniform(5, 10)
                if random.random() < 0.15:
                    base_delay = random.uniform(15, 30)
                    print(f"  [PAUSE]  Longer break: {base_delay:.1f}s")
                else:
                    print(f"  [WAIT] Waiting {base_delay:.1f}s...")
                await asyncio.sleep(base_delay)

        await browser.close()

    return results


# ─────────────────────────────────────────
# ORIGINAL: SCRAPE FROM SELLER IDs DIRECTLY
# ─────────────────────────────────────────

async def scrape_sellers_batch(
    seller_ids: list[str],
    marketplace: str = "com"
) -> list[dict]:
    """
    Scrape a batch of sellers directly by seller ID.
    Fresh browser context per seller for maximum stealth.
    """
    results: list[dict] = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=BROWSER_LAUNCH_ARGS)

        for i, seller_id in enumerate(seller_ids):
            print(f"[{i+1}/{len(seller_ids)}] Seller: {seller_id}")

            context = await create_stealth_context(browser)
            page = await context.new_page()
            await stealth_async(page)

            try:
                result = await scrape_seller_page(page, seller_id, marketplace)
                results.append(result)
                print(f"  [OK] {result['status']} | {result.get('business_name', 'N/A')}")
            except Exception as e:
                results.append({"seller_id": seller_id, "status": "error", "error": str(e)})
                print(f"  [ERR] {e}")
            finally:
                await context.close()

            if i < len(seller_ids) - 1:
                base_delay = random.uniform(4, 9)
                if random.random() < 0.15:
                    base_delay = random.uniform(15, 30)
                    print(f"  [PAUSE]  Longer break: {base_delay:.1f}s")
                else:
                    print(f"  [WAIT] Waiting {base_delay:.1f}s...")
                await asyncio.sleep(base_delay)

        await browser.close()

    return results
