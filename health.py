"""
health.py — zero-config Railway healthcheck for discord.py bots.

Usage in your entry point (two lines):
    import health
    health.attach(bot)          # before bot.start() / bot.run()

Railway: set healthcheck path to /health. That's it.
PORT is injected automatically by Railway.

Main use: stop railway from thrashing the old instance during deploys while the bot is still starting up.
"""

from __future__ import annotations

import asyncio
import os
from typing import TYPE_CHECKING

from aiohttp import web

if TYPE_CHECKING:
    import discord

_PORT = int(os.environ.get("PORT", 8080))
_ENDPOINT = "/health"


def _is_ready(bot: discord.Client) -> bool:
    if bot.is_closed():
        return False
    shards: dict | None = getattr(bot, "shards", None)
    if shards:
        return all(not s.is_closed() for s in shards.values())
    return bot.is_ready()


async def _serve(bot: discord.Client) -> None:
    async def handle(req: web.Request) -> web.Response:  # noqa: ARG001
        ok = _is_ready(bot)
        return web.Response(status=200 if ok else 503, text="OK" if ok else "Starting")

    runner = web.AppRunner(web.Application(), access_log=None)
    runner.app.router.add_get(_ENDPOINT, handle)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", _PORT).start()
    print(f"[health] :{_PORT}{_ENDPOINT}")
    # Stays alive until the event loop closes; no explicit teardown needed —
    # Railway only polls once at deploy time, so the server is idle after that.


def attach(bot: discord.Client) -> None:
    """Register the health server to start inside the bot's setup_hook."""
    original = bot.setup_hook  # bound method on the instance

    async def _setup_hook() -> None:
        await original()
        asyncio.get_event_loop().create_task(_serve(bot), name="health-server")

    bot.setup_hook = _setup_hook  # instance-level override, class untouched
