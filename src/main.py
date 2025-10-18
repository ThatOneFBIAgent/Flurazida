#    "Flurazide" - A more than basic Discord bot with database functions
#    Copyright (C) 2025  Iza Carlos (Aka Carlos E.)

#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU Affero General Public License as published by
#    the Free Software Foundation, either version 3 of the License, or
#    (at your option) any later version.

#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU Affero General Public License for more details.

#    You should have received a copy of the GNU Affero General Public License
#    along with this program.  If not, see <https://www.gnu.org/licenses/>.

import discord
from discord.ext import commands
from discord import Interaction, app_commands
from discord.app_commands import CheckFailure
from googleapiclient.http import MediaIoBaseDownload
from googleapiclient.http import MediaIoBaseUpload
from google.oauth2 import service_account
from googleapiclient.discovery import build
import config, asyncio, random, sys, logging, socket, aiohttp, os, psutil, time, signal, io
from database import get_expired_cases, mod_cursor, periodic_backup, restore_db_from_gdrive_env, ECONOMY_DB_PATH, MODERATOR_DB_PATH, BACKUP_FOLDER_ID

process = psutil.Process(os.getpid())
last_activity_signature = None
activities = config.ACITIVIES

# Intents & Bot Setup
intents = discord.Intents.default()
intents.messages = True
intents.guilds = True
intents.message_content = True
intents.members = True
bot_owner = config.BOT_OWNER

# Bot CPU/DISK/MEMORY usage
def get_bot_stats():
    mem = process.memory_info()
    cpu = process.cpu_percent(interval=None)
    disk = process.io_counters()

    return {
        "Memory (RSS)": f"{mem.rss / (1024 ** 2):.2f} MB",  # Convert to MB
        "CPU Usage": f"{cpu:.2f}%",
        "Disk Read (TOTAL)": f"{disk.read_bytes / (1024 ** 2):.2f} MB",  # Convert to MB
        "Disk Write (TOTAL)": f"{disk.write_bytes / (1024 ** 2):.2f} MB"  # Convert to MB
    }

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

# Define the Main bot class
class Main(commands.Bot):
    def __init__(self, *args, **kwargs):
        super().__init__(command_prefix="!", intents=intents, *args, **kwargs)
        self.user_id = bot_owner

    async def setup_hook(self):
        # Load all cogs from same folder as this file
        commands_dir = os.path.join(os.path.dirname(__file__), "commands")
        for filename in os.listdir(commands_dir):
            if filename.endswith(".py"):
                await self.load_extension(f"commands.{filename[:-3]}")

        # Register slash command
        async def reload(interaction: discord.Interaction, cog_name: str):
            if interaction.user.id != self.user_id:
                return await interaction.response.send_message("❌ You do not have permission to use this command.", ephemeral=True)

            cog_name = cog_name.lower()
            cog_path = f"commands.{cog_name}"
            cog_file = f"./commands/{cog_name}.py"

            # Make sure the file exists
            if not os.path.exists(cog_file):
                return await interaction.response.send_message(f"❌ The cog `{cog_name}` does not exist as a file.", ephemeral=True)

            try:
                if cog_path in self.extensions:
                    await self.reload_extension(cog_path)
                    await interaction.response.send_message(f"🔁 Reloaded `{cog_name}` successfully.", ephemeral=True)
                    print(f"Reloaded cog: {cog_name}")
                else:
                    await self.load_extension(cog_path)
                    await interaction.response.send_message(f"📥 Loaded new cog `{cog_name}` successfully.", ephemeral=True)
                    print(f"Loaded new cog: {cog_name}")
            except commands.NoEntryPointError:
                await interaction.response.send_message(f"❌ Cog `{cog_name}` is missing a `setup()` function.", ephemeral=True)
                print(f"Failed to load cog: {cog_name} - No setup function found. Maybe add `async def setup(bot): await bot.add_cog(CogName(bot))` in the cog file?")
            except commands.ExtensionFailed as e:
                await interaction.response.send_message(f"❌ Failed to load `{cog_name}`: {e}", ephemeral=True)
                print(f"Failed to load cog: {cog_name} - {e}")

        self.tree.add_command(app_commands.Command(
            name="reload",
            description="Reloads a specific cog.",
            callback=reload
        ))

        await self.tree.sync()

