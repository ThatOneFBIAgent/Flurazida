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
    """Background task to post stats to a configured URL."""
    await bot.wait_until_ready()
    
    # Determine the target URL based on environment
    # Default local URL
    target_url = "http://localhost:8000/api/bot/status"
    
    # Check for Railway environment
    if os.getenv("RAILWAY_PROJECT_ID") or os.getenv("RAILWAY_STATIC_URL"):
        prod_url = os.getenv("PROD_STATUS_URL")
        # If the user supplied a specific URL, use it
        if prod_url:
            target_url = prod_url
        else:
            # Fallback or warning if production URL is expected but not found
            msg = "Running in Railway but PROD_STATUS_URL not set. Stats posting might fail or go to default."
            if base_logger:
                base_logger.warning(msg)
            else:
                print(msg)
            target_url = None

    if not target_url:
        return

    msg = f"Starting stats poster task targeting: {target_url}"
    if base_logger:
        base_logger.info(msg)
    else:
        print(msg)

    async with aiohttp.ClientSession() as session:
        while not bot.is_closed():
            try:
                stats = await get_bot_stats(bot)
                async with session.post(target_url, json=stats) as response:
                    if response.status not in (200, 201, 204):
                        err_msg = f"Failed to post stats to {target_url}: {response.status}"
                        if base_logger:
                            base_logger.warning(err_msg)
                        else:
                            print(err_msg)
            except Exception as e:
                err_msg = f"Error posting stats: {e}"
                if base_logger:
                    base_logger.error(err_msg)
                else:
                    print(err_msg)
            
            # Wait for 60 seconds before next post
            await asyncio.sleep(60)

