# proxy_manager.py
# Webshare residential proxies — 20,000 threads
# Format: p.webshare.io:80:edonqeko-{N}:kxvif6tazp3e  (N = 1..20000)

import random
import time

# ─────────────────────────────────────────
# Config
# ─────────────────────────────────────────
PROXY_HOST     = "p.webshare.io"
PROXY_PORT     = 80
PROXY_PASSWORD = "kxvif6tazp3e"
PROXY_USER_PREFIX = "edonqeko"
PROXY_COUNT    = 20000   # total threads available

# Cooldown: skip a thread for this many seconds after it gets blocked
PROXY_COOLDOWN_SECS = 300

# ─────────────────────────────────────────
# Failed-thread tracking
# ─────────────────────────────────────────
_failed_at: dict[int, float] = {}   # thread_index -> unix timestamp of failure
_last_used:  int = -1


def _thread_username(n: int) -> str:
    """Return Webshare username for thread N (1-based)."""
    return f"{PROXY_USER_PREFIX}-{n}"


def get_proxy() -> dict:
    """
    Pick a random Webshare thread.
    - Avoids the last-used thread (reduce fingerprint correlation).
    - Avoids threads that recently triggered a block (cooldown window).
    - Falls back to any thread if all are in cooldown.
    """
    global _last_used

    now = time.time()

    # Build a candidate pool: not failed-recently, not the very last used
    def is_available(n: int) -> bool:
        return (
            n != _last_used
            and now - _failed_at.get(n, 0) > PROXY_COOLDOWN_SECS
        )

    # Sample 100 random threads to check (cheap; avoids scanning all 20k)
    sample = random.sample(range(1, PROXY_COUNT + 1), min(100, PROXY_COUNT))
    candidates = [n for n in sample if is_available(n)]

    if not candidates:
        # All sampled threads in cooldown — just pick any random thread
        candidates = [n for n in sample if n != _last_used] or sample

    chosen = random.choice(candidates)
    _last_used = chosen

    return {
        "server":   f"http://{PROXY_HOST}:{PROXY_PORT}",
        "username": _thread_username(chosen),
        "password": PROXY_PASSWORD,
        "_thread":  chosen,   # carried for mark_proxy_failed
    }


def mark_proxy_failed(proxy_dict: dict) -> None:
    """Call when a proxy returns a bot-detection / blocked page."""
    thread = proxy_dict.get("_thread")
    if thread is not None:
        _failed_at[thread] = time.time()
        print(f"  [PROXY] Thread #{thread} marked failed — cooldown {PROXY_COOLDOWN_SECS}s")


def get_proxy_for_playwright() -> dict:
    """Returns proxy formatted for Playwright browser context."""
    proxy = get_proxy()
    return {
        "server":   proxy["server"],
        "username": proxy["username"],
        "password": proxy["password"],
    }


def reset_usage_counts() -> None:
    """No-op kept for API compatibility."""
    pass
