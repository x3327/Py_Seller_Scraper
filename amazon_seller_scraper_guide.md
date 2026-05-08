# Amazon Seller Scraper — Full Build Guide
> For use with **n8n automation** via Google Cloud Run  
> Stack: Python · FastAPI · Playwright · Stealth · Residential Proxies

---

## 📁 Project Structure

```
amazon-seller-scraper/
├── main.py               # FastAPI app (n8n calls this)
├── scraper.py            # Core Playwright scraper logic
├── proxy_manager.py      # Proxy rotation handler
├── human_behaviour.py    # Human-like delays and interactions
├── requirements.txt
├── Dockerfile
└── .env                  # Proxy credentials (never commit this)
```

---

## 🔐 Environment Variables

Create a `.env` file in root. **Never commit this to Git.**

```env
API_KEY=your-secret-key-for-n8n
PORT=8080
```

Proxies are stored directly in `proxy_manager.py` (see below) or can be moved to `.env` if preferred.

---

## 📦 requirements.txt

```txt
fastapi==0.111.0
uvicorn==0.29.0
playwright==1.44.0
playwright-stealth==1.0.6
pydantic==2.7.0
python-dotenv==1.0.1
asyncio==3.4.3
```

---

## 🌐 proxy_manager.py — Residential Proxy Rotation

This module handles rotating through your 10 Webshare residential proxies. Each proxy is used in round-robin with randomisation to avoid patterns.

```python
# proxy_manager.py

import random

# ─────────────────────────────────────────
# Your Webshare Residential Proxies
# Format: IP:PORT:USERNAME:PASSWORD
# ─────────────────────────────────────────
RAW_PROXIES = [
    "31.59.20.176:6754:edonqeko:kxvif6tazp3e",
    "92.113.242.158:6742:edonqeko:kxvif6tazp3e",
    "198.23.239.134:6540:edonqeko:kxvif6tazp3e",
    "45.38.107.97:6014:edonqeko:kxvif6tazp3e",
    "107.172.163.27:6543:edonqeko:kxvif6tazp3e",
    "216.10.27.159:6837:edonqeko:kxvif6tazp3e",
    "142.111.67.146:5611:edonqeko:kxvif6tazp3e",
    "191.96.254.138:6185:edonqeko:kxvif6tazp3e",
    "31.58.9.4:6077:edonqeko:kxvif6tazp3e",
    "23.229.19.94:8689:edonqeko:kxvif6tazp3e",
]


def parse_proxies(raw_list: list[str]) -> list[dict]:
    """Parse raw proxy strings into structured dicts."""
    parsed = []
    for proxy in raw_list:
        parts = proxy.split(":")
        parsed.append({
            "server": f"http://{parts[0]}:{parts[1]}",
            "username": parts[2],
            "password": parts[3],
        })
    return parsed


PROXIES = parse_proxies(RAW_PROXIES)

# Track usage count per proxy to distribute load evenly
_usage_count = {i: 0 for i in range(len(PROXIES))}
_last_used_index = -1


def get_proxy() -> dict:
    """
    Returns a proxy using weighted rotation:
    - Prefer least-used proxy
    - Add randomness to avoid strict patterns
    - Never use same proxy twice in a row
    """
    global _last_used_index

    # Get indices sorted by usage (least used first)
    sorted_indices = sorted(_usage_count.keys(), key=lambda i: _usage_count[i])

    # Take the 5 least-used proxies and pick randomly among them
    candidates = [i for i in sorted_indices[:5] if i != _last_used_index]

    if not candidates:
        candidates = [i for i in sorted_indices if i != _last_used_index]

    chosen_index = random.choice(candidates)
    _usage_count[chosen_index] += 1
    _last_used_index = chosen_index

    return PROXIES[chosen_index]


def get_proxy_for_playwright() -> dict:
    """Returns proxy formatted for Playwright browser context."""
    proxy = get_proxy()
    return {
        "server": proxy["server"],
        "username": proxy["username"],
        "password": proxy["password"],
    }


def reset_usage_counts():
    """Reset usage counts — call this daily if running long-term."""
    global _usage_count
    _usage_count = {i: 0 for i in range(len(PROXIES))}
```

---

## 🤖 human_behaviour.py — Human-Like Interactions

This module simulates real human browsing behaviour to avoid detection by Amazon's bot systems.

