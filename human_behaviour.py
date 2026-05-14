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


def _cubic_bezier(t: float, p0: float, p1: float, p2: float, p3: float) -> float:
    """Evaluate a 1-D cubic Bezier at parameter t ∈ [0, 1]."""
    u = 1 - t
    return u**3 * p0 + 3 * u**2 * t * p1 + 3 * u * t**2 * p2 + t**3 * p3


async def simulate_mouse_movement(page: Page):
    """
    Move mouse along a cubic Bezier path with two independent control points.
    Cubic Bezier produces more complex, natural-looking curves than quadratic.
    Includes micro-tremor jitter and occasional hesitation pauses.
    """
    sx = random.randint(200, 700)
    sy = random.randint(150, 500)
    ex = random.randint(350, 1100)
    ey = random.randint(250, 650)

    # Two control points — offset from the straight line for a natural arc
    c1x = sx + (ex - sx) * random.uniform(0.2, 0.4) + random.randint(-120, 120)
    c1y = sy + (ey - sy) * random.uniform(0.1, 0.3) + random.randint(-80, 80)
    c2x = sx + (ex - sx) * random.uniform(0.6, 0.8) + random.randint(-120, 120)
    c2y = sy + (ey - sy) * random.uniform(0.7, 0.9) + random.randint(-80, 80)

    steps = random.randint(16, 28)
    for i in range(steps + 1):
        # Ease-in-out: accelerate then decelerate
        raw_t = i / steps
        t = raw_t * raw_t * (3 - 2 * raw_t)  # smoothstep

        x = int(_cubic_bezier(t, sx, c1x, c2x, ex))
        y = int(_cubic_bezier(t, sy, c1y, c2y, ey))

        # Micro-tremor: human hand is never perfectly steady
        x += random.randint(-2, 2)
        y += random.randint(-2, 2)

        await page.mouse.move(x, y)

        # Vary inter-step speed: faster in the middle, slower at start/end
        speed = 0.006 + 0.03 * (1 - abs(raw_t - 0.5) * 2)
        await asyncio.sleep(speed + random.uniform(0, 0.015))

        # Random hesitation pause mid-path (simulates tracking something on screen)
        if random.random() < 0.06:
            await asyncio.sleep(random.uniform(0.08, 0.25))


async def simulate_scroll(page: Page, amount: int = 0):
    """
    Scroll down with momentum easing — fast burst then deceleration.
    Mimics mouse-wheel inertia: humans rarely scroll in perfectly equal steps.
    """
    total_scroll = amount if amount > 0 else random.randint(300, 700)

    # Split total into a burst phase (60–70%) and a deceleration phase
    burst_ratio = random.uniform(0.55, 0.72)
    burst_total = int(total_scroll * burst_ratio)
    decel_total = total_scroll - burst_total

    # Burst: 2-3 large fast chunks
    burst_chunks = random.randint(2, 3)
    for i in range(burst_chunks):
        delta = burst_total // burst_chunks + random.randint(-15, 15)
        await page.evaluate(f"window.scrollBy(0, {delta})")
        await asyncio.sleep(random.uniform(0.08, 0.18))

    # Deceleration: 3-5 smaller slower chunks
    decel_chunks = random.randint(3, 5)
    for i in range(decel_chunks):
        # Chunks shrink as we decelerate
        weight = (decel_chunks - i) / sum(range(1, decel_chunks + 1))
        delta = int(decel_total * weight) + random.randint(-8, 8)
        await page.evaluate(f"window.scrollBy(0, {max(5, delta)})")
        await asyncio.sleep(random.uniform(0.15, 0.45))

    # 35% chance to scroll back up slightly (natural reading behaviour)
    if random.random() < 0.35:
        await page.evaluate(f"window.scrollBy(0, -{random.randint(30, 110)})")
        await asyncio.sleep(random.uniform(0.12, 0.35))


async def simulate_reading_pause(page: Page):
    """Pause as if reading the page content."""
    await asyncio.sleep(random.uniform(0.8, 2.5))


async def simulate_micro_movements(page: Page, duration_s: float = 1.2):
    """
    Tiny random mouse drifts during a reading pause.
    Real users fidget — the mouse moves slightly even while they're reading.
    """
    end = asyncio.get_event_loop().time() + duration_s
    x = random.randint(300, 800)
    y = random.randint(200, 500)
    while asyncio.get_event_loop().time() < end:
        x += random.randint(-6, 6)
        y += random.randint(-4, 4)
        x = max(100, min(1200, x))
        y = max(100, min(700, y))
        await page.mouse.move(x, y)
        await asyncio.sleep(random.uniform(0.12, 0.35))


async def simulate_page_entry(page: Page):
    """
    Human-behaviour sequence for product pages.
    Total time: ~4-8 seconds (retains realism while staying fast enough).
    """
    await micro_delay()
    await simulate_mouse_movement(page)
    await random_delay("normal")                           # 0.8-2s — eyes scan
    await simulate_scroll(page)
    await simulate_micro_movements(page, duration_s=random.uniform(0.8, 1.8))


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
