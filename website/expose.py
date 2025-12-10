# little helper to expose backend info to the website in the form of a json

# Standard imports
import psutil
import json
import time
import platform
import os
import asyncio
import aiohttp
from aiohttp import web
from datetime import datetime

# Third party imports
import discord

async def get_bot_stats(bot):
    """Gathers statistics about the bot and system."""
    
    # System Stats
    cpu_usage = psutil.cpu_percent()
    ram_usage = psutil.virtual_memory().percent
    
    # Bot Stats
    guilds = len(bot.guilds)
    users = sum(g.member_count for g in bot.guilds)
    latency = round(bot.latency * 1000, 2)
    
    # Uptime
    now = time.time()
    bot_start_time = getattr(bot, "start_time", time.time())
    uptime_seconds = int(now - bot_start_time)
    
    # Shard Info
    shards = {}
    if bot.shards:
        for shard_id, shard in bot.shards.items():
            shards[shard_id] = {
                "latency": round(shard.latency * 1000, 2),
                "is_closed": shard.is_closed()
            }

    stats = {
        "system": {
            "cpu_percent": cpu_usage,
            "ram_percent": ram_usage,
            "platform": f"{platform.system()} {platform.release()}",
            "python_version": platform.python_version(),
            "discord_py_version": discord.__version__
        },
        "bot": {
            "guilds": guilds,
            "users": users,
            "latency_ms": latency,
            "uptime_seconds": uptime_seconds,
            "shards": shards,
            "status": "online" # if we are responding, we are online 
        },
        "timestamp": datetime.utcnow().isoformat()
    }
    return stats

async def handle_stats(request):
    """Handle the /stats endpoint."""
    bot = request.app['bot']
    stats = await get_bot_stats(bot)
    return web.json_response(stats)

async def handle_health(request):
    """handle the /health endpoint."""
    bot = request.app['bot']
    stats = await get_bot_health(bot)
    return web.json_response(stats)

async def handle_root(request):
    """Handle the / endpoint."""
    return web.json_response({"message": "Flurazida Backend API is running.", "endpoints": ["/stats"]})

async def start_web_server(bot):
    """Starts the aiohttp web server."""
    app = web.Application()
    app['bot'] = bot
    app.add_routes([
        web.get('/', handle_root),
        web.get('/stats', handle_stats),
    ])
    
    runner = web.AppRunner(app)
    await runner.setup()
    
    # Get port from env or default to 5000
    port = int(os.getenv("PORT", 5000))
    
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    
    # Log to the bot's logger if possible, otherwise print
    try:
        from logger import get_logger
        log = get_logger()
        log.info(f"Web server started on port {port}")
    except ImportError:
        log = None
        print(f"Web server started on port {port}")

    # Start the periodic stats poster
    bot.loop.create_task(post_stats_task(bot, base_logger=log))

async def post_stats_task(bot, base_logger=None):
    await bot.wait_until_ready()

    target_url = os.getenv("PROD_STATUS_URL") or "http://localhost:8000/api/bot/status"

    if base_logger:
        log_info = base_logger.info
        log_error = base_logger.error
    else:
        log_info = print
        log_error = print

    log_info(f"Starting stats poster task targeting: {target_url}")

    consecutive_failures = 0
    MAX_CONSECUTIVE_FAILURES = 4

    async with aiohttp.ClientSession() as session:
        while not bot.is_closed():
            try:
                stats = await get_bot_stats(bot)

                async with session.post(target_url, json=stats) as response:
                    if response.status in (200, 201, 204):
                        log_info(f"POST {target_url} -> {response.status}")
                        consecutive_failures = 0  # Reset on success
                    else:
                        log_error(f"Failed to post stats: {response.status}")
                        consecutive_failures += 1
                        # Print server response for debugging
                        try:
                            text = await response.text()
                            log_error(f"Response body: {text}")
                        except Exception as e:
                            log_error(f"Could not read response body: {e}")

            except Exception as e:
                log_error(f"Error posting stats: {e}")
                consecutive_failures += 1

            if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                log_error(f"Consecutive failures reached {MAX_CONSECUTIVE_FAILURES}. Shutting down stats poster task.")
                session.close() # this surely wont close every single session right?
                break # Exit the loop to shut down the task

            await asyncio.sleep(60)