```python
# human_behaviour.py

import asyncio
import random
from playwright.async_api import Page


# ─────────────────────────────────────────
# Delay Profiles
# Use "cautious" for Amazon (recommended)
# ─────────────────────────────────────────
DELAY_PROFILES = {
    "fast":     {"min": 500,  "max": 1500},   # Risky on Amazon
    "normal":   {"min": 1500, "max": 3500},   # Balanced
    "cautious": {"min": 3000, "max": 7000},   # Recommended for Amazon
    "paranoid": {"min": 7000, "max": 15000},  # Maximum safety
}

ACTIVE_PROFILE = "cautious"


async def random_delay(profile: str = ACTIVE_PROFILE):
    """Wait a random human-like amount of time."""
    p = DELAY_PROFILES[profile]
    delay_ms = random.randint(p["min"], p["max"])
    await asyncio.sleep(delay_ms / 1000)


async def micro_delay():
    """Tiny pause — simulates human reaction time between actions."""
    await asyncio.sleep(random.uniform(0.1, 0.5))


async def simulate_mouse_movement(page: Page):
    """
    Move mouse in a natural curved path across the page.
    Humans never move mouse in straight lines.
    """
    # Random start and end positions
    start_x = random.randint(100, 400)
    start_y = random.randint(100, 400)
    end_x = random.randint(500, 1200)
    end_y = random.randint(200, 700)

    # Move in small steps to simulate natural movement
    steps = random.randint(10, 20)
    for i in range(steps):
        x = start_x + (end_x - start_x) * i / steps + random.randint(-5, 5)
        y = start_y + (end_y - start_y) * i / steps + random.randint(-5, 5)
        await page.mouse.move(x, y)
        await asyncio.sleep(random.uniform(0.01, 0.05))


async def simulate_scroll(page: Page):
    """
    Scroll down the page naturally, as a human would
    when reading content.
    """
    # Scroll in chunks, not one big jump
    total_scroll = random.randint(300, 800)
    chunks = random.randint(3, 6)
    scroll_per_chunk = total_scroll // chunks

    for _ in range(chunks):
        await page.evaluate(f"window.scrollBy(0, {scroll_per_chunk})")
        await asyncio.sleep(random.uniform(0.3, 0.9))

    # Sometimes scroll back up a little (humans do this)
    if random.random() < 0.3:
        await page.evaluate(f"window.scrollBy(0, -{random.randint(50, 150)})")
        await asyncio.sleep(random.uniform(0.2, 0.5))


async def simulate_reading_pause(page: Page):
    """
    Pause as if reading the page content.
    Longer pauses on content-heavy pages.
    """
    read_time = random.uniform(2.0, 5.0)
    await asyncio.sleep(read_time)


async def simulate_page_entry(page: Page):
    """
    Full sequence of human behaviour when landing on a new page:
    1. Brief pause (page loaded, eyes scanning)
    2. Mouse movement
    3. Scroll to read
    4. Reading pause
    """
    await micro_delay()
    await simulate_mouse_movement(page)
    await random_delay("cautious")
    await simulate_scroll(page)
    await simulate_reading_pause(page)


async def block_unnecessary_resources(page: Page):
    """
    Block images, CSS fonts, and media to speed up scraping.
    We only need the HTML text content.
    """
    async def handle_route(route):
        resource_type = route.request.resource_type
        blocked_types = ["image", "media", "font", "stylesheet"]
        blocked_domains = ["google-analytics.com", "doubleclick.net",
                           "amazon-adsystem.com", "scorecardresearch.com"]

        url = route.request.url
        if resource_type in blocked_types:
            await route.abort()
        elif any(domain in url for domain in blocked_domains):
            await route.abort()
        else:
            await route.continue_()

    await page.route("**/*", handle_route)
```

---

## 🕷️ scraper.py — Core Playwright Scraper with Stealth

