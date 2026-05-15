# scraper.py

import asyncio
import json
import random
import re
from pathlib import Path

from playwright.async_api import async_playwright, Page, BrowserContext
from playwright_stealth import stealth_async

from proxy_manager import get_proxy, mark_proxy_failed
from human_behaviour import (
    simulate_page_entry,
    simulate_page_entry_fast,
    block_unnecessary_resources,
    random_delay,
    micro_delay,
    simulate_scroll,
    simulate_reading_pause,
)


# ─────────────────────────────────────────
# Cookie persistence dir
# ─────────────────────────────────────────
COOKIES_DIR = Path(__file__).parent / "cookies"
COOKIES_DIR.mkdir(exist_ok=True)


# ─────────────────────────────────────────
# Browser Fingerprint Configs
# ─────────────────────────────────────────

# Chrome 136/135/134 + Edge 136 + Firefox 127 — current real-world distribution
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36 Edg/136.0.0.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:127.0) Gecko/20100101 Firefox/127.0",
]

VIEWPORTS = [
    {"width": 1920, "height": 1080},
    {"width": 1536, "height": 864},
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
    "--disable-infobars",
    "--window-size=1920,1080",
]

# Amazon "sold by Amazon" signals — all EU/US/CA languages (check as lowercase)
# IMPORTANT: Amazon shows English text on ES/DE/FR/IT when &language=en is appended to URLs.
# Both localised AND English variants must be listed here.
AMAZON_SOLD_PHRASES = [
    # English (shown on all marketplaces when &language=en or base language is EN)
    "ships from and sold by amazon",
    "shipped and sold by amazon",
    "sold by amazon.com",
    "sold by amazon",
    "sold by amazon canada",
    "shipper / seller\namazon",       # EN buybox table row (ES/DE/FR/IT with &language=en)
    "seller\namazon",                 # short form of the EN buybox row
    # Spanish
    "remitente / vendedor\namazon",
    "vendedor\namazon",
    # German
    "verkäufer\namazon",
    # French
    "vendu par amazon",
    "vendeur\namazon",
    # Italian
    "venduto da amazon",
    "venditore\namazon",
    # Amazon legal entities
    "amazon eu s.à r.l.",
    "amazon services europe",
    "amazon.co.uk",
]


# ─────────────────────────────────────────
# Sec-CH-UA header generation
# ─────────────────────────────────────────

def _ua_to_sec_ch_ua(ua: str) -> tuple[str, str, str]:
    """
    Derive Sec-CH-UA client-hint headers from the user agent string.
    Returns (Sec-CH-UA, Sec-CH-UA-Mobile, Sec-CH-UA-Platform).
    These MUST match the UA or Amazon's WAF fingerprint check will flag the mismatch.
    """
    if "Edg/" in ua:
        v = (re.search(r'Edg/(\d+)', ua) or re.search(r'Chrome/(\d+)', ua))
        ver = v.group(1) if v else "136"
        return (
            f'"Microsoft Edge";v="{ver}", "Chromium";v="{ver}", "Not A(Brand";v="8"',
            "?0",
            '"Windows"',
        )
    if "Firefox/" in ua:
        v = re.search(r'Firefox/(\d+)', ua)
        ver = v.group(1) if v else "127"
        return (
            f'"Firefox";v="{ver}", "Not A(Brand";v="8"',
            "?0",
            '"Windows"',
        )
    # Chrome (Windows or Mac)
    platform = '"macOS"' if "Macintosh" in ua else '"Windows"'
    v = re.search(r'Chrome/(\d+)', ua)
    ver = v.group(1) if v else "136"
    return (
        f'"Chromium";v="{ver}", "Not A(Brand";v="8", "Google Chrome";v="{ver}"',
        "?0",
        platform,
    )


# ─────────────────────────────────────────
# Cookie helpers
# ─────────────────────────────────────────

async def load_marketplace_cookies(context: BrowserContext, marketplace: str) -> None:
    """Load persisted cookies for this marketplace into the browser context."""
    cookie_file = COOKIES_DIR / f"{marketplace.replace('.', '_')}.json"
    if not cookie_file.exists():
        return
    try:
        cookies = json.loads(cookie_file.read_text(encoding="utf-8"))
        await context.add_cookies(cookies)
        print(f"  [COOKIES] Loaded {len(cookies)} cookies for {marketplace}")
    except Exception as e:
        print(f"  [COOKIES] Load failed: {e}")


async def save_marketplace_cookies(context: BrowserContext, marketplace: str) -> None:
    """Persist cookies from the browser context to disk for future reuse."""
    try:
        cookies = await context.cookies()
        if not cookies:
            return
        cookie_file = COOKIES_DIR / f"{marketplace.replace('.', '_')}.json"
        cookie_file.write_text(json.dumps(cookies, indent=2), encoding="utf-8")
        print(f"  [COOKIES] Saved {len(cookies)} cookies for {marketplace}")
    except Exception as e:
        print(f"  [COOKIES] Save failed: {e}")


# ─────────────────────────────────────────
# Stealth browser context factory
# ─────────────────────────────────────────

