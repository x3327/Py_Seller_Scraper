# main.py

import os
import sys
import io

# Force UTF-8 stdout so emoji in print() don't crash on Windows cp1252
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace', line_buffering=True)

import asyncio
import logging
import uuid
from datetime import datetime
from fastapi import FastAPI, HTTPException, Header
from pydantic import BaseModel
from dotenv import load_dotenv

from scraper import scrape_sellers_batch, scrape_asins_batch, scrape_sellers_from_asins_batch

logger = logging.getLogger("uvicorn.error")

load_dotenv()

app = FastAPI(title="Amazon Seller Scraper API")

# One scrape at a time — each Chromium context uses ~300MB on Railway's container.
_scrape_lock = asyncio.Semaphore(1)

# Per-scrape timeout — must finish before Railway's nginx 300s hard limit.
# 240s gives 60s of headroom for cold starts + proxy setup overhead.
_REQUEST_TIMEOUT_SECS = 240

API_KEY = os.environ.get("API_KEY", "change-this-secret-key")

SUPPORTED_MARKETPLACES = ["com", "co.uk", "de", "fr", "it", "es", "ca", "com.mx"]

# ─────────────────────────────────────────
# Async job store
# Jobs are kept in memory; lost on container restart (n8n poll returns 404 → error row).
# ─────────────────────────────────────────
_jobs: dict[str, dict] = {}


# ─────────────────────────────────────────
# Request / Response Models
# ─────────────────────────────────────────

class ScrapeRequest(BaseModel):
    seller_ids: list[str]
    marketplace: str = "com"


class CategoryUrlItem(BaseModel):
    url: str
    category_name: str = ""
    category_path: str = ""


class GetAsinsRequest(BaseModel):
    category_urls: list[CategoryUrlItem]
    marketplace: str = "es"
    max_items: int = 0


class AsinItem(BaseModel):
    asin: str
    category: str = ""
    category_path: str = ""


class ScrapeFromAsinsRequest(BaseModel):
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


class JobStartedResponse(BaseModel):
    job_id: str
    status: str


# ─────────────────────────────────────────
# Auth helpers
# ─────────────────────────────────────────

def check_api_key(key: str):
    if key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")


def check_marketplace(mp: str):
    if mp not in SUPPORTED_MARKETPLACES:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported marketplace '{mp}'. Valid: {SUPPORTED_MARKETPLACES}"
        )


# ─────────────────────────────────────────
# Background job runners
# ─────────────────────────────────────────

async def _bg_scrape_from_asins(job_id: str, asin_list: list[dict], marketplace: str):
    """Background task: scrape sellers from ASINs, update job store when done."""
    async with _scrape_lock:
        try:
            results = await asyncio.wait_for(
                scrape_sellers_from_asins_batch(asin_list, marketplace),
                timeout=_REQUEST_TIMEOUT_SECS,
            )
            successful = sum(1 for r in results if r.get("status") == "success")
            _jobs[job_id].update({
                "status": "complete",
                "total": len(results),
                "successful": successful,
                "failed": len(results) - successful,
                "results": results,
                "completed_at": datetime.utcnow().isoformat(),
            })
        except asyncio.TimeoutError:
            logger.error("job %s timed out after %ss", job_id, _REQUEST_TIMEOUT_SECS)
            _jobs[job_id].update({
                "status": "error",
                "error": f"Scraping timed out after {_REQUEST_TIMEOUT_SECS}s",
                "results": [],
                "total": 0, "successful": 0, "failed": 0,
                "completed_at": datetime.utcnow().isoformat(),
            })
        except Exception as e:
            logger.error("job %s failed: %s", job_id, e)
            _jobs[job_id].update({
                "status": "error",
                "error": str(e),
                "results": [],
                "total": 0, "successful": 0, "failed": 0,
                "completed_at": datetime.utcnow().isoformat(),
            })


async def _bg_get_asins(job_id: str, category_urls: list[dict], marketplace: str, max_items: int):
    """Background task: scrape ASINs from category URLs, update job store when done."""
    async with _scrape_lock:
        try:
            asins = await asyncio.wait_for(
                scrape_asins_batch(
                    category_urls=category_urls,
                    marketplace=marketplace,
                    max_items=max_items,
                ),
                timeout=_REQUEST_TIMEOUT_SECS,
            )
            _jobs[job_id].update({
                "status": "complete",
                "total": len(asins),
                "asins": asins,
                "completed_at": datetime.utcnow().isoformat(),
            })
        except asyncio.TimeoutError:
            logger.error("get-asins job %s timed out after %ss", job_id, _REQUEST_TIMEOUT_SECS)
            _jobs[job_id].update({
                "status": "error",
                "error": f"ASIN scraping timed out after {_REQUEST_TIMEOUT_SECS}s",
                "asins": [],
                "total": 0,
                "completed_at": datetime.utcnow().isoformat(),
            })
        except Exception as e:
            logger.error("get-asins job %s failed: %s", job_id, e)
            _jobs[job_id].update({
                "status": "error",
                "error": str(e),
                "asins": [],
                "total": 0,
                "completed_at": datetime.utcnow().isoformat(),
            })


