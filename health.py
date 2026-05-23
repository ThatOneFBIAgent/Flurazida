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
    """Check if bot is truly ready to handle commands."""
    if bot.is_closed():
        return False
    
    # For sharded bots, check if the bot has emitted on_ready at least once
    if hasattr(bot, "_ready_once"):
        return bot._ready_once.is_set()
    
    # Fallback for non-sharded bots
    return bot.is_ready()


async def _serve(bot: discord.Client) -> None:
    async def handle(req: web.Request) -> web.Response:  # noqa: ARG001
        ok = _is_ready(bot)
        status = 200 if ok else 503
        text = "OK" if ok else "Starting"
        return web.Response(status=status, text=text)

    try:
        runner = web.AppRunner(web.Application(), access_log=None)
        runner.app.router.add_get(_ENDPOINT, handle)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", _PORT)
        await site.start()
        print(f"[health] Listening on :{_PORT}{_ENDPOINT}")
        # Stays alive until the event loop closes; Railway polls this at deploy time.
    except Exception as e:
        print(f"[health] FAILED to start: {e}")
        raise


def attach(bot: discord.Client) -> None:
    """Register the health server to start inside the bot's setup_hook."""
    original = bot.setup_hook  # bound method on the instance

    async def _setup_hook() -> None:
        await original()
        asyncio.get_event_loop().create_task(_serve(bot), name="health-server")

    bot.setup_hook = _setup_hook  # instance-level override, class untouched
