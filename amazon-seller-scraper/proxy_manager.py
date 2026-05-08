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

    sorted_indices = sorted(_usage_count.keys(), key=lambda i: _usage_count[i])
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