```python
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
)


# ─────────────────────────────────────────
# Browser Fingerprint Configs
# Rotate these to appear as different users
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


async def create_stealth_context(browser) -> BrowserContext:
    """
    Create a browser context that looks like a real human user.
    New context = new identity (different fingerprint).
    """
    proxy = get_proxy_for_playwright()

    context = await browser.new_context(
        proxy=proxy,
        user_agent=random.choice(USER_AGENTS),
        viewport=random.choice(VIEWPORTS),
        locale=random.choice(LOCALES),
        timezone_id=random.choice(TIMEZONES),
        # Mimic real browser capabilities
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


def parse_seller_page(html_content: str, raw_text: str) -> dict:
    """
    Parse seller info from page text.
    Amazon's seller page structure:
    amazon.com/sp?seller=SELLER_ID
    """
    result = {
        "business_name": None,
        "business_type": None,
        "address": None,
        "country": None,
        "vat_number": None,
        "phone": None,
        "email": None,
    }

    # Extract VAT number (EU format: GB123456789, DE123456789, etc.)
    vat_patterns = [
        r'VAT[:\s#]*([A-Z]{2}[0-9A-Z]{5,15})',
        r'VAT Number[:\s]*([A-Z]{2}[0-9]{7,12})',
        r'Tax ID[:\s]*([A-Z0-9\-]{8,20})',
    ]
    for pattern in vat_patterns:
        match = re.search(pattern, raw_text, re.IGNORECASE)
        if match:
            result["vat_number"] = match.group(1).strip()
            break

    # Extract phone number
    phone_patterns = [
        r'(?:Phone|Tel|Telephone)[:\s]*([+\d\s\-\(\)]{8,20})',
        r'(\+\d{1,3}[\s\-]?\d{3,4}[\s\-]?\d{3,4}[\s\-]?\d{3,4})',
    ]
    for pattern in phone_patterns:
        match = re.search(pattern, raw_text, re.IGNORECASE)
        if match:
            result["phone"] = match.group(1).strip()
            break

    # Extract email (mainly available on EU Amazon)
    email_match = re.search(
        r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}',
        raw_text
    )
    if email_match:
        result["email"] = email_match.group(0)

    return result


async def scrape_single_seller(
    page: Page,
    seller_id: str,
    marketplace: str = "com"
) -> dict:
    """
    Scrape a single Amazon seller's detail page.

    Args:
        page: Playwright page object
        seller_id: Amazon seller ID (e.g. A1B2C3XYZ)
        marketplace: Amazon marketplace (com, co.uk, de, fr, it, es)
    """
    url = f"https://www.amazon.{marketplace}/sp?seller={seller_id}"

    try:
        # Block unnecessary resources (speeds up scraping)
        await block_unnecessary_resources(page)

        # Navigate to seller page
        await page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=30000
        )

        # Check if we hit a CAPTCHA
        page_title = await page.title()
        if "robot" in page_title.lower() or "captcha" in page_title.lower():
            return {
                "seller_id": seller_id,
                "status": "captcha",
                "error": "CAPTCHA detected — try again with different proxy",
                "data": None
            }

        # Simulate human behaviour on the page
        await simulate_page_entry(page)

        # ── Extract structured data ──────────────────────────
        data = {"seller_id": seller_id, "marketplace": marketplace}

        # Business name
        try:
            name_el = await page.query_selector("h1.a-size-large")
            if not name_el:
                name_el = await page.query_selector(".a-section h1")
            data["business_name"] = extract_text_safe(
                await name_el.inner_text() if name_el else None
            )
        except Exception:
            data["business_name"] = None

        # Full address block — contains address, VAT, phone
        try:
            # Amazon stores seller info in a structured box
            info_selectors = [
                "#page-section-detail-seller-info .a-box-inner",
                ".a-section.a-spacing-base",
                "[data-feature-name='baSeller']",
            ]
            raw_text = ""
            for selector in info_selectors:
                el = await page.query_selector(selector)
                if el:
                    raw_text = await el.inner_text()
                    if len(raw_text) > 50:
                        break

            data["raw_info"] = raw_text.strip()

            # Parse structured fields from raw text
            parsed = parse_seller_page("", raw_text)
            data.update(parsed)

        except Exception as e:
            data["parse_error"] = str(e)

        # Seller rating
        try:
            rating_el = await page.query_selector(".a-icon-alt")
            data["rating"] = extract_text_safe(
                await rating_el.inner_text() if rating_el else None
            )
        except Exception:
            data["rating"] = None

        # Storefront URL
        data["storefront_url"] = url
        data["status"] = "success"

        return data

    except Exception as e:
        return {
            "seller_id": seller_id,
            "status": "error",
            "error": str(e),
            "data": None
        }


async def scrape_sellers_batch(
    seller_ids: list[str],
    marketplace: str = "com"
) -> list[dict]:
    """
    Scrape a batch of sellers.
    Creates a fresh browser context per seller for maximum stealth.
    """
    results = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
                "--disable-extensions",
                "--no-first-run",
                "--disable-default-apps",
            ]
        )

        for i, seller_id in enumerate(seller_ids):
            print(f"[{i+1}/{len(seller_ids)}] Scraping seller: {seller_id}")

            # ── Fresh context per seller = new identity ──────
            context = await create_stealth_context(browser)
            page = await context.new_page()

            # Apply stealth patches (hides automation signals)
            await stealth_async(page)

            try:
                result = await scrape_single_seller(page, seller_id, marketplace)
                results.append(result)
                print(f"  ✅ Status: {result['status']}")

            except Exception as e:
                results.append({
                    "seller_id": seller_id,
                    "status": "error",
                    "error": str(e)
                })
                print(f"  ❌ Error: {str(e)}")

            finally:
                await context.close()

            # ── Human-like delay between sellers ─────────────
            # Vary the delay so requests don't look scheduled
            if i < len(seller_ids) - 1:  # No delay after last item
                base_delay = random.uniform(4, 9)
                # Occasionally take a longer break (like a human would)
                if random.random() < 0.15:  # 15% chance of longer break
                    base_delay = random.uniform(15, 30)
                    print(f"  ⏸️  Taking longer break: {base_delay:.1f}s")
                else:
                    print(f"  ⏳ Waiting {base_delay:.1f}s before next seller...")
                await asyncio.sleep(base_delay)

        await browser.close()

    return results
```

