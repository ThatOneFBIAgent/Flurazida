import aiohttp, asyncio, time, logging
from typing import Optional, TypedDict, Dict, Union

CLOUD_FLARE_PING_INTERVAL = 1800  # seconds
CLOUD_FLARE_IPV4 = "1.1.1.1"
CLOUD_FLARE_IPV6 = "2606:4700:4700::1111"

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

async def _loop(interval: float):
    global _CACHE
    logging.info("Cloudflare ping loop started (interval=%s)", interval)
    while True:
        try:
            async with aiohttp.ClientSession() as session:
                try:
                    v4 = await _ping_once(session, f"https://{CLOUD_FLARE_IPV4}/cdn-cgi/trace")
                except Exception as e:
                    v4 = None
                    logging.debug("CF IPv4 ping failed: %s", e)

                try:
                    v6 = await _ping_once(session, f"https://[{CLOUD_FLARE_IPV6}]/cdn-cgi/trace")
                except Exception as e:
                    v6 = None
                    logging.debug("CF IPv6 ping failed: %s", e)

                async with _CACHE_LOCK:
                    _CACHE["ipv4"] = v4
                    _CACHE["ipv6"] = v6
                    _CACHE["ts"] = time.time()
                    _CACHE["error"] = None
            # end session
        except Exception as e:
            logging.exception("Unexpected error in Cloudflare ping loop")
            async with _CACHE_LOCK:
                _CACHE["error"] = str(e)
        await asyncio.sleep(interval)

def ensure_started(interval: int = CLOUD_FLARE_PING_INTERVAL) -> asyncio.Task:
    """Start background task if not already running. Returns the Task."""
    global _task
    loop = asyncio.get_event_loop()
    if _task is None or _task.done():
        _task = loop.create_task(_loop(interval))
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
async def ping_now() -> Dict[str, Optional[float]]:
    async with aiohttp.ClientSession() as session:
        try:
            v4 = await _ping_once(session, f"https://{CLOUD_FLARE_IPV4}/cdn-cgi/trace")
        except Exception:
            v4 = None
        try:
            v6 = await _ping_once(session, f"https://[{CLOUD_FLARE_IPV6}]/cdn-cgi/trace")
        except Exception:
            v6 = None
        return {"ipv4": v4, "ipv6": v6, "ts": time.time(), "error": None}