async def create_stealth_context(
    browser,
    use_proxy: bool = True,
    marketplace: str = "",
    load_cookies: bool = True,
) -> tuple[BrowserContext, dict | None]:
    """
    Create a browser context that looks like a real human user.
    Returns (context, proxy_dict_or_None).
    proxy_dict is returned so callers can call mark_proxy_failed() when needed.
    """
    proxy_dict: dict | None = None
    playwright_proxy = None

    if use_proxy:
        proxy_dict = get_proxy()
        playwright_proxy = {
            "server":   proxy_dict["server"],
            "username": proxy_dict["username"],
            "password": proxy_dict["password"],
        }
        print(f"  [PROXY] Using {proxy_dict['server']}")
    else:
        print("  [INFO] Using direct connection (no proxy)")

    # Locale / language match per marketplace — Amazon detects mismatches
    if marketplace == "co.uk":
        locale = "en-GB"; timezone = "Europe/London";      accept_lang = "en-GB,en;q=0.9"
    elif marketplace == "de":
        locale = "de-DE"; timezone = "Europe/Berlin";      accept_lang = "de-DE,de;q=0.9,en;q=0.8"
    elif marketplace == "fr":
        locale = "fr-FR"; timezone = "Europe/Paris";       accept_lang = "fr-FR,fr;q=0.9,en;q=0.8"
    elif marketplace == "es":
        locale = "es-ES"; timezone = "Europe/Madrid";      accept_lang = "es-ES,es;q=0.9,en;q=0.8"
    elif marketplace == "it":
        locale = "it-IT"; timezone = "Europe/Rome";        accept_lang = "it-IT,it;q=0.9,en;q=0.8"
    elif marketplace == "ca":
        locale = "en-CA"; timezone = "America/Toronto";    accept_lang = "en-CA,en;q=0.9"
    elif marketplace == "com.mx":
        locale = "es-MX"; timezone = "America/Mexico_City"; accept_lang = "es-MX,es;q=0.9,en;q=0.8"
    else:
        locale = random.choice(LOCALES)
        timezone = random.choice(TIMEZONES)
        accept_lang = "en-US,en;q=0.9"

    viewport = random.choice(VIEWPORTS)
    ua = random.choice(USER_AGENTS)
    sec_ch_ua, sec_ch_ua_mobile, sec_ch_ua_platform = _ua_to_sec_ch_ua(ua)

    # Short language code for navigator.languages array (e.g. "de-DE" → "de")
    lang_short = locale.split("-")[0]

    context = await browser.new_context(
        proxy=playwright_proxy,
        user_agent=ua,
        viewport=viewport,
        locale=locale,
        timezone_id=timezone,
        java_script_enabled=True,
        accept_downloads=False,
        extra_http_headers={
            "Accept":                    "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "Accept-Language":           accept_lang,
            "Accept-Encoding":           "gzip, deflate, br, zstd",
            "Connection":                "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest":            "document",
            "Sec-Fetch-Mode":            "navigate",
            "Sec-Fetch-Site":            "none",
            "Sec-Fetch-User":            "?1",
            "Cache-Control":             "max-age=0",
            "Sec-CH-UA":                 sec_ch_ua,
            "Sec-CH-UA-Mobile":          sec_ch_ua_mobile,
            "Sec-CH-UA-Platform":        sec_ch_ua_platform,
        },
    )

    # ── Enhanced stealth init script ──────────────────────────────────────────
    # Runs before any page script — patches all headless-detection vectors.
    await context.add_init_script(f"""
        // ── 1. Delete CDP (Chrome DevTools Protocol) artifacts ──
        // Automation frameworks leave window.cdc_* symbols — fingerprint checks look for these.
        try {{
            for (const key of Object.getOwnPropertyNames(window)) {{
                if (key.startsWith('cdc_')) {{
                    try {{ delete window[key]; }} catch (_) {{}}
                }}
            }}
        }} catch (_) {{}}

        // ── 2. navigator.webdriver — must be undefined, not false ──
        try {{
            Object.defineProperty(navigator, 'webdriver', {{
                get: () => undefined,
                configurable: true,
            }});
        }} catch (_) {{}}

        // ── 3. Hardware fingerprints — headless defaults to 1 core / no memory ──
        try {{ Object.defineProperty(navigator, 'hardwareConcurrency', {{ get: () => 8 }}); }} catch (_) {{}}
        try {{ Object.defineProperty(navigator, 'deviceMemory',         {{ get: () => 8 }}); }} catch (_) {{}}

        // ── 4. Network info (navigator.connection) — headless lacks this ──
        try {{
            Object.defineProperty(navigator, 'connection', {{
                get: () => ({{
                    effectiveType:      '4g',
                    rtt:                Math.floor(Math.random() * 30 + 40),
                    downlink:           Math.round((Math.random() * 5 + 8) * 10) / 10,
                    saveData:           false,
                    onchange:           null,
                    addEventListener:    () => {{}},
                    removeEventListener: () => {{}},
                }}),
            }});
        }} catch (_) {{}}

        // ── 5. window.chrome — required for Chrome fingerprinting checks ──
        if (!window.chrome || !window.chrome.runtime) {{
            window.chrome = {{
                app: {{
                    isInstalled:   false,
                    getDetails:    () => ({{}}),
                    getIsInstalled: () => false,
                }},
                runtime: {{
                    connect:      () => ({{}}),
                    sendMessage:  () => {{}},
                    onConnect:    {{ addListener: () => {{}}, removeListener: () => {{}} }},
                    onMessage:    {{ addListener: () => {{}}, removeListener: () => {{}} }},
                    id:           undefined,
                }},
                loadTimes: () => ({{
                    firstPaintAfterLoadTime: 0,
                    requestTime:             (Date.now() - 500) / 1000,
                    startLoadTime:           (Date.now() - 500) / 1000,
                    commitLoadTime:          (Date.now() - 400) / 1000,
                    finishDocumentLoadTime:  Date.now() / 1000,
                    finishLoadTime:          Date.now() / 1000,
                    firstPaintTime:          (Date.now() - 50) / 1000,
                    navigationType:          'Other',
                    wasFetchedViaSpdy:       true,
                }}),
                csi: () => ({{ startE: Date.now() - 500, onloadT: Date.now(), pageT: 1000, tran: 15 }}),
            }};
        }}

        // ── 6. navigator.plugins — headless has empty plugin list ──
        try {{
            const pluginData = [
                {{ name: 'Chrome PDF Plugin',  filename: 'internal-pdf-viewer',              description: 'Portable Document Format' }},
                {{ name: 'Chrome PDF Viewer',  filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: '' }},
                {{ name: 'Native Client',      filename: 'internal-nacl-plugin',             description: '' }},
            ];
            Object.defineProperty(navigator, 'plugins', {{
                get: () => {{
                    const arr = Object.create(PluginArray.prototype);
                    pluginData.forEach((p, i) => {{
                        const plugin = Object.create(Plugin.prototype);
                        Object.defineProperty(plugin, 'name',        {{ get: () => p.name }});
                        Object.defineProperty(plugin, 'filename',    {{ get: () => p.filename }});
                        Object.defineProperty(plugin, 'description', {{ get: () => p.description }});
                        Object.defineProperty(plugin, 'length',      {{ get: () => 1 }});
                        arr[i] = plugin;
                    }});
                    Object.defineProperty(arr, 'length', {{ get: () => pluginData.length }});
                    return arr;
                }},
            }});
        }} catch (_) {{}}

        // ── 7. navigator.languages — must match the locale ──
        try {{
            Object.defineProperty(navigator, 'languages', {{
                get: () => ['{locale}', '{lang_short}', 'en'],
            }});
        }} catch (_) {{}}

        // ── 8. document.hasFocus — headless returns false ──
        try {{ Object.defineProperty(document, 'hasFocus', {{ value: () => true }}); }} catch (_) {{}}

        // ── 9. Window outer dimensions — headless omits browser chrome ──
        try {{ Object.defineProperty(window, 'outerHeight', {{ get: () => window.innerHeight + 88 }}); }} catch (_) {{}}
        try {{ Object.defineProperty(window, 'outerWidth',  {{ get: () => window.innerWidth  }}); }} catch (_) {{}}

        // ── 10. Screen dimensions — must match viewport ──
        try {{ Object.defineProperty(screen, 'width',       {{ get: () => {viewport['width']} }}); }} catch (_) {{}}
        try {{ Object.defineProperty(screen, 'height',      {{ get: () => {viewport['height']} }}); }} catch (_) {{}}
        try {{ Object.defineProperty(screen, 'availWidth',  {{ get: () => {viewport['width']} }}); }} catch (_) {{}}
        try {{ Object.defineProperty(screen, 'availHeight', {{ get: () => {viewport['height'] - 40} }}); }} catch (_) {{}}
        try {{ Object.defineProperty(screen, 'colorDepth',  {{ get: () => 24 }}); }} catch (_) {{}}
        try {{ Object.defineProperty(screen, 'pixelDepth',  {{ get: () => 24 }}); }} catch (_) {{}}

        // ── 11. WebGL vendor / renderer spoof ──
        // WAF fingerprint checks call getParameter(UNMASKED_VENDOR/RENDERER_WEBGL)
        try {{
            const patchWebGL = (ctx) => {{
                const orig = ctx.prototype.getParameter;
                ctx.prototype.getParameter = function(p) {{
                    if (p === 37445) return 'Intel Inc.';
                    if (p === 37446) return 'Intel(R) Iris(TM) Plus Graphics 640';
                    return orig.call(this, p);
                }};
            }};
            patchWebGL(WebGLRenderingContext);
            if (typeof WebGL2RenderingContext !== 'undefined') patchWebGL(WebGL2RenderingContext);
        }} catch (_) {{}}

        // ── 12. Permissions API ──
        try {{
            const origQuery = window.navigator.permissions && window.navigator.permissions.query;
            if (origQuery) {{
                window.navigator.permissions.query = (params) => (
                    params.name === 'notifications'
                        ? Promise.resolve({{ state: 'denied' }})
                        : origQuery(params)
                );
            }}
        }} catch (_) {{}}

        // ── 13. Battery API ──
        try {{
            if (navigator.getBattery) {{
                navigator.getBattery = () => Promise.resolve({{
                    charging:        true,
                    chargingTime:    0,
                    dischargingTime: Infinity,
                    level:           1.0,
                    addEventListener: () => {{}},
                }});
            }}
        }} catch (_) {{}}
    """)

    if load_cookies:
        await load_marketplace_cookies(context, marketplace)

    return context, proxy_dict


