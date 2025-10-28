#    "Flurazide" - A more than basic Discord bot with database functions
#    (okay "basic" is an understatement becuase of my autism)
#    © 2024-2025  Iza Carlos (Aka Carlos E.)
#    Licensed under the GNU Affero General Public License v3.0

import discord
from discord.ext import commands
from discord import Interaction, app_commands
from discord.app_commands import CheckFailure
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload
from google.oauth2 import service_account
from googleapiclient.discovery import build
import config, asyncio, random, sys, socket, aiohttp, os, psutil, time, signal, io
import CloudflarePing as cf
from database import (
    get_expired_cases, mod_cursor, periodic_backup, restore_db_from_gdrive_env,
    ECONOMY_DB_PATH, MODERATOR_DB_PATH, BACKUP_FOLDER_ID, backup_all_dbs_to_gdrive_env, restore_all_dbs_from_gdrive_env
)
from logger import get_logger

log = get_logger("main")

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
intents.members = True
bot_owner = config.BOT_OWNER

def wait_for_continue():
    while True:
        answer = input("Mixer crashed! Turn back on? [Y/N]").strip().lower()
        if answer == "y":
            return True
        elif answer == "n":
            print("Exiting.")
            return False
        else:
            print("Please type Y or N.")

class Main(commands.AutoShardedBot):
    def __init__(self, *args, **kwargs):
        super().__init__(command_prefix="!", intents=intents, *args, **kwargs)
        self.user_id = bot_owner
        self._ready_once = asyncio.Event()
        self._activity_sync_lock = asyncio.Lock()
        self.start_time = time.time()

    async def setup_hook(self):
        commands_dir = os.path.join(os.path.dirname(__file__), "commands")
        failed = []
        for filename in os.listdir(commands_dir):
            if not filename.endswith(".py"):
                continue
            cog_name = filename[:-3]
            cog_path = f"commands.{cog_name}"
            try:
                await self.load_extension(cog_path)
                log.info(f"Loaded cog: {cog_name}")
            except Exception as e:
                failed.append((cog_name, e))
                log.exception(f"Failed to load cog `{cog_name}`; continuing without it.")

        if failed:
            log.warning(f"{len(failed)} cog(s) failed to load: {[n for n, _ in failed]}")

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
                    log.info(msg)
                    await interaction.response.send_message(msg, ephemeral=True)
                else:
                    await self.load_extension(cog_path)
                    msg = f"📥 Loaded new cog `{cog_name}` successfully."
                    log.info(msg)
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
        await self.tree.sync(guild=discord.Object(id=1240438418388029460))

    async def change_activity_all(self, activity, status):
        """Force sync activities across all shards safely."""
        async with self._activity_sync_lock:
            for shard_id, ws in self.shards.items():
                try:
                    await self.change_presence(activity=activity, status=status, shard_id=shard_id)
                    log.info(f"[Shard {shard_id}] Synced activity: {activity.name} ({activity.type.name})")
                except Exception as e:
                    log.warning(f"[Shard {shard_id}] Failed to sync activity: {e}")

bot = Main()
bot.help_command = None

@bot.event
async def on_ready():
    # Only run once, even though each shard calls on_ready
    if not bot._ready_once.is_set():
        total_shards = bot.shard_count or 1
        log.info(f"🚀 Bot is online as {bot.user} (ID: {bot.user.id})")
        log.info(f"Connected to {len(bot.guilds)} guilds across {total_shards} shard(s).")
        log.info(f"Serving approximately {sum(g.member_count for g in bot.guilds)} users.")
        bot._ready_once.set()
    else:
        # Shard resumed event — bot reconnected
        log.info(f"🔁 [Shard {bot.shard_id or '?'}] resumed session.")

@bot.event
async def on_shard_connect(shard_id):
    log.info(f"✅ [Shard {shard_id}] connected successfully.")

@bot.event
async def on_shard_ready(shard_id):
    log.info(f"🌐 [Shard {shard_id}] is ready and receiving events.")

@bot.event
async def on_shard_disconnect(shard_id):
    log.warning(f"⚠️ [Shard {shard_id}] disconnected — waiting for resume.")

@bot.event
async def on_shard_resumed(shard_id):
    log.info(f"🔄 [Shard {shard_id}] resumed connection cleanly.")

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

async def global_blacklist_check(interaction: Interaction) -> bool:
    guild_id = interaction.guild.id if interaction.guild else None
    if guild_id in config.FORBIDDEN_GUILDS:
        reason = config.FORBIDDEN_GUILDS[guild_id].get("reason", "No reason")
        await interaction.response.send_message(f"**This server is not allowed.**\nReason: {reason}", ephemeral=False)
        raise CheckFailure("Forbidden guild")
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
                log.info(f"Changed presence to: {act.name} ({act.type})")
                break
        await asyncio.sleep(900)

async def moderation_expiry_task():
    await bot.wait_until_ready()
    while not bot.is_closed():
        now = int(time.time())
        for guild in bot.guilds:
            expired_mutes = get_expired_cases(mod_cursor, guild.id, "mute", now)
            for _, user_id in expired_mutes:
                member = guild.get_member(user_id)
                mute_role = discord.utils.get(guild.roles, name="Muted")
                if member and mute_role:
                    try:
                        await member.remove_roles(mute_role, reason="Mute expired")
                    except Exception as e:
                        log.error(f"Unmute failed: {member} - {e}")
            expired_bans = get_expired_cases(mod_cursor, guild.id, "ban", now)
            for _, user_id in expired_bans:
                try:
                    user = await bot.fetch_user(user_id)
                    await guild.unban(user, reason="Ban expired")
                except Exception as e:
                    log.error(f"Unban failed: {user_id} - {e}")
        log.info("Moderation expiry check complete.")
        await asyncio.sleep(60)


async def main():
    restore_all_dbs_from_gdrive_env(BACKUP_FOLDER_ID,
        {
            "economy.db": ECONOMY_DB_PATH,
            "moderator.db": MODERATOR_DB_PATH,
        }
    )
    BACKUP_DELAY_HOURS = 1

    cf.ensure_started()
    async def delayed_backup_starter(delay_hours):
        await bot.wait_until_ready()
        await asyncio.sleep(delay_hours * 3600)
        asyncio.create_task(periodic_backup(delay_hours))
        log.info(f"Periodic backup started after {delay_hours}h delay.")

    async with bot:
        asyncio.create_task(cycle_paired_activities())
        asyncio.create_task(moderation_expiry_task())
        asyncio.create_task(delayed_backup_starter(BACKUP_DELAY_HOURS))
        bot.tree.interaction_check = global_blacklist_check

        # Create a future that will be set when a shutdown signal is received
        shutdown_signal = asyncio.get_event_loop().create_future()

        # Signal handler
        def _signal_handler():
            if not shutdown_signal.done():
                shutdown_signal.set_result(True)

        # Register signals
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, _signal_handler)

        # Run the bot, but also wait for shutdown signal
        bot_task = asyncio.create_task(bot.start(config.BOT_TOKEN))
        await shutdown_signal  # Wait here until signal received

        # Signal received → shutdown
        log.info("Shutdown signal received, stopping bot...")
        await bot.close()  # closes connections cleanly
        try:
            backup_all_dbs_to_gdrive_env([
                (ECONOMY_DB_PATH, "economy.db"),
                (MODERATOR_DB_PATH, "moderator.db"),
            ], BACKUP_FOLDER_ID)
            log.info("Final backup complete.")
        except Exception as e:
            log.error(f"Backup on exit failed: {e}")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        log.exception(f"Fatal crash: {e}")
