#    "Flurazide" - A more than basic Discord bot with database functions
#    (okay "basic" is an understatement becuase of my autism)
#    © 2024-2026  Iza Carlos (Aka Carlos E.)
#    Licensed under the GNU Affero General Public License v3.0

# Standard Library Imports
import asyncio
import io
import os
import psutil
import random
import signal
import socket
import subprocess
import sys
import time
import contextlib
from typing import Optional

# Third-Party Imports
import aiohttp
import discord
from discord import Interaction, app_commands
from discord.app_commands import CheckFailure
from discord.ext import commands

os.environ["MAFIC_LIBRARY"] = "discord.py"
import mafic

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload
import socket
import sys
import time
import contextlib
from typing import Optional

# Local Application Imports
import CloudflarePing as cf
import config
from config import IS_ALPHA
from database import (
    get_expired_cases,
    periodic_backup,
    init_databases,
    ECONOMY_DB_PATH,
    MODERATOR_DB_PATH,
    BACKUP_FOLDER_ID,
    backup_all_dbs_to_gdrive_env,
    restore_all_dbs_from_gdrive_env,
)
from logger import get_logger

# Add website to path to import expose
sys.path.append(os.path.join(os.path.dirname(__file__), "..", "website"))
from expose import start_web_server

log = get_logger()

process = psutil.Process(os.getpid())
last_activity_signature = None
activities = config.ACTIVITIES
doubles = config.ALLOW_DOUBLE_ACTIVITIES

SINGLE_ACTIVITY_TYPES = ["A", "B", "C"]
DOUBLE_ACTIVITY_TYPES = ["A+B", "A+C", "B+C", "B+A", "C+A", "C+B"]
ALL_ACTIVITY_TYPES = SINGLE_ACTIVITY_TYPES + DOUBLE_ACTIVITY_TYPES

# Intents & Bot Setup
intents = discord.Intents.default()
intents.messages = True
intents.guilds = True
intents.message_content = True
intents.presences = True
intents.members = True
from extraconfig import BOT_OWNER, TEST_SERVER, FORBIDDEN_GUILDS, FORBIDDEN_USERS, WEBSITE_ENABLED
bot_owner = BOT_OWNER
test_server = TEST_SERVER

class Main(commands.AutoShardedBot):
    def __init__(self, *args, **kwargs):
        super().__init__(command_prefix="!", shard_count=3, intents=intents, max_messages=100, *args, **kwargs)
        self.user_id = bot_owner
        self._ready_once = asyncio.Event()
        self._activity_sync_lock = asyncio.Lock()
        self.start_time = time.time()
        self.http_session: Optional[aiohttp.ClientSession] = None

    async def setup_hook(self):
        # Initialize shared HTTP session
        self.http_session = aiohttp.ClientSession()
        log.info("Initialized shared HTTP session")
        
        # Start Cloudflare ping loop with shared session
        cf.ensure_started(session=self.http_session)

        # Start the web server
        if WEBSITE_ENABLED:
            try:
                await start_web_server(self)
                log.success("Web server started")
            except Exception as e:
                log.error(f"Failed to start web server: {e}")
        else:
            log.info("Website disabled, skipping web server startup")
        
        commands_dir = os.path.join(os.path.dirname(__file__), "commands")
        failed = []
        for filename in os.listdir(commands_dir):
            if not filename.endswith(".py"):
                continue
            cog_name = filename[:-3]
            cog_path = f"commands.{cog_name}"
            try:
                await self.load_extension(cog_path)
                log.success(f"Loaded cog: {cog_name}")
            except Exception as e:
                failed.append((cog_name, e))
                log.critical(f"Failed to load cog `{cog_name}`; continuing without it. Reason: {e}")

        if failed:
            log.error(f"{len(failed)} cog(s) failed to load: {[n for n, _ in failed]}")
        else:
            log.success("All cogs loaded successfully")

        async def reload(interaction: discord.Interaction, cog_name: str):
            if interaction.user.id != self.user_id:
                return await interaction.response.send_message("❌ You do not have permission.", ephemeral=True)

            cog_name = cog_name.lower()
            cog_path = f"commands.{cog_name}"
            cog_file = f"./commands/{cog_name}.py"

            if not os.path.exists(cog_file):
                return await interaction.response.send_message(f"❌ Cog `{cog_name}` not found.", ephemeral=True)

            try:
                if cog_path in self.extensions:
                    await self.reload_extension(cog_path)
                    msg = f"🔁 Reloaded `{cog_name}` successfully."
                    log.success(msg)
                    await interaction.response.send_message(msg, ephemeral=True)
                else:
                    await self.load_extension(cog_path)
                    msg = f"📥 Loaded new cog `{cog_name}` successfully."
                    log.success(msg)
                    await interaction.response.send_message(msg, ephemeral=True)
            except Exception as e:
                log.exception(f"Failed to load or reload `{cog_name}`")
                await interaction.response.send_message(f"❌ Failed to load `{cog_name}`: {e}", ephemeral=True)

        self.tree.add_command(app_commands.Command(
            name="reload",
            description="Reloads a specific cog.",
            callback=reload
        ))

        await self.tree.sync()
        await self.tree.sync(guild=discord.Object(id=test_server))

    async def change_activity_all(self, activity, status):
        """Force sync activities across all shards safely."""
        async with self._activity_sync_lock:
            for shard_id, ws in self.shards.items():
                try:
                    human_shard = shard_id + 1
                    activityname = getattr(activity, "name", None) or str(activity)
                    pershardactivity = f"{activityname} | {human_shard}/{self.shard_count}"
                    newactivity = discord.Activity(name=pershardactivity, type=activity.type)
                    await self.change_presence(activity=newactivity, status=status, shard_id=shard_id)
                    log.info(f"[Shard {shard_id}] Synced activity: {pershardactivity} ({activity.type.name})")
                except Exception as e:
                    log.warning(f"[Shard {shard_id}] Failed to sync activity: {e}")