# ─────────────────────────────────────────
# Text helpers
# ─────────────────────────────────────────

def extract_text_safe(text: str | None) -> str | None:
    """Clean and return text, or None if empty."""
    if not text:
        return None
    cleaned = text.strip()
    return cleaned if cleaned else None


def parse_seller_info(raw_text: str) -> dict:
    """Parse structured seller fields from raw page text — all EU languages."""
    result = {
        "vat_number": None,
        "phone": None,
        "email": None,
    }

    # VAT / Tax ID — covers all EU marketplace languages + Chinese OSS registrations
    vat_patterns = [
        r'UStID(?:Nr\.?)?[:\s]*([A-Z]{0,2}[A-Z0-9]{8,15})',          # DE: UStID: DE454526764
        r'(?:N[uú]mero\s+de\s+)?IVA[:\s]*([A-Z0-9]{7,15})',          # ES/IT (Número de IVA / IVA:)
        r'Partita\s+IVA[:\s]*([IT0-9]{10,13})',                        # IT: Partita IVA
        r'P\.?\s*IVA[:\s]*([0-9]{10,13})',                             # IT short form
        r'(?:Num[eé]ro\s+de\s+)?TVA[:\s]*([A-Z]{0,2}[A-Z0-9]{8,15})',# FR
        r'VAT(?:\s+(?:Registration\s+)?Number)?[:\s#]*([A-Z]{2}[A-Z0-9]{5,15})',  # EN
        r'Tax(?:\s+(?:ID|Number|Reg))?[:\s]*([A-Z0-9\-]{8,20})',
        r'NIF[:\s]*([A-Z0-9]{8,12})',
        r'CIF[:\s]*([A-Z][0-9A-Z]{7,10})',
        r'Steuer[^\n:]*[:\s]*([0-9/]{8,15})',                          # DE: Steuernummer
        r'Trade\s+Register(?:\s+Number)?[:\s]*([A-Z0-9\-\/]{6,20})',
        r'Company\s+(?:Number|Reg\.?)[:\s]*([A-Z0-9\-]{6,15})',        # UK Companies House
        r'BTW[:\s]*([A-Z]{2}[A-Z0-9]{7,15})',                          # NL/BE VAT
        r'CVR[:\s]*(\d{8})',                                             # DK company number
        r'((?:ES|DE|FR|IT|GB|NL|BE|PL|SE|AT|CZ|PT|RO|HU|HR)[0-9]{8,12})(?=[\s,\n]|$)',  # bare EU VAT code
    ]
    for pattern in vat_patterns:
        match = re.search(pattern, raw_text, re.IGNORECASE)
        if match:
            result["vat_number"] = match.group(1).strip()
            break

    # Phone — all EU languages
    phone_patterns = [
        r'(?:Phone|Tel\.?|Telephone|Teléfono|Telefon(?:nummer)?|Téléphone|Telefono)[:\s]*([+\d\s\-\(\)\.]{7,25})',
        r'(\+\d{1,3}[\s\-]?\(?\d{1,4}\)?[\s\-]?\d{3,4}[\s\-]?\d{3,6})',
    ]
    for pattern in phone_patterns:
        match = re.search(pattern, raw_text, re.IGNORECASE)
        if match:
            phone_raw = match.group(1).strip().rstrip('.')
            if len(re.sub(r'\D', '', phone_raw)) >= 7:
                result["phone"] = phone_raw
                break

    # Email
    email_match = re.search(
        r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}',
        raw_text
    )
    if email_match:
        result["email"] = email_match.group(0)

    return result