# Instantiate your bot
bot = Main()
bot.help_command = None

# Sync commands with Discord
@bot.event
async def on_ready():
    if not hasattr(bot, "start_time"):
        bot.start_time = time.time()
    await bot.tree.sync()
    print("Commands synced!")
    print(f"Bot is online as {bot.user} (ID: {bot.user.id})")
    print(f"Connected to {len(bot.guilds)} guild(s). Serving {sum(g.member_count for g in bot.guilds)} user(s).")

    # restore from backup, if not write to gdrive backup (scary!)

@bot.event
async def on_message(message):
    if message.author.bot:
        return
    
    bot_name = str(bot.user.name).lower()
    if bot_name in message.content.lower():
        emojis = ["🧪"]

        for emoji in emojis:
            try:
                await message.add_reaction(emoji)
            except Exception as e:
                logging.warning(f"Failed to add reaction {emoji}: {e}")
    
    await bot.process_commands(message)

async def global_blacklist_check(interaction: Interaction) -> bool:
    guild_id = interaction.guild.id if interaction.guild else None
    if guild_id in config.FORBIDDEN_GUILDS:
        reason = config.FORBIDDEN_GUILDS[guild_id]["reason"]
        if reason == "N/a" or reason == "No reason":
            reason = "No specific reason provided."
        await interaction.response.send_message(f"**This server is not allowed to use this bot.**\n**Reason:** {reason}", ephemeral=False)
        raise CheckFailure("Forbidden guild")
    return True

async def resource_monitor():
    await bot.wait_until_ready()
    while not bot.is_closed():
        stats = get_bot_stats()
        print(f"Bot Resource Usage: {stats}")
        await asyncio.sleep(60)

async def cycle_paired_activities():
    global last_activity_signature
    global total_unique_combos
    # Calculate total unique combos based on actual combination logic (single and ordered pairs)
    num_games = len([a for a in activities if isinstance(a, discord.Game)])
    num_listen = len([a for a in activities if isinstance(a, discord.Activity) and a.type == discord.ActivityType.listening])
    num_watch = len([a for a in activities if isinstance(a, discord.Activity) and a.type == discord.ActivityType.watching])
    total_unique_combos = (
        num_games + num_listen + num_watch +  # singles
        num_games * num_listen +              # A+B, B+A
        num_games * num_watch +               # A+C, C+A
        num_listen * num_watch +              # B+C, C+B
        num_listen * num_games +              # B+A
        num_watch * num_games +               # C+A
        num_watch * num_listen                # C+B
    )
    await bot.wait_until_ready()
    while not bot.is_closed():
        for _ in range(10): # Try up to 10 times to choose a unique activity
            # A = Playing... B = Listening to... C = Watching...
            combo_type = random.choice(["A", "B", "C", "A+B", "A+C", "B+C", "B+A", "C+A", "C+B"])
            status = random.choice([discord.Status.online, discord.Status.idle, discord.Status.dnd])
            
            game = lambda: random.choice([a for a in activities if isinstance(a, discord.Game)])
            listen = lambda: random.choice([a for a in activities if isinstance(a, discord.Activity) and a.type == discord.ActivityType.listening])
            watch = lambda: random.choice([a for a in activities if isinstance(a, discord.Activity) and a.type == discord.ActivityType.watching])

            activity = None
            combined_activity = None

            if combo_type == "A":
                activity = game()
            elif combo_type == "B":
                activity = listen()
            elif combo_type == "C":
                activity = watch()
            elif combo_type == "A+B":
                combined_activity = discord.Game(f"{game().name} & listening to {listen().name}")
            elif combo_type == "A+C":
                combined_activity = discord.Game(f"{game().name} & watching {watch().name}")
            elif combo_type == "B+A":
                combined_activity = discord.Activity(type=discord.ActivityType.listening, name=f"{listen().name} & playing {game().name}")
            elif combo_type == "B+C":
                combined_activity = discord.Activity(type=discord.ActivityType.listening, name=f"{listen().name} & watching {watch().name}")
            elif combo_type == "C+A":
                combined_activity = discord.Activity(type=discord.ActivityType.watching, name=f"{watch().name} & playing {game().name}")
            elif combo_type == "C+B":
                combined_activity = discord.Activity(type=discord.ActivityType.watching, name=f"{watch().name} & listening to {listen().name}")
            
            act = combined_activity if combined_activity else activity
            activity_signature = (act.name, act.type)

            if activity_signature != last_activity_signature:
                last_activity_signature = activity_signature
                await bot.change_presence(activity=act, status=status)
                break
        else:
            # Fallback if same activity 10x in a row
            fallback_name = f"One in a {total_unique_combos}"
            fallback_activity = discord.Game(name=fallback_name)
            fallback_status = discord.Status.idle
            last_activity_signature = (fallback_activity.name, fallback_activity.type)
            await bot.change_presence(activity=fallback_activity, status=fallback_status)
        
        await asyncio.sleep(900)  # Wait 15 mins before cycling again