bot = Main()
bot.help_command = None

@bot.event
async def on_ready():
    # Only run once, even though each shard calls on_ready
    if not bot._ready_once.is_set():
        total_shards = bot.shard_count or 1
        log.event(f"Bot is online as {bot.user} (ID: {bot.user.id})")
        log.event(f"Connected to {len(bot.guilds)} guilds across {total_shards} shard(s).")
        log.event(f"Serving approximately {sum(g.member_count for g in bot.guilds)} users.")

        # Connect to Lavalink
        try:
            await mafic.NodePool(bot).create_node(
                host=config.LAVALINK_HOST,
                port=config.LAVALINK_PORT,
                label="main",
                password=config.LAVALINK_PASSWORD,
            )
            log.success("Connected to Lavalink node.")
        except Exception as e:
            log.critical(f"Failed to connect to Lavalink node: {e}")

        bot._ready_once.set()
    else:
        # Shard resumed event — bot reconnected
        log.info(f"[Shard {bot.shard_id or '?'}] resumed session in {time.time() - bot.start_time:.2f} seconds.")

@bot.event
async def on_shard_connect(shard_id):
    log.event(f"[Shard {shard_id}] connected successfully.")

@bot.event
async def on_shard_ready(shard_id):
    guilds = [g for g in bot.guilds if g.shard_id == shard_id]
    log.event(f"[Shard {shard_id}] ready — handling {len(guilds)} guild(s).")

@bot.event
async def on_shard_disconnect(shard_id):
    log.warning(f"[Shard {shard_id}] disconnected — waiting for resume.")

@bot.event
async def on_shard_resumed(shard_id):
    log.event(f"[Shard {shard_id}] resumed connection cleanly.")

@bot.event
async def on_message(message):
    if message.author.bot:
        return
    bot_name = str(bot.user.name).lower()
    if bot_name in message.content.lower():
        for emoji in ["🧪"]:
            try:
                await message.add_reaction(emoji)
            except Exception as e:
                log.warning(f"Failed to react {emoji}: {e}")
    await bot.process_commands(message)

@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: discord.app_commands.AppCommandError):
    from discord.app_commands import CommandInvokeError

    # Extract the "real" underlying error
    original = error.original if isinstance(error, CommandInvokeError) else error

    cmd_name = getattr(interaction.command, "name", "Unknown")
    cmd_group = getattr(interaction.command, "qualified_name", cmd_name)
    user = f"{interaction.user} ({interaction.user.id})"

    log.error(
        f"Slash command failed:\n"
        f" • Command: {cmd_group}\n"
        f" • User: {user}\n"
        f" • Guild: {getattr(interaction.guild, 'name', 'DM')} "
        f"({getattr(interaction.guild, 'id', 'N/A')})",
        exc_info=original
    )