# ─────────────────────────────────────────
# ASIN COLLECTION FROM SEARCH / CATEGORY PAGES
# ─────────────────────────────────────────

async def scrape_asins_from_url(
    page: Page,
    url: str,
    category_name: str = "",
    category_path: str = "",
    max_items: int = 0,
    marketplace: str = "",
) -> list[dict]:
    """
    Scrape ASINs from an Amazon search or category URL.
    Raises on timeout/navigation failure so the caller can retry with a fresh proxy.
    """
    asins = []
    seen = set()

    await block_unnecessary_resources(page)

    if marketplace == "co.uk":
        print(f"  [DEBUG] UK marketplace — wait_until=load + 15s WAF challenge window")
        await page.goto(url, wait_until="load", timeout=90000)
        content = await page.content()
        print(f"  [DEBUG] Initial load: {len(content)} bytes, title={await page.title()!r}")
        if len(content) < 20000:
            print(f"  [INFO] WAF challenge page detected — waiting up to 15s...")
            try:
                await page.wait_for_function(
                    "document.querySelectorAll('div[data-asin]').length > 0",
                    timeout=15000, polling=2000,
                )
                print(f"  [INFO] WAF challenge passed!")
            except Exception:
                print(f"  [WARN] WAF challenge did not pass within 15s")
    else:
        await page.goto(url, wait_until="domcontentloaded", timeout=90000)

    content = await page.content()
    current_url = page.url
    page_title = await page.title()
    print(f"  [DEBUG] URL after goto: {current_url[:100]}")
    print(f"  [DEBUG] Page title: {page_title!r}")
    print(f"  [DEBUG] Content length: {len(content)} bytes")

    if marketplace != "co.uk" and len(content) < 20000:
        print(f"  [INFO] Small initial page ({len(content)} bytes) — following redirect...")
        try:
            await page.wait_for_load_state("load", timeout=60000)
            content = await page.content()
            current_url = page.url
            page_title = await page.title()
            print(f"  [DEBUG] After redirect — URL: {current_url[:100]}, size: {len(content)} bytes")
        except Exception as e:
            print(f"  [WARN] wait_for_load_state failed: {e}")

    if "robot" in page_title.lower() or "captcha" in page_title.lower():
        print(f"  [WARN] CAPTCHA on category page: {page_title!r}")
        raise RuntimeError(f"CAPTCHA detected: {page_title}")

    if len(content) < 20000:
        print(f"  [WARN] Page too small ({len(content)} bytes) — stub content preview:\n{content[:800]}")
        raise RuntimeError(f"Bot-detection stub ({len(content)} bytes)")

    try:
        await page.wait_for_selector(
            'div[data-asin]:not([data-asin=""]), div[data-component-type="s-search-result"]',
            timeout=15000,
        )
    except Exception as e:
        print(f"  [WARN] wait_for_selector data-asin timed out: {e}")

    # Search results pages need less simulation than product pages — fast is enough
    await simulate_page_entry_fast(page)

    product_els = await page.query_selector_all(
        'div[data-asin]:not([data-asin=""]):not([data-asin=" "])'
    )
    print(f"  [DEBUG] Found {len(product_els)} product elements with data-asin")

    for el in product_els:
        asin = (await el.get_attribute("data-asin") or "").strip().upper()
        if not asin or len(asin) < 8 or asin in seen:
            continue

        seen.add(asin)
        title = ""
        for sel in [
            "h2 .a-text-normal",
            "h2 a span",
            ".a-size-medium.a-text-normal",
            "h2 span[aria-label]",
            ".a-size-base-plus",
            "[data-cy='title-recipe-title'] span",
            ".s-title-instructions-style span",
            "h2 .a-link-normal span",
            "h2 span",
        ]:
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

    print(f"  [DEBUG] Extracted {len(asins)} valid ASINs from page")
    return asins


