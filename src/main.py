import discord
from discord.ext import commands
from discord import Interaction, app_commands
from discord.app_commands import CheckFailure
import config, asyncio, random, sys, logging, socket, aiohttp, os, psutil, time
from database import get_expired_cases, mod_cursor

process = psutil.Process(os.getpid())

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
    cpu = psutil.cpu_percent(interval=None) # we are not a vps, blocking cpu time = bad
    disk = process.io_counters()

    return {
        "Memory (RSS)": f"{mem.rss / (1024 ** 2):.2f} MB",  # Convert to MB
        "CPU Usage": f"{cpu:.2f}%",
        "Disk Read": f"{disk.read_bytes / (1024 ** 2):.2f} MB",  # Convert to MB
        "Disk Write": f"{disk.write_bytes / (1024 ** 2):.2f} MB"  # Convert to MB
    }


# Define the Main bot class
class Main(commands.Bot):
    def __init__(self, *args, **kwargs):
        super().__init__(command_prefix="!", intents=intents, *args, **kwargs)
        self.user_id = bot_owner

    async def setup_hook(self):
        # Load all cogs
        for filename in os.listdir("./commands"):
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
                else:
                    await self.load_extension(cog_path)
                    await interaction.response.send_message(f"📥 Loaded new cog `{cog_name}` successfully.", ephemeral=True)
            except commands.NoEntryPointError:
                await interaction.response.send_message(f"❌ Cog `{cog_name}` is missing a `setup()` function.", ephemeral=True)
            except commands.ExtensionFailed as e:
                await interaction.response.send_message(f"❌ Failed to load `{cog_name}`: {e}", ephemeral=True)

        self.tree.add_command(app_commands.Command(
            name="reload",
            description="Reloads a specific cog.",
            callback=reload
        ))

        await self.tree.sync()

# Instantiate your bot
bot = Main()

# Sync commands with Discord
# Activities list moved to global scope so it can be used in multiple functions
activities = [
    #  Games
    discord.Game("balancing equations ⚖️"),
    discord.Game("mixing solutions 🧪"),
    discord.Game("with molecules 🧬"),
    discord.Game("titrating acids and bases"),
    discord.Game("with noble gases"),
    discord.Game("finding Avogadro's number"),
    discord.Game("with unstable isotopes ☢️"),
    discord.Game("hide and seek with electrons"),
    discord.Game("on the Bunsen burner 🔥"),
    discord.Game("molecular tag 🏷️"),
    discord.Game("with questionable solvents"),
    discord.Game("chemistry but it's in base 16"),
    discord.Game("with Schrödinger’s keyboard"),
    discord.Game("in the lab... unsupervised"),
    discord.Game("with forbidden compounds"),
    discord.Game("with polyatomic sadness 😢"),
    discord.Game("toxic bonding"),
    discord.Game("Minecraft but it's stoichiometric"),
    discord.Game("Portal 3: Chemical Edition"),
    discord.Game("Factorio: Meth Lab DLC"),
    discord.Game("breaking bad (educational edition)"),
    discord.Game("gacha pulling for noble gases"),

    #  Listening
    discord.Activity(type=discord.ActivityType.listening, name="to the periodic table song"),
    discord.Activity(type=discord.ActivityType.listening, name="to chemistry facts"),
    discord.Activity(type=discord.ActivityType.listening, name="to user hypotheses"),
    discord.Activity(type=discord.ActivityType.listening, name="about stoichiometry lectures"),
    discord.Activity(type=discord.ActivityType.listening, name="to bubbling beakers"),
    discord.Activity(type=discord.ActivityType.listening, name="for endothermic reactions"),
    discord.Activity(type=discord.ActivityType.listening, name="to uranium humming"),
    discord.Activity(type=discord.ActivityType.listening, name="to complaints about the mole concept"),
    discord.Activity(type=discord.ActivityType.listening, name="to lab goggles fog up"),
    discord.Activity(type=discord.ActivityType.listening, name="to theoretical screams"),
    discord.Activity(type=discord.ActivityType.listening, name="to periodic table diss tracks"),

    #  Watching
    discord.Activity(type=discord.ActivityType.watching, name="chemical reactions"),
    discord.Activity(type=discord.ActivityType.watching, name="atoms collide"),
    discord.Activity(type=discord.ActivityType.watching, name="the lab safety video"),
    discord.Activity(type=discord.ActivityType.watching, name="crystals grow"),
    discord.Activity(type=discord.ActivityType.watching, name="the periodic table rearrange itself"),
    discord.Activity(type=discord.ActivityType.watching, name="the flask boil over"),
    discord.Activity(type=discord.ActivityType.watching, name="ionic drama unfold"),
    discord.Activity(type=discord.ActivityType.watching, name="thermodynamics take a nap"),
    discord.Activity(type=discord.ActivityType.watching, name="carbon date badly"),
    discord.Activity(type=discord.ActivityType.watching, name="users ignore lab safety"),
    discord.Activity(type=discord.ActivityType.watching, name="moles commit tax fraud"),
    discord.Activity(type=discord.ActivityType.watching, name="the periodic table change"),
    discord.Activity(type=discord.ActivityType.watching, name="the lab explode"),
]

