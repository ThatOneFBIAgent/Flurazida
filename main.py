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

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload

# Local Application Imports
import services.cloudflare_ping as cf
import config
from config import IS_ALPHA, get_activity
from database import (
    get_expired_cases,
    periodic_backup,
    init_databases,
    ECONOMY_DB_PATH,
    MODERATOR_DB_PATH,
    BACKUP_FOLDER_ID,
    backup_all_dbs_to_gdrive_env,
    restore_all_dbs_from_gdrive_env,
    get_total_economy_sum,
)
from logging_modules.custom_logger import get_logger
from status import StatusReporter, BotMonitor, ConfigSync

reporter = StatusReporter(
    api_url=os.getenv("DASHBOARD_URL"),          # Railway internal link
    private_key_pem=os.getenv("RSA_PRIVATE_KEY"), # PEM string
    bot_id="flurazide",
)

log = get_logger()

process = psutil.Process(os.getpid())
last_activity_signature = None

# Intents & Bot Setup
intents = discord.Intents.default()
intents.messages = True
intents.guilds = True
intents.message_content = True
intents.presences = True
intents.members = True
from extraconfig import BOT_OWNER, TEST_SERVER, FORBIDDEN_GUILDS, FORBIDDEN_USERS
bot_owner = BOT_OWNER
test_server = TEST_SERVER

# fucking prefixes istg
def prefix(bot, message):
    if not bot.user:
        return ['>>']
    return ['>>', bot.user.mention]
class Main(commands.AutoShardedBot):
    def __init__(self, *args, **kwargs):
        super().__init__(command_prefix=prefix, shard_count=3, intents=intents, max_messages=100, *args, **kwargs)
        self.user_id = bot_owner
        self._ready_once = asyncio.Event()
        self._activity_sync_lock = asyncio.Lock()
        self.start_time = time.time()
        self.http_session: Optional[aiohttp.ClientSession] = None
        self.cached_economy = 0

    async def setup_hook(self):
        # Initialize shared HTTP session
        self.http_session = aiohttp.ClientSession()
        log.info("Initialized shared HTTP session")
        
        # Start Cloudflare ping loop with shared session
        cf.ensure_started(session=self.http_session)

        log.info("Starting background tasks")
        # Flurazide is do-it-all, so we can add more metrics here later
        monitor = BotMonitor(
            reporter, 
            self,
            custom_metrics_callback=lambda: {"economy": self.cached_economy}
        )
        asyncio.create_task(monitor.run_forever())
        
        # Start config polling
        self.config_sync = ConfigSync(
            api_url=os.getenv("DASHBOARD_URL"),
            bot_id="flurazide",
            bot=self,
        )
        asyncio.create_task(self.config_sync.run_forever())

        # Register global checks
        self.tree.interaction_check = global_blacklist_check
        
        # Dynamic cooldown tracking (user_id -> command_name -> timestamp)
        self._custom_cooldowns = {}

        asyncio.create_task(self.update_economy_metrics())
        self.cycle_activities_task = asyncio.create_task(cycle_activities())
        self.moderation_expiry_task = asyncio.create_task(moderation_expiry_task())
        self.delayed_backup_starter_task = asyncio.create_task(delayed_backup_starter(BACKUP_DELAY_HOURS))
        
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
                    log.trace(f"[Shard {shard_id}] Synced activity: {pershardactivity} ({activity.type.name})")
                except Exception as e:
                    log.warning(f"[Shard {shard_id}] Failed to sync activity: {e}")

    async def update_economy_metrics(self):
        """Background task to update the economy cache every 5 minutes."""
        while True:
            try:
                self.cached_economy = await get_total_economy_sum()
                log.trace(f"Updated cached global economy: {self.cached_economy}")
            except Exception as e:
                log.error(f"Failed to update economy metrics: {e}")
            await asyncio.sleep(300) # 5 minutes

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

        bot._ready_once.set()
    else:
        # Shard resumed event — bot reconnected
        log.info(f"[Shard {bot.shard_id or '?'}] resumed session in {time.time() - bot.start_time:.2f} seconds.")

@bot.event
async def on_shard_connect(shard_id):
    log.event(f"[Shard {shard_id}] connected successfully in {time.time() - bot.start_time:.2f} seconds.")

@bot.event
async def on_shard_ready(shard_id):
    guilds = [g for g in bot.guilds if g.shard_id == shard_id]
    log.event(f"[Shard {shard_id}] ready — handling {len(guilds)} guild(s).")

disconnect_time = {}
@bot.event
async def on_shard_disconnect(shard_id):
    disconnect_time[shard_id] = time.time()
    log.warning(f"[Shard {shard_id}] disconnected — waiting for resume.")

@bot.event
async def on_shard_resumed(shard_id):
    if shard_id in disconnect_time: delta = time.time() - disconnect_time[shard_id] 
    else: delta = 0
    log.event(f"[Shard {shard_id}] resumed connection cleanly in {delta:.2f} seconds.")
    disconnect_time.pop(shard_id, None)

@bot.event
async def on_message(message):
    if message.author.bot:
        return
    bot_name = str(bot.user.name).lower()
    content = message.content.lower()

    mentioned_by_name = bot_name in content.split()
    mentioned_directly = bot.user in message.mentions
    
    if mentioned_by_name or mentioned_directly:
        for emoji in ["🧪"]:
            try:
                await message.add_reaction(emoji)
            except Exception as e:
                log.warning(f"Failed to react {emoji}: {e}")
    await bot.process_commands(message)