---

## 🚀 main.py — FastAPI App (n8n calls this)

```python
# main.py

import os
import asyncio
from fastapi import FastAPI, HTTPException, Header
from pydantic import BaseModel
from dotenv import load_dotenv

from scraper import scrape_sellers_batch

load_dotenv()

app = FastAPI(title="Amazon Seller Scraper API")

# ── API Key Auth (set this in n8n HTTP Request headers) ──
API_KEY = os.environ.get("API_KEY", "change-this-secret-key")

SUPPORTED_MARKETPLACES = ["com", "co.uk", "de", "fr", "it", "es", "ca", "com.mx"]


class ScrapeRequest(BaseModel):
    seller_ids: list[str]
    marketplace: str = "com"   # Default: Amazon US


class ScrapeResponse(BaseModel):
    status: str
    total: int
    successful: int
    failed: int
    results: list[dict]


@app.post("/scrape", response_model=ScrapeResponse)
async def scrape_sellers(
    request: ScrapeRequest,
    x_api_key: str = Header(...)  # n8n sends this in headers
):
    # Validate API key
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")

    # Validate inputs
    if not request.seller_ids:
        raise HTTPException(status_code=400, detail="seller_ids cannot be empty")

    if len(request.seller_ids) > 50:
        raise HTTPException(
            status_code=400,
            detail="Max 50 sellers per request. Split into batches in n8n."
        )

    if request.marketplace not in SUPPORTED_MARKETPLACES:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported marketplace. Use one of: {SUPPORTED_MARKETPLACES}"
        )

    # Run the scraper
    results = await scrape_sellers_batch(request.seller_ids, request.marketplace)

    successful = sum(1 for r in results if r.get("status") == "success")
    failed = len(results) - successful

    return ScrapeResponse(
        status="complete",
        total=len(results),
        successful=successful,
        failed=failed,
        results=results
    )


@app.get("/health")
def health_check():
    """n8n can ping this to check if service is running."""
    return {"status": "ok", "service": "amazon-seller-scraper"}


@app.get("/")
def root():
    return {"message": "Amazon Seller Scraper API is running"}


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
```

---

## 🐳 Dockerfile

```dockerfile
FROM python:3.11-slim

WORKDIR /app

# Install system dependencies for Playwright/Chromium
RUN apt-get update && apt-get install -y \
    wget gnupg ca-certificates \
    libglib2.0-0 libnss3 libnspr4 libatk1.0-0 \
    libatk-bridge2.0-0 libcups2 libdrm2 libdbus-1-3 \
    libxcb1 libxkbcommon0 libx11-6 libxcomposite1 \
    libxdamage1 libxext6 libxfixes3 libxrandr2 \
    libgbm1 libpango-1.0-0 libcairo2 libasound2 \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright's Chromium browser
RUN playwright install chromium

# Copy all source files
COPY . .

EXPOSE 8080

CMD ["python", "main.py"]
```

---

## ☁️ Deploy to Google Cloud Run

### Step 1 — Install & Setup Google Cloud CLI
```bash
curl https://sdk.cloud.google.com | bash
exec -l $SHELL
gcloud init
gcloud auth login
```

### Step 2 — Set your project
```bash
export PROJECT_ID=your-gcp-project-id
gcloud config set project $PROJECT_ID
gcloud services enable run.googleapis.com containerregistry.googleapis.com
```

### Step 3 — Build & Push Docker Image
```bash
docker build -t gcr.io/$PROJECT_ID/amazon-seller-scraper .
docker push gcr.io/$PROJECT_ID/amazon-seller-scraper
```