@bot.event
async def on_ready():
    # Pick a random activity
    activity = random.choice(activities)
    # Optionally, set status to mobile (fake it by using 'idle' or 'dnd', true "mobile" is not officially supported)
    status = random.choice([discord.Status.online, discord.Status.idle, discord.Status.dnd])
    await bot.change_presence(activity=activity, status=status)
    await bot.tree.sync()
    print("Commands synced!")

async def global_blacklist_check(interaction: Interaction) -> bool:
    guild_id = interaction.guild.id if interaction.guild else None
    if guild_id in config.FORBIDDEN_GUILDS:
        reason = config.FORBIDDEN_GUILDS[guild_id]["reason"]
        if reason == "N/a" or reason == "No reason":
            reason = "No specific reason provided."
        await interaction.response.send_message(f"**This server is not allowed to use this bot.**\n**Reason:** {reason}", ephemeral=False)
        raise CheckFailure("Forbidden guild")
    return True

# Run the bot
async def resource_monitor():
    await bot.wait_until_ready()
    while not bot.is_closed():
        stats = get_bot_stats()
        print(f"Bot Resource Usage: {stats}")
        await asyncio.sleep(45)

async def cycle_paired_activities():
    await bot.wait_until_ready()
    while not bot.is_closed():
        game = random.choice([a for a in activities if isinstance(a, discord.Game)])
        listening = random.choice([a for a in activities if isinstance(a, discord.Activity) and a.type == discord.ActivityType.listening])
        # Combine their names for a fun effect
        combined_name = f"{game.name} & {listening.name}"
        combined_activity = discord.Game(combined_name)
        status = random.choice([discord.Status.online, discord.Status.idle, discord.Status.dnd])
        await bot.change_presence(activity=combined_activity, status=status)
        await asyncio.sleep(600)  # 10 minutes

async def unmute_task(self):
    await self.bot.wait_until_ready()
    while not self.bot.is_closed():
        now = int(time.time())
        expired_mutes = get_expired_cases(mod_cursor, "mute", now)
        for guild_id, user_id in expired_mutes:
            guild = self.bot.get_guild(guild_id)
            if guild:
                member = guild.get_member(user_id)
                mute_role = discord.utils.get(guild.roles, name="Muted")
                if member and mute_role:
                    try:
                        await member.remove_roles(mute_role, reason="Mute duration expired")
                    except Exception as e:
                        logging.error(f"Failed to unmute {member}: {e}")
        await asyncio.sleep(60)

async def unban_task(self):
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            now = int(time.time())
            expired_bans = get_expired_cases(mod_cursor, "ban", now)
            for guild_id, user_id in expired_bans:
                guild = self.bot.get_guild(guild_id)
                if guild:
                    try:
                        user = await self.bot.fetch_user(user_id)
                        await guild.unban(user, reason="Temporary ban expired")
                    except Exception as e:
                        logging.error(f"Failed to unban {user_id} in {guild_id}: {e}")
            await asyncio.sleep(60)

async def main():
    async with bot:
        asyncio.create_task(resource_monitor())
        asyncio.create_task(cycle_paired_activities())
        asyncio.create_task(unmute_task(bot))
        asyncio.create_task(unban_task(bot))
        bot.tree.interaction_check = global_blacklist_check
        await bot.start(config.BOT_TOKEN)

asyncio.run(main())