@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: discord.app_commands.AppCommandError):
    from discord.app_commands import (
        CommandInvokeError,
        BotMissingPermissions,
        MissingPermissions,
        CheckFailure
    )

    original = error.original if isinstance(error, CommandInvokeError) else error

    cmd_name = getattr(interaction.command, "name", "Unknown")
    cmd_group = getattr(interaction.command, "qualified_name", cmd_name)
    user = f"{interaction.user} ({interaction.user.id})"
    guild = f"{getattr(interaction.guild, 'name', 'DM')} ({getattr(interaction.guild, 'id', 'N/A')})"

    # Handle missing permissions: don't log as 'ERROR' unless you want the pain
    if isinstance(original, BotMissingPermissions):
        perms = ", ".join(original.missing_permissions)
        await _safe_response(interaction, f'❌ I cant do that, im missing: {perms}', True)
        log.warning(
            f"Bot missing perms for command {cmd_group} by {user} in {guild}: {perms}"
        )
        return

    if isinstance(original, MissingPermissions):
        perms = ", ".join(original.missing_permissions)
        await _safe_response(interaction, f'❌ You cant do that, missing: {perms}', True)
        log.info(
            f"User missing perms for command {cmd_group} by {user} in {guild}: {perms}"
        )
        return

    if isinstance(original, CheckFailure):
        await _safe_response(interaction, '❌ You dont meet the requirements for that.', True)
        log.info(
            f"Check failed for command {cmd_group} by {user} in {guild}"
        )
        return

    # real errors beyond checks
    log.error(
        f"Slash command failed:\n"
        f" • Command: {cmd_group}\n"
        f" • User: {user}\n"
        f" • Guild: {guild}",
        exc_info=original
    )

    await _safe_response(interaction, f'💥 The command imploded spectacularly, call Stephen Hawking. \n\n Err: {original}', True)

async def _safe_response(interaction, message, ephemeral=False):
    try:
        await interaction.response.send_message(message, ephemeral=ephemeral)
    except discord.InteractionResponded:
        await interaction.followup.send(message, ephemeral=ephemeral)

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

    # Check dashboard config overrides (if in a guild and it's a command)
    if guild_id and interaction.type == discord.InteractionType.application_command:
        settings = getattr(bot, "config_sync", None)
        if settings:
            guild_cfg = settings.get(guild_id)
            if guild_cfg:
                cmd_name = interaction.command.name if interaction.command else ""
                
                # 1. Module (Cog) Check
                cog_binding = getattr(interaction.command, "binding", None)
                if cog_binding:
                    # Normalize: FunCommands -> fun, EconomyCog -> economy
                    cog_name = cog_binding.__class__.__name__.replace("Commands", "").replace("Cog", "").lower()
                    if guild_cfg.get("enabled_modules", {}).get(cog_name) is False:
                        await interaction.response.send_message(
                            f"❌ The `{cog_name}` module is disabled in this server.",
                            ephemeral=True
                        )
                        return False
                
                # 2. Command Overrides Check (Unified Toggles & Cooldowns)
                overrides = guild_cfg.get("command_overrides", [])
                override = next((o for o in overrides if o.get("name") == cmd_name), None)
                
                if override:
                    cooldown = override.get("cooldown", 0)
                    
                    # Negative cooldown means disabled
                    if cooldown < 0:
                        await interaction.response.send_message(
                            f"❌ The `/{cmd_name}` command is disabled in this server.",
                            ephemeral=True
                        )
                        return False
                    
                    # Dynamic Cooldown Override
                    if cooldown > 0:
                        now = time.time()
                        if user_id not in bot._custom_cooldowns:
                            bot._custom_cooldowns[user_id] = {}
                        
                        last_use = bot._custom_cooldowns[user_id].get(cmd_name, 0)
                        if now - last_use < cooldown:
                            retry_after = cooldown - (now - last_use)
                            await interaction.response.send_message(
                                f"⏳ You are on cooldown for `/{cmd_name}`. Try again in {retry_after:.1f}s.",
                                ephemeral=True
                            )
                            return False
                            
                        bot._custom_cooldowns[user_id][cmd_name] = now

    return True

BACKUP_DELAY_HOURS = 1
async def delayed_backup_starter(delay_hours):
    """Waits for delay_hours then starts the periodic backup loop."""
    if IS_ALPHA:
        log.warning("Skipping backup as this is an alpha version.")
        return
    await bot.wait_until_ready()
    await asyncio.sleep(delay_hours * 3600)
    asyncio.create_task(periodic_backup(delay_hours))
    log.info(f"Periodic backup started after {delay_hours}h delay.")

async def cycle_activities():
    global last_activity_signature
    await bot.wait_until_ready()
    while not bot.is_closed():
        try:
            now_hour = time.localtime().tm_hour
            act = get_activity(now_hour)
            sig = (act.name, act.type)

            if sig != last_activity_signature:
                last_activity_signature = sig
                status = random.choice([discord.Status.online, discord.Status.idle, discord.Status.dnd])
                await bot.change_activity_all(activity=act, status=status)
                log.event(f"Changed presence to: {act.name} ({act.type})")
            else:
                log.debug(f"Activity signature unchanged: {act.name} ({act.type})")

            await asyncio.sleep(900)  # Sleep for 15 minutes before checking again
        except Exception as e:
            log.error(f"Error in cycle_activities: {e}", exc_info=True)
            await asyncio.sleep(60) # Short sleep on error to prevent busy-looping

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