# Number of category URLs scraped simultaneously within one batch.
# 3 = 3 Playwright sessions in parallel → ~3× throughput vs. sequential.
_CATEGORY_CONCURRENCY = 3


async def _scrape_single_category(
    browser,
    cat: dict,
    marketplace: str,
    cat_sem: asyncio.Semaphore,
    index: int,
    total: int,
    max_items: int,
    global_count: list,       # [int] — mutable shared counter
    global_seen: set,
    global_lock: asyncio.Lock,
) -> list[dict]:
    """
    Scrape one category URL concurrently.
    Mirrors _scrape_single_asin: semaphore-guarded, proxy-then-direct retry.
    """
    async with cat_sem:
        # Early-exit if global max already reached by another concurrent task
        async with global_lock:
            if max_items > 0 and global_count[0] >= max_items:
                return []

        url = cat.get("url", "")
        if not url:
            return []

        cat_name = cat.get("category_name", "")
        cat_path = cat.get("category_path", "")
        print(f"[{index+1}/{total}] Category: {cat_name or url[:60]}")

        batch: list[dict] = []
        for attempt in range(3):
            use_proxy = False if marketplace == "co.uk" else (attempt < 2)
            print(f"  [ATTEMPT {attempt+1}/3] proxy={use_proxy}")
            context, proxy_dict = await create_stealth_context(
                browser, use_proxy=use_proxy, marketplace=marketplace
            )
            page = await context.new_page()
            await stealth_async(page)
            try:
                async with global_lock:
                    remaining = (max_items - global_count[0]) if max_items > 0 else 0
                batch = await scrape_asins_from_url(
                    page, url, cat_name, cat_path,
                    max_items=remaining, marketplace=marketplace,
                )
                await save_marketplace_cookies(context, marketplace)
                await context.close()
                break
            except Exception as e:
                print(f"  [RETRY {attempt+1}/3] Error: {type(e).__name__}: {str(e)[:120]}")
                if proxy_dict and not use_proxy is False:
                    mark_proxy_failed(proxy_dict)
                await context.close()
                if attempt < 2:
                    wait = random.uniform(2, 5)   # was 5-12s — tightened for speed
                    print(f"  [WAIT] Cooling {wait:.1f}s before retry...")
                    await asyncio.sleep(wait)

        # Merge results into the shared seen/count under lock — avoid duplicates
        added: list[dict] = []
        async with global_lock:
            for a in batch:
                if max_items > 0 and global_count[0] >= max_items:
                    break
                if a["asin"] not in global_seen:
                    global_seen.add(a["asin"])
                    global_count[0] += 1
                    added.append(a)

        print(f"  [OK] Got {len(added)} new ASINs from {cat_name!r} (running total: {global_count[0]})")
        return added


async def scrape_asins_batch(
    category_urls: list[dict],
    marketplace: str = "com",
    max_items: int = 0,
) -> list[dict]:
    """
    Scrape ASINs from multiple Amazon category/search URLs.
    Processes _CATEGORY_CONCURRENCY URLs simultaneously for ~3× throughput.
    category_urls: [{"url": "...", "category_name": "...", "category_path": "..."}]
    """
    global_seen: set[str] = set()
    global_lock = asyncio.Lock()
    global_count: list[int] = [0]   # shared mutable counter

    cat_sem = asyncio.Semaphore(_CATEGORY_CONCURRENCY)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=BROWSER_LAUNCH_ARGS)

        tasks = [
            _scrape_single_category(
                browser, cat, marketplace, cat_sem,
                i, len(category_urls),
                max_items, global_count, global_seen, global_lock,
            )
            for i, cat in enumerate(category_urls)
            if cat.get("url")
        ]
        raw = await asyncio.gather(*tasks, return_exceptions=True)

        await browser.close()

    all_asins: list[dict] = []
    for r in raw:
        if isinstance(r, list):
            all_asins.extend(r)

    return all_asins[:max_items] if max_items > 0 else all_asins


# Number of ASINs processed simultaneously within one batch.
# 2 = 2 Playwright sessions in parallel → ~2× throughput.
_ASIN_CONCURRENCY = 2


# ─────────────────────────────────────────
# SELLER SCRAPING
# ─────────────────────────────────────────

