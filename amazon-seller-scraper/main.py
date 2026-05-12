# main.py

import os
import sys
import io

# Force UTF-8 stdout so emoji in print() don't crash on Windows cp1252
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace', line_buffering=True)

import asyncio
from fastapi import FastAPI, HTTPException, Header
from pydantic import BaseModel
from dotenv import load_dotenv

from scraper import scrape_sellers_batch, scrape_asins_batch, scrape_sellers_from_asins_batch

load_dotenv()

app = FastAPI(title="Amazon Seller Scraper API")

# Allow 2 concurrent batch requests — each batch already runs _ASIN_CONCURRENCY ASINs
# in parallel internally. 2 here covers concurrent users or back-to-back batch requests.
_scrape_lock = asyncio.Semaphore(2)

API_KEY = os.environ.get("API_KEY", "change-this-secret-key")

SUPPORTED_MARKETPLACES = ["com", "co.uk", "de", "fr", "it", "es", "ca", "com.mx"]


# ─────────────────────────────────────────
# Request / Response Models
# ─────────────────────────────────────────

class ScrapeRequest(BaseModel):
    """Scrape seller info directly by seller ID."""
    seller_ids: list[str]
    marketplace: str = "com"


class CategoryUrlItem(BaseModel):
    url: str
    category_name: str = ""
    category_path: str = ""


class GetAsinsRequest(BaseModel):
    """Scrape ASINs from Amazon category/search URLs."""
    category_urls: list[CategoryUrlItem]
    marketplace: str = "es"
    max_items: int = 0   # 0 = no limit


class AsinItem(BaseModel):
    asin: str
    category: str = ""
    category_path: str = ""


class ScrapeFromAsinsRequest(BaseModel):
    """Scrape seller info from ASINs (navigates product page → seller page)."""
    asins: list[AsinItem]
    marketplace: str = "es"


class ScrapeResponse(BaseModel):
    status: str
    total: int
    successful: int
    failed: int
    results: list[dict]


class GetAsinsResponse(BaseModel):
    status: str
    total: int
    asins: list[dict]


# ─────────────────────────────────────────
# Auth helper
# ─────────────────────────────────────────

def check_api_key(key: str):
    if key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")


def check_marketplace(mp: str):
    if mp not in SUPPORTED_MARKETPLACES:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported marketplace '{mp}'. Valid options: {SUPPORTED_MARKETPLACES}"
        )


# ─────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────

@app.post("/scrape", response_model=ScrapeResponse)
async def scrape_sellers(
    request: ScrapeRequest,
    x_api_key: str = Header(...)
):
    """
    Scrape Amazon seller info pages directly by seller ID.
    Used when you already know the seller IDs.
    Max 50 per request.
    """
    check_api_key(x_api_key)
    check_marketplace(request.marketplace)

    if not request.seller_ids:
        raise HTTPException(status_code=400, detail="seller_ids cannot be empty")
    if len(request.seller_ids) > 50:
        raise HTTPException(status_code=400, detail="Max 50 sellers per request")

    async with _scrape_lock:
        results = await scrape_sellers_batch(request.seller_ids, request.marketplace)
    successful = sum(1 for r in results if r.get("status") == "success")

    return ScrapeResponse(
        status="complete",
        total=len(results),
        successful=successful,
        failed=len(results) - successful,
        results=results
    )


@app.post("/get-asins", response_model=GetAsinsResponse)
async def get_asins(
    request: GetAsinsRequest,
    x_api_key: str = Header(...)
):
    """
    Scrape product ASINs from Amazon category or search result pages.
    Used by n8n to collect ASINs before seller scraping.

    category_urls: list of Amazon search/category URLs with optional metadata.
    max_items: cap on total ASINs returned (0 = no limit).
    """
    check_api_key(x_api_key)
    check_marketplace(request.marketplace)

    if not request.category_urls:
        raise HTTPException(status_code=400, detail="category_urls cannot be empty")
    if len(request.category_urls) > 100:
        raise HTTPException(status_code=400, detail="Max 100 category URLs per request")

    category_urls = [
        {
            "url": c.url,
            "category_name": c.category_name,
            "category_path": c.category_path,
        }
        for c in request.category_urls
    ]

    async with _scrape_lock:
        asins = await scrape_asins_batch(
            category_urls=category_urls,
            marketplace=request.marketplace,
            max_items=request.max_items,
        )

    return GetAsinsResponse(
        status="complete",
        total=len(asins),
        asins=asins,
    )


@app.post("/scrape-from-asins", response_model=ScrapeResponse)
async def scrape_from_asins(
    request: ScrapeFromAsinsRequest,
    x_api_key: str = Header(...)
):
    """
    Scrape seller info from a list of ASINs.

    For each ASIN:
      1. Navigates the product page to find the seller ID
      2. Navigates the seller info page to extract business details

    Returns: business name, address, email, phone, VAT number per seller.
    Max 50 ASINs per request (use n8n loop for larger batches).
    """
    check_api_key(x_api_key)
    check_marketplace(request.marketplace)

    if not request.asins:
        raise HTTPException(status_code=400, detail="asins cannot be empty")
    if len(request.asins) > 50:
        raise HTTPException(status_code=400, detail="Max 50 ASINs per request")

    asin_list = [
        {
            "asin": a.asin,
            "category": a.category,
            "category_path": a.category_path,
        }
        for a in request.asins
    ]

    async with _scrape_lock:
        results = await scrape_sellers_from_asins_batch(asin_list, request.marketplace)
    successful = sum(1 for r in results if r.get("status") == "success")

    return ScrapeResponse(
        status="complete",
        total=len(results),
        successful=successful,
        failed=len(results) - successful,
        results=results,
    )


@app.get("/health")
def health_check():
    """n8n can ping this to verify the service is up."""
    return {"status": "ok", "service": "amazon-seller-scraper"}


@app.get("/")
def root():
    return {"message": "Amazon Seller Scraper API is running"}


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=port,
        timeout_keep_alive=75,   # keep TCP alive during long scrapes (default 5s causes "fetch failed")
        timeout_graceful_shutdown=30,
    )