async def moderation_expiry_task():
    await bot.wait_until_ready()
    while not bot.is_closed():
        now = int(time.time())
        # Unmute expired users
        for guild in bot.guilds:
            expired_mutes = get_expired_cases(mod_cursor, guild.id, "mute", now)
            for _, user_id in expired_mutes:
                guild_obj = bot.get_guild(guild.id)
                if guild_obj:
                    member = guild_obj.get_member(user_id)
                    mute_role = discord.utils.get(guild_obj.roles, name="Muted")
                    if member and mute_role:
                        try:
                            await member.remove_roles(mute_role, reason="Mute duration expired")
                        except Exception as e:
                            logging.error(f"Failed to unmute {member}: {e}")
        # Unban expired users
        for guild in bot.guilds:
            expired_bans = get_expired_cases(mod_cursor, guild.id, "ban", now)
            for _, user_id in expired_bans:
                guild_obj = bot.get_guild(guild.id)
                if guild_obj:
                    try:
                        user = await bot.fetch_user(user_id)
                        await guild_obj.unban(user, reason="Temporary ban expired")
                    except Exception as e:
                        logging.error(f"Failed to unban {user_id} in {guild.id}: {e}")
        logging.info(f"Moderation expiry task run at {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(now))}")
        await asyncio.sleep(60)

async def main():

    # restore from drive (use the env-version that accepts drive filename)
    restore_db_from_gdrive_env(MODERATOR_DB_PATH, "moderator.db", BACKUP_FOLDER_ID)
    restore_db_from_gdrive_env(ECONOMY_DB_PATH, "economy.db", BACKUP_FOLDER_ID)

    # Start bot + background tasks
    async with bot:
#       asyncio.create_task(resource_monitor())
        asyncio.create_task(cycle_paired_activities())
        asyncio.create_task(moderation_expiry_task())
        asyncio.create_task(periodic_backup(1))  # Backup every 1 hour (adjust)
        bot.tree.interaction_check = global_blacklist_check

        await bot.start(config.BOT_TOKEN)

try:
    asyncio.run(main())
except aiohttp.ClientConnectorError as e:
    if isinstance(e.os_error, socket.gaierror):
        print(f"Detected socket.gaierror inside ClientConnectorError: {e.os_error}")
    else:
        print(f"Bad chemicals! ClientConnectorError: {e}")

    if wait_for_continue():
        os.execv(sys.executable, [sys.executable] + sys.argv)
    else:
        sys.exit(1)

except (
    aiohttp.ClientConnectionError,
    aiohttp.ClientOSError,
    aiohttp.ServerDisconnectedError,
    discord.ConnectionClosed,
    discord.GatewayNotFound,
    asyncio.TimeoutError
) as e:
    print(f"Bad lab practitioner! Connection error: {e}")
    if wait_for_continue():
        os.execv(sys.executable, [sys.executable] + sys.argv)
    else:
        sys.exit(1)

except KeyboardInterrupt:
    print("Cleaning the lab.. Exiting gracefully.")
except Exception as e:
    print(f"Wrong chemicals! Bot crashed with exception: {e}")
# the coconut.png of the bot