async def get_seller_id_from_product_page(
    page: Page,
    asin: str,
    marketplace: str,
    use_proxy: bool = True,
) -> tuple[str | None, bool, bool]:
    """
    Navigate to Amazon product page and extract the seller ID.

    Returns (seller_id_or_None, page_was_valid, is_amazon_sold).
      - page_was_valid=False → bot-detection stub; caller should retry with fresh proxy
      - is_amazon_sold=True  → Amazon is the seller; no point retrying
      - seller_id is non-None → success
    """
    url = f"https://www.amazon.{marketplace}/dp/{asin}"
    # Fail proxies fast (15s) — direct connection gets full 60s
    timeout = 15000 if use_proxy else 60000

    try:
        await block_unnecessary_resources(page)
        await page.goto(url, wait_until="domcontentloaded", timeout=timeout)

        # Handle small stub pages
        content = await page.content()
        print(f"  [DEBUG] Product page: {len(content)} bytes, title={await page.title()!r}")

        if len(content) < 15000:
            if marketplace == "co.uk":
                print(f"  [INFO] UK WAF challenge ({len(content)} bytes) — waiting up to 15s...")
                try:
                    await page.wait_for_function(
                        "document.querySelector('#productTitle') !== null || "
                        "document.querySelector(\"a[href*='seller=']\") !== null || "
                        "document.querySelector('#dp') !== null",
                        timeout=15000, polling=2000,
                    )
                    print(f"  [INFO] UK product page loaded after WAF")
                except Exception:
                    print(f"  [WARN] UK WAF challenge not passed within 15s")
            else:
                print(f"  [INFO] Small initial page ({len(content)} bytes) — following redirect...")
                try:
                    await page.wait_for_load_state("load", timeout=15000)
                except Exception:
                    pass
            content = await page.content()

        page_title = await page.title()
        print(f"  [DEBUG] Product page after wait: {len(content)} bytes, title={page_title!r}")

        if "robot" in page_title.lower() or "captcha" in page_title.lower():
            print(f"  [WARN] CAPTCHA/robot page detected")
            return None, False, False

        if len(content) < 15000:
            print(f"  [WARN] Page too small ({len(content)} bytes) — proxy likely blocked")
            return None, False, False

        # Wait for product content
        try:
            await page.wait_for_selector(
                "#productTitle, #dp-container, #buybox, #merchant-info, #tabular-buybox",
                timeout=20000,
            )
        except Exception:
            pass

        # ── Early Amazon-sold detection ──────────────────────────────────────
        # Check BEFORE simulating page entry — saves 5-12s per Amazon-sold ASIN.
        # Amazon-sold products have no 3rd-party seller link; detecting early skips retries.
        try:
            merch_el = await page.query_selector("#merchant-info, #tabular-buybox, #buybox, #buyBoxAccordion")
            if merch_el:
                merch_text = ((await merch_el.inner_text()) or "").lower()
                print(f"  [DEBUG] Early buybox text: {merch_text[:150]!r}")
                if any(phrase in merch_text for phrase in AMAZON_SOLD_PHRASES):
                    print(f"  [INFO] Amazon is the seller — skipping retries (amazon_sold)")
                    return None, True, True
        except Exception as e:
            print(f"  [DEBUG] Early buybox check error: {e}")

        await simulate_page_entry(page)

        # Also wait specifically for a seller link to appear (JS-rendered buybox)
        try:
            await page.wait_for_selector("a[href*='seller=']", timeout=5000)
        except Exception:
            pass  # No seller link visible — proceed to fallback checks

        content = await page.content()

        # ── DOM selectors for seller link ────────────────────────────────────
        seller_selectors = [
            "#merchant-info a[href*='seller=']",
            "#tabular-buybox a[href*='seller=']",
            "#buybox a[href*='seller=']",
            "#buyBoxAccordion a[href*='seller=']",
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
                    return match.group(1), True, False

        # ── Fallback: scan raw page source ───────────────────────────────────
        for pattern in [
            r'"sellerId"\s*:\s*"([A-Z0-9]{8,20})"',
            r'sellerID\s*=\s*["\']([A-Z0-9]{8,20})["\']',
            r'/sp\?seller=([A-Z0-9]{8,20})',
            r'[?&]seller=([A-Z0-9]{8,20})',
            r'seller=([A-Z0-9]{8,20})',
        ]:
            match = re.search(pattern, content)
            if match:
                cand = match.group(1)
                if 8 <= len(cand) <= 20:
                    return cand, True, False

    except Exception as e:
        print(f"  [WARN] seller ID lookup failed for {asin}: {type(e).__name__}: {str(e)[:120]}")
        return None, False, False

    # Page loaded but no seller link found — check if Amazon-sold via page text
    seller_snippets = re.findall(r'.{0,40}seller.{0,40}', content, re.IGNORECASE)[:8]
    print(f"  [DEBUG] No seller link found. Seller snippets: {seller_snippets[:3]}")
    try:
        merch_el = await page.query_selector("#merchant-info, #tabular-buybox, #buybox")
        merch_text = (await merch_el.inner_text())[:300] if merch_el else "N/A"
        print(f"  [DEBUG] Buybox text: {merch_text!r}")
        # Final Amazon-sold check on full buybox text
        if any(phrase in merch_text.lower() for phrase in AMAZON_SOLD_PHRASES):
            print(f"  [INFO] Amazon is the seller (final check) — amazon_sold")
            return None, True, True
    except Exception as dbg_err:
        print(f"  [DEBUG] Could not read buybox: {dbg_err}")

    return None, True, False  # Page loaded OK but genuinely no seller link


async def scrape_seller_page(
    page: Page,
    seller_id: str,
    marketplace: str,
    use_proxy: bool = True,
) -> dict:
    """Scrape the Amazon seller info page given a seller ID."""
    url = f"https://www.amazon.{marketplace}/sp?seller={seller_id}"
    timeout = 15000 if use_proxy else 60000
    data: dict = {
        "seller_id":    seller_id,
        "marketplace":  marketplace,
        "storefront_url": url,
        "business_name": None,
        "address":      None,
        "raw_info":     "",
        "vat_number":   None,
        "phone":        None,
        "email":        None,
        "rating":       None,
    }

    try:
        await block_unnecessary_resources(page)
        await page.goto(url, wait_until="domcontentloaded", timeout=timeout)

        sp_content = await page.content()
        sp_title = await page.title()
        sp_url = page.url
        print(f"  [DEBUG] Seller page URL: {sp_url[:80]}")
        print(f"  [DEBUG] Seller page title: {sp_title!r}, size: {len(sp_content)} bytes")

        if len(sp_content) < 15000:
            print(f"  [INFO] Seller page small ({len(sp_content)} bytes) — following redirect...")
            try:
                await page.wait_for_load_state("load", timeout=60000)
                sp_content = await page.content()
                print(f"  [DEBUG] After redirect: {page.url[:80]}, size: {len(sp_content)} bytes")
            except Exception as e:
                print(f"  [WARN] Seller page redirect failed: {e}")

        page_title = await page.title()
        if "robot" in page_title.lower() or "captcha" in page_title.lower():
            print(f"  [WARN] Seller page CAPTCHA: {page_title!r}")
            data["status"] = "captcha"
            return data

        try:
            await page.wait_for_selector(
                "h1, #page-section-detail-seller-info, #seller-about-section, .a-section",
                timeout=20000,
            )
        except Exception:
            pass

        # Seller info pages need less simulation — product page already proved we're human
        await simulate_page_entry_fast(page)

        sp_content = await page.content()
        if len(sp_content) < 15000:
            print(f"  [WARN] Seller page too small ({len(sp_content)} bytes) — blocked")
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
            "#sellerInformation",
            "[data-feature-name='baSeller']",
            ".a-section.a-spacing-base",
            "#aag_description",
            "div[id*='seller']",
        ]:
            el = await page.query_selector(sel)
            if el:
                candidate = ((await el.inner_text()) or "").strip()
                if len(candidate) > len(raw_text):
                    raw_text = candidate

        if len(raw_text) < 50:
            body_el = await page.query_selector("body")
            if body_el:
                raw_text = ((await body_el.inner_text()) or "").strip()[:5000]

        data["raw_info"] = raw_text
        print(f"  [DEBUG] raw_info length: {len(raw_text)} chars")
        if raw_text:
            print(f"  [DEBUG] raw_info preview: {raw_text[:200]!r}")

        # ── Parse VAT/phone/email from selected section first ─────────────────
        parsed = parse_seller_info(raw_text)

        # ── Fallback: scan full body text (up to 20 000 chars) ───────────────
        # Amazon ES puts business info (VAT, address) at the BOTTOM of seller
        # pages — often past the first 5 000 chars of "About seller" text.
        # We always do a second pass on the full body to catch it.
        if not all(parsed.values()):
            body_el2 = await page.query_selector("body")
            if body_el2:
                full_body = ((await body_el2.inner_text()) or "").strip()[:20000]
                if len(full_body) > len(raw_text):
                    parsed2 = parse_seller_info(full_body)
                    # Merge: prefer the specific-section value if already found
                    for k in ("vat_number", "phone", "email"):
                        if not parsed.get(k) and parsed2.get(k):
                            parsed[k] = parsed2[k]
                            print(f"  [DEBUG] Full-body fallback found {k}: {parsed2[k]!r}")

        data.update(parsed)
        print(f"  [DEBUG] Parsed → VAT: {parsed.get('vat_number')!r}, phone: {parsed.get('phone')!r}, email: {parsed.get('email')!r}")

        # Address — multilingual label matching
        if raw_text:
            addr_match = re.search(
                r'(?:Business Address|Geschäftsadresse|Direcci[oó]n empresarial'
                r'|Adresse(?: professionnelle)?|Indirizzo(?: aziendale)?)[:\s]+([\s\S]+?)'
                r'(?=\n\n|\nThis seller|\nVAT|\nUStID|\nN[oú]mero|\nNuméro|\nPartita'
                r'|\nPhone|\nTel|\nEmail|\nCorreo|\nE-Mail|\nTrade|\nBusiness Name'
                r'|\nGeschäftsname|\nNombre de empresa|\nDieser Verkäufer|\nEste vendedor'
                r'|\nCe vendeur|\nQuesto venditore|\Z)',
                raw_text, re.IGNORECASE
            )
            if addr_match:
                addr_raw = addr_match.group(1)
                skip_keywords = {
                    "delivery policies", "other policies", "help", "products",
                    "see all products", "leave seller", "returns", "refund",
                    "a-to-z", "amazon", "learn more", "share your thoughts",
                    "filter by", "all stars", "star only", "all positive",
                }
                addr_lines = []
                for l in addr_raw.split("\n"):
                    l = l.strip()
                    if not l:
                        continue
                    if any(kw in l.lower() for kw in skip_keywords):
                        break
                    addr_lines.append(l)
                if addr_lines:
                    data["address"] = ", ".join(addr_lines)
            print(f"  [DEBUG] Address: {data.get('address')!r}")

        # Seller rating
        for rating_sel in [
            ".a-icon-alt",
            "[data-hook='seller-feedback-summary']",
            ".feedback-detail-stars .a-icon-alt",
        ]:
            rating_el = await page.query_selector(rating_sel)
            if rating_el:
                rating_text = extract_text_safe(await rating_el.inner_text())
                if rating_text:
                    data["rating"] = rating_text
                    break

        data["status"] = "success"

    except Exception as e:
        data["status"] = "error"
        data["error"] = str(e)

    return data


