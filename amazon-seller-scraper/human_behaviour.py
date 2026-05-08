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
    start_x = random.randint(100, 400)
    start_y = random.randint(100, 400)
    end_x = random.randint(500, 1200)
    end_y = random.randint(200, 700)

    steps = random.randint(10, 20)
    for i in range(steps):
        x = start_x + (end_x - start_x) * i / steps + random.randint(-5, 5)
        y = start_y + (end_y - start_y) * i / steps + random.randint(-5, 5)
        await page.mouse.move(x, y)
        await asyncio.sleep(random.uniform(0.01, 0.05))


async def simulate_scroll(page: Page):
    """
    Scroll down the page naturally, as a human would when reading content.
    """
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
    """Pause as if reading the page content."""
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
        blocked_types = ["image", "media", "font"]   # keep stylesheet — Amazon needs it for buybox
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
