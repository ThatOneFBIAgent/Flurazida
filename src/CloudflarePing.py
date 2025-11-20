# Helper library for cloudflare pinging and storing of cache.

# Standard Library Imports
import asyncio
import time
import os
import platform
from typing import Optional, TypedDict, Dict, Union


# Third-Party Imports
import aiohttp


# Local Imports
from logger import get_logger

log = get_logger()

CLOUD_FLARE_PING_INTERVAL = 1800  # seconds
CLOUD_FLARE_IPV4 = "1.1.1.1"
CLOUD_FLARE_IPV6 = "2606:4700:4700::1111"

# Environment detection
IS_RAILWAY = "RAILWAY_PROJECT_ID" in os.environ  # most reliable Railway indicator
IS_DOCKER = os.path.exists("/.dockerenv")
IS_LINUX = platform.system().lower() == "linux"

class CloudflareCache(TypedDict):
    ipv4: Optional[float]
    ipv6: Optional[float]
    ts: Optional[float]
    error: Optional[str]

# module-level cache + lock + task handle
_CACHE: CloudflareCache = {"ipv4": None, "ipv6": None, "ts": None, "error": None}
_CACHE_LOCK = asyncio.Lock()
_task: Optional[asyncio.Task] = None

async def _ping_once(session: aiohttp.ClientSession, url: str) -> float:
    start = time.monotonic()
    async with session.get(url, timeout=10) as resp:
        resp.raise_for_status()
        # we don't need content, but ensure the request completes
        await resp.text()
    return (time.monotonic() - start) * 1000.0  # ms

async def _loop(interval: float, session: Optional[aiohttp.ClientSession] = None):
    global _CACHE
    log.info("Cloudflare ping loop started (interval=%s)", interval)
    while True:
        try:
            # Use provided session or create a temporary one
            use_shared = session is not None and not session.closed
            if use_shared:
                ping_session = session
            else:
                ping_session = aiohttp.ClientSession()
            
            try:
                try:
                    v4 = await _ping_once(ping_session, f"https://{CLOUD_FLARE_IPV4}/cdn-cgi/trace")
                except Exception as e:
                    v4 = None
                    log.warning("CF IPv4 ping failed: %s", e)
                v6 = None
                if not IS_RAILWAY and not IS_DOCKER:
                    try:
                        v6 = await _ping_once(ping_session, f"https://[{CLOUD_FLARE_IPV6}]/cdn-cgi/trace")
                    except Exception as e:
                        v6 = None
                        log.warning("CF IPv6 ping failed: %s", e)
                else:
                    log.debug("Skipping IPv6 ping (unsupported in Railway/Docker).")
                async with _CACHE_LOCK:
                    _CACHE["ipv4"] = v4
                    _CACHE["ipv6"] = v6
                    _CACHE["ts"] = time.time()
                    _CACHE["error"] = None
            finally:
                # Only close if we created a temporary session
                if not use_shared:
                    await ping_session.close()
        except Exception as e:
            log.warning("Unexpected error in Cloudflare ping loop: %s", e)
            async with _CACHE_LOCK:
                _CACHE["error"] = str(e)
        await asyncio.sleep(interval)

def ensure_started(interval: int = CLOUD_FLARE_PING_INTERVAL, session: Optional[aiohttp.ClientSession] = None) -> asyncio.Task:
    """Start background task if not already running. Returns the Task.
    
    Args:
        interval: Ping interval in seconds
        session: Optional shared HTTP session to use (recommended)
    """
    global _task
    loop = asyncio.get_event_loop()
    if _task is None or _task.done():
        _task = loop.create_task(_loop(interval, session))
    return _task

async def get_cached_pings() -> Dict[str, Optional[Union[float, str]]]:
    """Return a copy of the cached results (ipv4, ipv6, ts, error)."""
    async with _CACHE_LOCK:
        # construct an explicit dict so the value types are inferred as
        # Optional[Union[float, str]] instead of plain object.
        return {
            "ipv4": _CACHE["ipv4"],
            "ipv6": _CACHE["ipv6"],
            "ts": _CACHE["ts"],
            "error": _CACHE["error"],
        }

# convenience immediate ping (no caching) if you need fresh values
async def ping_now(session: Optional[aiohttp.ClientSession] = None) -> Dict[str, Optional[float]]:
    """Ping Cloudflare immediately without caching.
    
    Args:
        session: Optional shared HTTP session to use (recommended)
    """
    use_shared = session is not None and not session.closed
    if use_shared:
        ping_session = session
    else:
        ping_session = aiohttp.ClientSession()
    
    try:
        try:
            v4 = await _ping_once(ping_session, f"https://{CLOUD_FLARE_IPV4}/cdn-cgi/trace")
        except Exception:
            v4 = None
        try:
            v6 = await _ping_once(ping_session, f"https://[{CLOUD_FLARE_IPV6}]/cdn-cgi/trace")
        except Exception:
            v6 = None
        return {"ipv4": v4, "ipv6": v6, "ts": time.time(), "error": None}
    finally:
        if not use_shared:
            await ping_session.close()