# yes i am this petty.
async def global_blacklist_check(interaction: Interaction) -> bool:
    # Check if bot is shutting down/restarting
    if getattr(bot, "_is_shutting_down", False):
        await interaction.response.send_message(
            "🚧 Bot is restarting, please try again later.",
            ephemeral=True
        )
        return False

    # Check guild blacklist
    guild_id = interaction.guild.id if interaction.guild else None
    if guild_id in FORBIDDEN_GUILDS:
        reason = FORBIDDEN_GUILDS[guild_id].get("reason", "No reason")
        await interaction.response.send_message(
            f"**This server is not allowed to use the bot.**\nReason: {reason}",
            ephemeral=True
        )
        return False

    # Check user blacklist
    user_id = interaction.user.id
    if user_id in FORBIDDEN_USERS:
        reason = FORBIDDEN_USERS[user_id].get("reason", "No reason")
        await interaction.response.send_message(
            f"❌ You are not allowed to use this bot.\nReason: {reason}",
            ephemeral=True
        )
        return False

    return True

async def cycle_paired_activities():
    global last_activity_signature
    await bot.wait_until_ready()
    while not bot.is_closed():
        for _ in range(10):
            combo_type = random.choice(ALL_ACTIVITY_TYPES if doubles else SINGLE_ACTIVITY_TYPES)
            status = random.choice([discord.Status.online, discord.Status.idle, discord.Status.dnd])
            game = lambda: random.choice([a for a in activities if isinstance(a, discord.Game)])
            listen = lambda: random.choice([a for a in activities if isinstance(a, discord.Activity) and a.type == discord.ActivityType.listening])
            watch = lambda: random.choice([a for a in activities if isinstance(a, discord.Activity) and a.type == discord.ActivityType.watching])

            activity, combined = None, None
            if combo_type == "A": activity = game()
            elif combo_type == "B": activity = listen()
            elif combo_type == "C": activity = watch()
            elif combo_type == "A+B": combined = discord.Game(f"{game().name} & listening to {listen().name}")
            elif combo_type == "A+C": combined = discord.Game(f"{game().name} & watching {watch().name}")
            elif combo_type == "B+A": combined = discord.Activity(type=discord.ActivityType.listening, name=f"{listen().name} & playing {game().name}")
            elif combo_type == "B+C": combined = discord.Activity(type=discord.ActivityType.listening, name=f"{listen().name} & watching {watch().name}")
            elif combo_type == "C+A": combined = discord.Activity(type=discord.ActivityType.watching, name=f"{watch().name} & playing {game().name}")
            elif combo_type == "C+B": combined = discord.Activity(type=discord.ActivityType.watching, name=f"{watch().name} & listening to {listen().name}")

            act = combined if combined else activity
            sig = (act.name, act.type)
            if sig != last_activity_signature:
                last_activity_signature = sig
                await bot.change_activity_all(activity=act, status=status)
                log.event(f"Changed presence to: {act.name} ({act.type})")
                break
        await asyncio.sleep(900)

async def moderation_expiry_task():
    await bot.wait_until_ready()
    log.event("Moderation expiry task started.")

    while not bot.is_closed():
        try:
            now = int(time.time())
            # Fetch all expired cases at once
            expired_cases = await get_expired_cases(None, "ban", now)
            # expected structure: [(guild_id, user_id), ...] when guild_id is None

            if not expired_cases:
                await asyncio.sleep(90 + random.randint(0, 10))  # no rush
                continue

            guild_map = {}
            for guild_id, user_id in expired_cases:
                guild_map.setdefault(guild_id, []).append(user_id)

            for guild_id, user_ids in guild_map.items():
                guild = bot.get_guild(guild_id)
                if not guild:
                    continue

                for user_id in user_ids:
                    try:
                        user = await bot.fetch_user(user_id)
                        await guild.unban(user, reason="Ban expired")
                        log.trace(f"Unbanned {user} ({user_id}) from {guild.name}")
                    except discord.NotFound:
                        log.warning(f"User {user_id} not found when unbanning.")
                    except Exception as e:
                        log.critical(f"Failed to unban {user_id} in {guild_id}: {e}")

            # sleep slightly randomized to desync ticks
            await asyncio.sleep(60 + random.randint(5, 20))

        except Exception as e:
            log.critical(f"moderation_expiry_task crashed: {e}")
            await asyncio.sleep(30) 