async def _scrape_single_asin(
    browser,
    asin_info: dict,
    marketplace: str,
    asin_sem: asyncio.Semaphore,
    index: int,
    total: int,
) -> dict:
    """
    Process one ASIN concurrently: product page → seller page.
    Retry strategy: proxy first, then direct (2 attempts total — fast).
    """
    async with asin_sem:
        asin = (asin_info.get("asin", "") if isinstance(asin_info, dict) else str(asin_info)).upper().strip()
        category = asin_info.get("category", "") if isinstance(asin_info, dict) else ""
        category_path = asin_info.get("category_path", "") if isinstance(asin_info, dict) else ""

        if not asin:
            return {}

        print(f"[{index+1}/{total}] ASIN: {asin}")

        initial_use_proxy = (marketplace != "co.uk")
        context, proxy_dict = await create_stealth_context(
            browser, use_proxy=initial_use_proxy, marketplace=marketplace
        )
        page = await context.new_page()
        await stealth_async(page)

        try:
            # ── Step 1: Get seller ID — proxy attempt then direct (2 total) ───
            print(f"  [>>] Fetching seller ID...")
            seller_id = None
            amazon_sold = False
            use_proxy = initial_use_proxy

            for attempt in range(2):  # 0 = proxy; 1 = direct
                if attempt > 0:
                    await context.close()
                    wait_s = random.uniform(3, 6)
                    print(f"  [WAIT] Retry — direct connection in {wait_s:.1f}s...")
                    await asyncio.sleep(wait_s)
                    use_proxy = False
                    context, proxy_dict = await create_stealth_context(
                        browser, use_proxy=False, marketplace=marketplace
                    )
                    page = await context.new_page()
                    await stealth_async(page)

                seller_id, page_ok, amazon_sold = await get_seller_id_from_product_page(
                    page, asin, marketplace, use_proxy=use_proxy
                )

                if seller_id or amazon_sold:
                    break

                if not page_ok and proxy_dict:
                    mark_proxy_failed(proxy_dict)
                    print(f"  [PROXY] Marked proxy failed — switching to direct")
                elif page_ok:
                    print(f"  [INFO] Page OK but no seller link — trying direct...")

            # ── Results ──────────────────────────────────────────────────────
            if amazon_sold:
                print(f"  [INFO] Sold by Amazon")
                return {
                    "asin": asin, "seller_id": None, "status": "amazon_sold",
                    "category": category, "category_path": category_path, "marketplace": marketplace,
                }

            if not seller_id:
                print(f"  [WARN] No seller found")
                return {
                    "asin": asin, "seller_id": None, "status": "no_seller_found",
                    "category": category, "category_path": category_path, "marketplace": marketplace,
                }

            print(f"  [OK] Seller ID: {seller_id}")
            await save_marketplace_cookies(context, marketplace)
            await asyncio.sleep(random.uniform(1, 2))

            # ── Step 2: Scrape seller info page ──────────────────────────────
            print(f"  [>>] Scraping seller page...")
            seller_page = await context.new_page()
            await stealth_async(seller_page)
            seller_data = await scrape_seller_page(seller_page, seller_id, marketplace, use_proxy=use_proxy)
            await seller_page.close()

            if seller_data.get("status") == "blocked_page":
                # Retry seller page on direct if proxy blocked
                if proxy_dict:
                    mark_proxy_failed(proxy_dict)
                print(f"  [PROXY] Seller page blocked — retrying direct...")
                await context.close()
                context, proxy_dict = await create_stealth_context(browser, use_proxy=False, marketplace=marketplace)
                seller_page2 = await context.new_page()
                await stealth_async(seller_page2)
                seller_data = await scrape_seller_page(seller_page2, seller_id, marketplace, use_proxy=False)
                await seller_page2.close()
            else:
                await save_marketplace_cookies(context, marketplace)

            seller_data["asin"] = asin
            seller_data["category"] = category
            seller_data["category_path"] = category_path
            print(f"  [OK] {seller_data['status']} | {seller_data.get('business_name', 'N/A')}")
            return seller_data

        except Exception as e:
            print(f"  [ERR] {asin}: {e}")
            return {
                "asin": asin, "seller_id": None, "status": "error", "error": str(e),
                "category": category, "category_path": category_path, "marketplace": marketplace,
            }
        finally:
            try:
                await context.close()
            except Exception:
                pass


