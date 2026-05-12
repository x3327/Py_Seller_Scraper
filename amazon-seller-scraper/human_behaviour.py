# human_behaviour.py

import asyncio
import random
from playwright.async_api import Page


# ─────────────────────────────────────────
# Delay Profiles
# ─────────────────────────────────────────
DELAY_PROFILES = {
    "fast":     {"min": 300,   "max": 800},
    "normal":   {"min": 800,   "max": 2000},
    "cautious": {"min": 1500,  "max": 3500},   # tightened for speed
    "paranoid": {"min": 4000,  "max": 8000},
}

ACTIVE_PROFILE = "cautious"


async def random_delay(profile: str = ACTIVE_PROFILE):
    """Wait a random human-like amount of time."""
    p = DELAY_PROFILES[profile]
    delay_ms = random.randint(p["min"], p["max"])
    await asyncio.sleep(delay_ms / 1000)


async def micro_delay():
    """Tiny pause — simulates human reaction time."""
    await asyncio.sleep(random.uniform(0.1, 0.4))


async def simulate_mouse_movement(page: Page):
    """
    Move mouse in a Bezier-curve-like path (more natural than linear).
    Humans never move the mouse in a straight line.
    """
    # Pick random start and end points in typical reading zone
    sx = random.randint(200, 600)
    sy = random.randint(150, 450)
    ex = random.randint(400, 1100)
    ey = random.randint(300, 650)

    # Control points for a slight curve
    cx = (sx + ex) // 2 + random.randint(-80, 80)
    cy = (sy + ey) // 2 + random.randint(-60, 60)

    steps = random.randint(12, 22)
    for i in range(steps + 1):
        t = i / steps
        # Quadratic Bezier interpolation
        x = int((1 - t) ** 2 * sx + 2 * (1 - t) * t * cx + t ** 2 * ex)
        y = int((1 - t) ** 2 * sy + 2 * (1 - t) * t * cy + t ** 2 * ey)
        # Add micro-jitter for realism
        x += random.randint(-3, 3)
        y += random.randint(-3, 3)
        await page.mouse.move(x, y)
        await asyncio.sleep(random.uniform(0.008, 0.04))

    # Occasionally pause briefly mid-path (human hesitation)
    if random.random() < 0.25:
        await asyncio.sleep(random.uniform(0.1, 0.35))


async def simulate_scroll(page: Page, amount: int = 0):
    """
    Scroll down the page naturally, as a human would when reading content.
    amount=0 → random scroll (300-700px), otherwise scroll that exact amount.
    """
    total_scroll = amount if amount > 0 else random.randint(300, 700)
    chunks = random.randint(3, 5)
    scroll_per_chunk = total_scroll // chunks

    for chunk in range(chunks):
        # Vary speed between chunks (humans scroll unevenly)
        jitter = random.randint(-20, 20)
        await page.evaluate(f"window.scrollBy(0, {scroll_per_chunk + jitter})")
        await asyncio.sleep(random.uniform(0.25, 0.75))

    # 30% chance to scroll back up slightly (natural reading behaviour)
    if random.random() < 0.30:
        await page.evaluate(f"window.scrollBy(0, -{random.randint(40, 120)})")
        await asyncio.sleep(random.uniform(0.15, 0.40))


async def simulate_reading_pause(page: Page):
    """Pause as if reading the page content."""
    await asyncio.sleep(random.uniform(0.8, 2.5))


async def simulate_page_entry(page: Page):
    """
    Human-behaviour sequence for product pages.
    Total time: ~3-7 seconds (optimised for speed while retaining realism).
    """
    await micro_delay()
    await simulate_mouse_movement(page)
    await random_delay("normal")         # 0.8-2s — eyes scan
    await simulate_scroll(page)
    await simulate_reading_pause(page)   # 0.8-2.5s


async def simulate_page_entry_fast(page: Page):
    """
    Lightweight behaviour for seller info pages.
    Total time: ~0.8-2 seconds.
    """
    await micro_delay()
    await simulate_scroll(page, amount=random.randint(150, 400))
    await asyncio.sleep(random.uniform(0.6, 1.5))


async def block_unnecessary_resources(page: Page):
    """
    Block images, fonts, and media to speed up page loads.
    Keep scripts and stylesheets — Amazon needs them for buybox rendering.
    Block known analytics/ad domains to reduce noise.
    """
    BLOCKED_TYPES = {"image", "media", "font"}
    BLOCKED_DOMAINS = {
        "google-analytics.com", "doubleclick.net",
        "amazon-adsystem.com", "scorecardresearch.com",
        "fls-na.amazon.com", "s.amazon-adsystem.com",
        "pixel.advertising.com", "ib.anycast.adnxs.com",
    }

    async def handle_route(route):
        req = route.request
        if req.resource_type in BLOCKED_TYPES:
            await route.abort()
        elif any(d in req.url for d in BLOCKED_DOMAINS):
            await route.abort()
        else:
            await route.continue_()

    await page.route("**/*", handle_route)