### Step 4 — Deploy
```bash
gcloud run deploy amazon-seller-scraper \
  --image gcr.io/$PROJECT_ID/amazon-seller-scraper \
  --platform managed \
  --region us-central1 \
  --memory 2Gi \
  --cpu 1 \
  --timeout 300 \
  --concurrency 1 \
  --set-env-vars API_KEY=your-secret-key-here \
  --allow-unauthenticated
```

After deploy, you'll get a URL like:
```
https://amazon-seller-scraper-abc123-uc.a.run.app
```

---

## 🔁 n8n Integration

### How to Set Up in n8n

Use an **HTTP Request** node in n8n to call your scraper.

#### n8n HTTP Request Node Settings:

| Field | Value |
|---|---|
| **Method** | POST |
| **URL** | `https://your-cloud-run-url.a.run.app/scrape` |
| **Authentication** | None (handled via header below) |
| **Headers** | `x-api-key: your-secret-key-here` |
| **Body Type** | JSON |

#### n8n Request Body:
```json
{
  "seller_ids": ["A1B2C3XYZ", "D4E5F6ABC", "G7H8I9JKL"],
  "marketplace": "com"
}
```

#### n8n Response (what you'll get back):
```json
{
  "status": "complete",
  "total": 3,
  "successful": 3,
  "failed": 0,
  "results": [
    {
      "seller_id": "A1B2C3XYZ",
      "status": "success",
      "business_name": "Acme Ltd",
      "address": "123 Business St, London, UK",
      "vat_number": "GB123456789",
      "phone": "+44 20 1234 5678",
      "email": "contact@acme.com",
      "rating": "4.5 out of 5 stars",
      "marketplace": "co.uk"
    }
  ]
}
```

---

### n8n Workflow for Bulk 5000 Sellers

Since the API accepts max 50 sellers per request, use this n8n flow to handle bulk scraping:

```
[Start / Webhook / Schedule]
        ↓
[Code Node: Split seller IDs into batches of 20]
        ↓
[Loop Over Items]
        ↓
[HTTP Request Node: POST /scrape]
        ↓
[Wait Node: 60 seconds]  ← important! gives proxies a rest
        ↓
[Merge results]
        ↓
[Save to Google Sheets / Airtable / Database]
```

#### n8n Code Node — Batch Splitting:
```javascript
// Split a large list into batches of 20
const allSellerIds = $input.first().json.seller_ids;
const batchSize = 20;
const batches = [];

for (let i = 0; i < allSellerIds.length; i += batchSize) {
  batches.push({
    seller_ids: allSellerIds.slice(i, i + batchSize),
    marketplace: "com"
  });
}

return batches.map(batch => ({ json: batch }));
```

---

## 🧪 Testing Locally Before Deploying

```bash
# Install dependencies locally
pip install -r requirements.txt
playwright install chromium

# Run the API locally
python main.py

# In another terminal — test it
curl -X POST "http://localhost:8080/scrape" \
  -H "Content-Type: application/json" \
  -H "x-api-key: your-secret-key-here" \
  -d '{"seller_ids": ["A1B2C3XYZ"], "marketplace": "com"}'
```

---

## ⚠️ Important Notes

### Proxy Credentials Security
- Your proxy credentials are hardcoded in `proxy_manager.py` for simplicity
- For production, move them to `.env` and load via `os.environ`
- **Never commit your proxy credentials to GitHub**

### CAPTCHA Handling
- If a seller returns `"status": "captcha"`, n8n should retry that seller after 30+ minutes
- High CAPTCHA rate = increase delays in `human_behaviour.py` (switch to "paranoid" profile)

### EU vs US Marketplaces
- For **email and VAT numbers**, use EU marketplaces: `co.uk`, `de`, `fr`, `it`, `es`
- US (`com`) seller pages rarely show email addresses

### Rate Limiting for 5000 Sellers/Month
- 167 sellers/day max
- Spread across the day using n8n Schedule Trigger
- Recommended: run 3 batches of ~56 sellers at 8am, 1pm, 8pm

### Proxy Usage at 5000/month
- ~1–2 GB bandwidth total
- Your 10 Webshare free proxies are sufficient
- Monitor usage at: https://proxy.webshare.io/dashboard

---

## 📞 API Endpoints Reference

| Endpoint | Method | Description |
|---|---|---|
| `/scrape` | POST | Scrape seller data (n8n calls this) |
| `/health` | GET | Health check for n8n monitoring |
| `/` | GET | Service info |