async def scrape_sellers_from_asins_batch(
    asin_list: list[dict],
    marketplace: str = "com",
) -> list[dict]:
    """
    Scrape seller info for a list of ASINs.
    Processes _ASIN_CONCURRENCY ASINs simultaneously for ~2× throughput.
    """
    asin_sem = asyncio.Semaphore(_ASIN_CONCURRENCY)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=BROWSER_LAUNCH_ARGS)

        tasks = [
            _scrape_single_asin(browser, asin_info, marketplace, asin_sem, i, len(asin_list))
            for i, asin_info in enumerate(asin_list)
        ]
        raw = await asyncio.gather(*tasks, return_exceptions=True)

        await browser.close()

    results = []
    for r in raw:
        if isinstance(r, Exception):
            results.append({"status": "error", "error": str(r)})
        elif r:
            results.append(r)
    return results


# ─────────────────────────────────────────
# SCRAPE FROM SELLER IDs DIRECTLY
# ─────────────────────────────────────────

async def scrape_sellers_batch(
    seller_ids: list[str],
    marketplace: str = "com",
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

            use_proxy = (marketplace != "co.uk")
            context, proxy_dict = await create_stealth_context(
                browser, use_proxy=use_proxy, marketplace=marketplace
            )
            page = await context.new_page()
            await stealth_async(page)

            try:
                result = await scrape_seller_page(page, seller_id, marketplace, use_proxy=use_proxy)
                if result.get("status") == "success":
                    await save_marketplace_cookies(context, marketplace)
                elif result.get("status") == "blocked_page" and proxy_dict:
                    mark_proxy_failed(proxy_dict)
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
                    print(f"  [PAUSE] Longer break: {base_delay:.1f}s")
                else:
                    print(f"  [WAIT] Waiting {base_delay:.1f}s...")
                await asyncio.sleep(base_delay)

        await browser.close()

    return results
