"""
status.py - Drop-in client for Discord bots to report telemetry.

Usage (discord.py / Pycord):
    from status import StatusReporter, BotMonitor

    reporter = StatusReporter(
        api_url=os.getenv("DASHBOARD_URL"),          # Railway internal link
        private_key_pem=os.getenv("RSA_PRIVATE_KEY"), # PEM string
        bot_id="flurazide",
    )

    # Inside your bot's on_ready or setup_hook:
    monitor = BotMonitor(reporter, bot)
    asyncio.create_task(monitor.run_forever())

The monitor will NOT crash your bot on failure — every send is wrapped
in try/except with exponential backoff (3 attempts, then silent skip).
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import platform
import time
from typing import TYPE_CHECKING, Any, Dict, Optional

import aiohttp
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

if TYPE_CHECKING:
    import discord

logger = logging.getLogger(__name__)


class StatusReporter:
    """Signs and sends status payloads to the dashboard server."""

    def __init__(self, api_url: str, private_key_pem: str, bot_id: str):
        api_url = api_url.rstrip("/")
        if not api_url.startswith(("http://", "https://")):
            api_url = f"http://{api_url}"
        self.api_url = api_url
        self.bot_id = bot_id
        self._private_key = serialization.load_pem_private_key(
            private_key_pem.encode() if isinstance(private_key_pem, str) else private_key_pem,
            password=None,
        )
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        """Re-use a single session for connection pooling."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10),
            )
        return self._session

    def _sign(self, data: dict) -> str:
        """RSA-PSS sign the deterministic JSON of `data`."""
        message = json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
        sig = self._private_key.sign(
            message,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.MAX_LENGTH,
            ),
            hashes.SHA256(),
        )
        return base64.b64encode(sig).decode()

    async def send(self, data: dict, *, max_retries: int = 3) -> bool:
        """
        Send a status update.  Returns True on success, False on failure.
        Never raises — safe to fire-and-forget.
        """
        payload = {
            "bot_id": self.bot_id,
            "timestamp": time.time(),
            "data": data,
            "signature": self._sign(data),
        }

        for attempt in range(max_retries):
            try:
                session = await self._get_session()
                async with session.post(
                    f"{self.api_url}/status/update", json=payload,
                ) as resp:
                    if resp.status == 200:
                        logger.debug("Status sent for %s", self.bot_id)
                        return True
                    body = await resp.text()
                    logger.warning(
                        "Attempt %d/%d failed (%d): %s",
                        attempt + 1, max_retries, resp.status, body,
                    )
            except Exception as exc:
                logger.warning(
                    "Attempt %d/%d connection error: %s",
                    attempt + 1, max_retries, exc,
                )

            if attempt < max_retries - 1:
                await asyncio.sleep(2 ** attempt)  # 1s, 2s

        logger.error("All %d attempts failed for %s — skipping.", max_retries, self.bot_id)
        return False

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()


class BotMonitor:
    """
    Collects real metrics from a discord.py / Pycord Bot instance
    and feeds them to a StatusReporter on two cadences:

      • Fast  (60s)  — shards, latency, uptime, host info
      • Slow  (600s) — guild count, member count (API-heavy)
    """

    def __init__(
        self,
        reporter: StatusReporter,
        bot: "discord.Bot | discord.AutoShardedBot",
        *,
        fast_interval: int = 60,
        slow_interval: int = 600,
    ):
        self.reporter = reporter
        self.bot = bot
        self.fast_interval = fast_interval
        self.slow_interval = slow_interval
        self._start_time = time.monotonic()

    async def run_forever(self):
        """Launch both metric loops concurrently.  Never raises."""
        await asyncio.gather(
            self._loop_fast(),
            self._loop_slow(),
        )

    # -- fast metrics (non-blocking) ----------------------------------------

    async def _loop_fast(self):
        while True:
            try:
                await self.reporter.send(self._collect_fast())
            except Exception as exc:
                logger.error("Fast metric loop error: %s", exc)
            await asyncio.sleep(self.fast_interval)

    def _collect_fast(self) -> Dict[str, Any]:
        uptime = time.monotonic() - self._start_time
        shards = []
        if hasattr(self.bot, "shards") and self.bot.shards:
            for sid, shard in self.bot.shards.items():
                shards.append({
                    "id": sid,
                    "latency_ms": round(shard.latency * 1000, 1),
                    "is_closed": shard.is_closed(),
                })
        else:
            shards.append({
                "id": 0,
                "latency_ms": round(self.bot.latency * 1000, 1),
                "is_closed": self.bot.is_closed(),
            })

        return {
            "type": "fast",
            "uptime_seconds": round(uptime),
            "shards": shards,
            "host": {
                "python": platform.python_version(),
                "os": platform.system(),
            },
        }

    # -- slow metrics (may hit Discord API) ---------------------------------

    async def _loop_slow(self):
        # Wait a bit on first run so the bot cache is warm
        await asyncio.sleep(30)
        while True:
            try:
                await self.reporter.send(await self._collect_slow())
            except Exception as exc:
                logger.error("Slow metric loop error: %s", exc)
            await asyncio.sleep(self.slow_interval)

    async def _collect_slow(self) -> Dict[str, Any]:
        guilds = self.bot.guilds
        return {
            "type": "slow",
            "guild_count": len(guilds),
            "member_count": sum(g.member_count or 0 for g in guilds),
            "channel_count": sum(len(g.channels) for g in guilds),
        }
