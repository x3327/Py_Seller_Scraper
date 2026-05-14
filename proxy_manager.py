# proxy_manager.py

import random
import time
from pathlib import Path

# ─────────────────────────────────────────
# PRIMARY proxies — Webshare rotating residential
# Single endpoint p.webshare.io:80; each username suffix is a different
# residential exit IP (2500 slots total, edonqeko-1 … edonqeko-2500).
# ─────────────────────────────────────────
_WEBSHARE_HOST     = "p.webshare.io"
_WEBSHARE_PORT     = 80
_WEBSHARE_USER     = "edonqeko"
_WEBSHARE_PASS     = "kxvif6tazp3e"
_WEBSHARE_SLOTS    = 2500   # total proxy slots purchased

def _make_primary(slot: int) -> dict:
    return {
        "server":   f"http://{_WEBSHARE_HOST}:{_WEBSHARE_PORT}",
        "username": f"{_WEBSHARE_USER}-{slot}",
        "password": _WEBSHARE_PASS,
    }

# Pre-build the full list once at import time (lightweight — just dicts)
PRIMARY_PROXIES = [_make_primary(i) for i in range(1, _WEBSHARE_SLOTS + 1)]

# ─────────────────────────────────────────
# BACKUP proxies — loaded from proxies_backup.txt (IP:PORT, no credentials)
# Used only when all primary proxies are on cooldown (unlikely with 2500 slots).
# ─────────────────────────────────────────
_BACKUP_FILE = Path(__file__).parent / "proxies_backup.txt"


def _load_backup_proxies() -> list[dict]:
    if not _BACKUP_FILE.exists():
        return []
    proxies = []
    for line in _BACKUP_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or not line.startswith(("http://", "socks5://")):
            continue
        proxies.append({"server": line, "username": None, "password": None})
    return proxies


_BACKUP_PROXIES: list[dict] = []   # lazy-loaded on first need


def _get_backup_proxies() -> list[dict]:
    global _BACKUP_PROXIES
    if not _BACKUP_PROXIES:
        _BACKUP_PROXIES = _load_backup_proxies()
        if _BACKUP_PROXIES:
            print(f"  [PROXY] Loaded {len(_BACKUP_PROXIES)} backup proxies from {_BACKUP_FILE.name}")
    return _BACKUP_PROXIES


# ─────────────────────────────────────────
# Shared rotation state
# ─────────────────────────────────────────

_usage:     dict[tuple[str, int], int]   = {}
_failed_at: dict[tuple[str, int], float] = {}
_last_used: tuple[str, int] | None = None

PROXY_COOLDOWN_SECS = 300   # 5 min cooldown after a proxy fails


def mark_proxy_failed(proxy_dict: dict) -> None:
    """Mark a proxy slot as failed so it enters cooldown."""
    server   = proxy_dict.get("server", "")
    username = proxy_dict.get("username", "")

    # Primary proxies are identified by username suffix
    if username.startswith(_WEBSHARE_USER + "-"):
        try:
            slot = int(username.split("-")[-1])
            key  = ("primary", slot - 1)   # 0-indexed
            _failed_at[key] = time.time()
            print(f"  [PROXY] primary slot {slot} ({username}) marked failed — cooldown {PROXY_COOLDOWN_SECS}s")
            return
        except ValueError:
            pass

    # Backup proxies identified by server URL
    for i, p in enumerate(_get_backup_proxies()):
        if p["server"] == server:
            _failed_at[("backup", i)] = time.time()
            print(f"  [PROXY] backup[{i}] {server} marked failed — cooldown {PROXY_COOLDOWN_SECS}s")
            return


def get_proxy() -> dict:
    """
    Return the best available proxy using weighted rotation.

    With 2500 primary residential slots the backup pool is almost never needed.
    Strategy within each pool:
      - Skip slots on cooldown
      - Skip the last-used slot
      - Prefer least-used slot
      - Pick randomly from the top 8 candidates (more spread with larger pool)
    """
    global _last_used

    now = time.time()

    def _pick(pool_name: str, pool: list[dict]) -> dict | None:
        global _last_used
        available = [
            i for i in range(len(pool))
            if now - _failed_at.get((pool_name, i), 0) > PROXY_COOLDOWN_SECS
            and (pool_name, i) != _last_used
        ]
        if not available:
            available = [i for i in range(len(pool)) if (pool_name, i) != _last_used]
        if not available:
            available = list(range(len(pool)))
        if not available:
            return None
        sorted_by_use = sorted(available, key=lambda i: _usage.get((pool_name, i), 0))
        candidates = sorted_by_use[:8]
        chosen = random.choice(candidates)
        _usage[(pool_name, chosen)] = _usage.get((pool_name, chosen), 0) + 1
        _last_used = (pool_name, chosen)
        return pool[chosen]

    # Always try primary first — 2500 residential slots available
    result = _pick("primary", PRIMARY_PROXIES)
    if result:
        return result

    # Fallback (extremely unlikely with 2500 slots)
    backup = _get_backup_proxies()
    if backup:
        print("  [PROXY] All primary slots on cooldown — using backup pool")
        result = _pick("backup", backup)
        if result:
            return result

    print("  [PROXY] All proxies on cooldown — force-reusing primary")
    return random.choice(PRIMARY_PROXIES)


def reset_usage_counts() -> None:
    global _usage
    _usage = {}