async def kill_all_tasks():
    current = asyncio.current_task()
    for task in asyncio.all_tasks():
        if task is current: continue
        task.cancel()
    await asyncio.sleep(1)

async def graceful_shutdown():
    log.info("Shutdown signal received — performing cleanup...")

    # Prevent new interactions (optional but good practice)
    bot._is_shutting_down = True

    # Let ongoing tasks wrap up
    await asyncio.sleep(1)

    # Final backup
    if not IS_ALPHA:
        try:
            log.info("Performing final database backup before shutdown...")
            await asyncio.wait_for(
                backup_all_dbs_to_gdrive_env(
                    [
                        (ECONOMY_DB_PATH, "economy.db"),
                        (MODERATOR_DB_PATH, "moderator.db")
                    ],
                    BACKUP_FOLDER_ID
                ),
                timeout=25  # must complete before Railway kills us
            )
            log.info("Backup completed successfully.")
        except asyncio.TimeoutError:
            log.critical("Backup timed out — Railway may have killed us mid-upload.")
        except Exception as e:
            log.critical(f"Backup failed: {e}")
    else:
        log.warning("Skipping final backup as this is an alpha version.")

    # Close shared HTTP session
    if bot.http_session and not bot.http_session.closed:
        await bot.http_session.close()
        log.info("Closed shared HTTP session")
        
    # Close bot connections
    await kill_all_tasks()
    with contextlib.suppress(Exception):
        await bot.close()

    log.info("Shutdown complete.")
    log.info("Flurazide says: Goodbye!")

    # if discord.py is still not closing shit, throw the interpreter (and everything) into the void
    await asyncio.sleep(10)
    os._exit(0)
    
    

async def main():
    # Attempt restore and surface any problem (was being called silently)
    try:
        restored_ok = await restore_all_dbs_from_gdrive_env(BACKUP_FOLDER_ID,
            {
                "economy.db": ECONOMY_DB_PATH,
                "moderator.db": MODERATOR_DB_PATH,
            }
        )
        if restored_ok is False:
            log.warning("Drive restore returned False (no files restored or an error occurred).")
    except Exception:
        log.exception("Exception while restoring databases from Drive")

    # Initialize database tables
    await init_databases()
    BACKUP_DELAY_HOURS = 1
    async def delayed_backup_starter(delay_hours):
        if IS_ALPHA:
            log.warning("Skipping backup as this is an alpha version.")
            return
        await bot.wait_until_ready()
        await asyncio.sleep(delay_hours * 3600)
        asyncio.create_task(periodic_backup(delay_hours))
        log.info(f"Periodic backup started after {delay_hours}h delay.")

    async with bot:
        asyncio.create_task(cycle_paired_activities())
        asyncio.create_task(moderation_expiry_task())
        asyncio.create_task(delayed_backup_starter(BACKUP_DELAY_HOURS))
        bot.tree.interaction_check = global_blacklist_check

        shutdown_signal = asyncio.get_event_loop().create_future()

        def _signal_handler():
            if not shutdown_signal.done():
                shutdown_signal.set_result(True)

        loop = asyncio.get_event_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, _signal_handler)
            except NotImplementedError:
                # Windows: loop.add_signal_handler usually isn't implemented for SIGTERM
                log.warning(f"Cannot register signal handler for {sig!r} on this platform; falling back to default behaviour.")
            except Exception:
                log.exception(f"Failed to register signal handler for {sig!r}")

        bot_task = asyncio.create_task(bot.start(config.BOT_TOKEN))

        try:
            # Wait for our shutdown future. If the process receives a KeyboardInterrupt
            await shutdown_signal
        except asyncio.CancelledError:
            log.info("Shutdown future was cancelled; initiating cleanup.")
        except KeyboardInterrupt:
            log.info("KeyboardInterrupt received; initiating cleanup.")
        finally:
            # Ensure bot.start task is cancelled and awaited so discord cleans up
            if not bot_task.done():
                bot_task.cancel()
                try:
                    await bot_task
                except asyncio.CancelledError:
                    pass
            # Ensure final graceful shutdown (closes http session, backups, bot.close)
            try:
                await graceful_shutdown()
            except Exception:
                log.exception("Error during graceful shutdown")
                sys.exit(1)

# optinally if someone rawdogs main.py
if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        # Use log.exception to capture full traceback rather than only the exception string
        log.exception(f"Fatal crash as {e}")
        sys.exit(1)