# ─────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────

@app.post("/scrape", response_model=ScrapeResponse)
async def scrape_sellers(
    request: ScrapeRequest,
    x_api_key: str = Header(...)
):
    """Scrape Amazon seller info pages directly by seller ID. Max 50 per request."""
    check_api_key(x_api_key)
    check_marketplace(request.marketplace)

    if not request.seller_ids:
        raise HTTPException(status_code=400, detail="seller_ids cannot be empty")
    if len(request.seller_ids) > 50:
        raise HTTPException(status_code=400, detail="Max 50 sellers per request")

    async with _scrape_lock:
        try:
            results = await asyncio.wait_for(
                scrape_sellers_batch(request.seller_ids, request.marketplace),
                timeout=_REQUEST_TIMEOUT_SECS,
            )
        except asyncio.TimeoutError:
            raise HTTPException(status_code=504, detail=f"Scraping timed out after {_REQUEST_TIMEOUT_SECS}s")

    successful = sum(1 for r in results if r.get("status") == "success")
    return ScrapeResponse(
        status="complete",
        total=len(results),
        successful=successful,
        failed=len(results) - successful,
        results=results,
    )


@app.post("/get-asins", response_model=JobStartedResponse)
async def get_asins(
    request: GetAsinsRequest,
    x_api_key: str = Header(...)
):
    """
    Start an async job to scrape ASINs from Amazon category/search URLs.
    Returns immediately with a job_id. Poll GET /job/{job_id} for results.
    """
    check_api_key(x_api_key)
    check_marketplace(request.marketplace)

    if not request.category_urls:
        raise HTTPException(status_code=400, detail="category_urls cannot be empty")
    if len(request.category_urls) > 100:
        raise HTTPException(status_code=400, detail="Max 100 category URLs per request")

    category_urls = [
        {"url": c.url, "category_name": c.category_name, "category_path": c.category_path}
        for c in request.category_urls
    ]

    job_id = str(uuid.uuid4())
    _jobs[job_id] = {
        "job_id": job_id,
        "type": "get_asins",
        "status": "running",
        "created_at": datetime.utcnow().isoformat(),
        "marketplace": request.marketplace,
    }

    asyncio.create_task(_bg_get_asins(job_id, category_urls, request.marketplace, request.max_items))

    return JobStartedResponse(job_id=job_id, status="running")


@app.post("/scrape-from-asins", response_model=JobStartedResponse)
async def scrape_from_asins(
    request: ScrapeFromAsinsRequest,
    x_api_key: str = Header(...)
):
    """
    Start an async job to scrape seller info from ASINs.
    Returns immediately with a job_id. Poll GET /job/{job_id} for results.

    For each ASIN:
      1. Navigates the product page to find the seller ID
      2. Navigates the seller info page to extract business details
    """
    check_api_key(x_api_key)
    check_marketplace(request.marketplace)

    if not request.asins:
        raise HTTPException(status_code=400, detail="asins cannot be empty")
    if len(request.asins) > 50:
        raise HTTPException(status_code=400, detail="Max 50 ASINs per request")

    asin_list = [
        {"asin": a.asin, "category": a.category, "category_path": a.category_path}
        for a in request.asins
    ]

    job_id = str(uuid.uuid4())
    _jobs[job_id] = {
        "job_id": job_id,
        "type": "scrape_from_asins",
        "status": "running",
        "created_at": datetime.utcnow().isoformat(),
        "marketplace": request.marketplace,
    }

    asyncio.create_task(_bg_scrape_from_asins(job_id, asin_list, request.marketplace))

    return JobStartedResponse(job_id=job_id, status="running")


@app.get("/job/{job_id}")
async def get_job_status(job_id: str, x_api_key: str = Header(...)):
    """
    Poll the status of a background scrape job.
    Returns job data including results when status == 'complete'.
    Returns 404 if the job_id is unknown (server may have restarted).
    """
    check_api_key(x_api_key)
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found or expired (server may have restarted)")
    return job


@app.get("/health")
def health_check():
    return {"status": "ok", "service": "amazon-seller-scraper", "active_jobs": len(_jobs)}


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
        timeout_keep_alive=75,
        timeout_graceful_shutdown=30,
    )
