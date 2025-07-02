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
        "Disk Read (TOTAL)": f"{disk.read_bytes / (1024 ** 2):.2f} MB",  # Convert to MB
        "Disk Write (TOTAL)": f"{disk.write_bytes / (1024 ** 2):.2f} MB"  # Convert to MB
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

# Sync commands with Discord
# Activities list moved to global scope so it can be used in multiple functions
activities = [
    #  Games
    discord.Game("with equations"),
    discord.Game("with solutions"),
    discord.Game("with molecules"),
    discord.Game("acid roulette"),
    discord.Game("with noble gases"),
    discord.Game("Finding Avogadro's number: 6.02214076e23"),
    discord.Game("with unstable isotopes"),
    discord.Game("hide and seek with electrons"),
    discord.Game("on the Bunsen burner"),
    discord.Game("molecular tag"),
    discord.Game("with questionable solvents"),
    discord.Game("chemistry but it's in base 16"),
    discord.Game("with Schrödinger's keyboard"),
    discord.Game("in the lab... unsupervised"),
    discord.Game("with forbidden compounds"),
    discord.Game("with polyatomic sadness"),
    discord.Game("with toxic bonding"),
    discord.Game("Minecraft but it's stoichiometric"),
    discord.Game("Portal 3: Chemical Edition"),
    discord.Game("Factorio: Meth Lab DLC"),
    discord.Game("breaking bad (educational edition)"),
    discord.Game("noble gas party simulator"),

    #  Listening
    discord.Activity(type=discord.ActivityType.listening, name="the periodic table song"),
    discord.Activity(type=discord.ActivityType.listening, name="chemistry facts"),
    discord.Activity(type=discord.ActivityType.listening, name="user hypotheses"),
    discord.Activity(type=discord.ActivityType.listening, name="about stoichiometry lectures"),
    discord.Activity(type=discord.ActivityType.listening, name="bubbling beakers"),
    discord.Activity(type=discord.ActivityType.listening, name="endothermic reactions"),
    discord.Activity(type=discord.ActivityType.listening, name="uranium humming"),
    discord.Activity(type=discord.ActivityType.listening, name="complaints about the mole concept"),
    discord.Activity(type=discord.ActivityType.listening, name="lab goggles fog up"),
    discord.Activity(type=discord.ActivityType.listening, name="theoretical screams"),
    discord.Activity(type=discord.ActivityType.listening, name="periodic table diss tracks"),
    discord.Activity(type=discord.ActivityType.listening, name="the sound of atoms bonding"),
    discord.Activity(type=discord.ActivityType.listening, name="the sound of a lab explosion"),
    discord.Activity(type=discord.ActivityType.listening, name="the sound of a chemical spill"),
    discord.Activity(type=discord.ActivityType.listening, name="the sound of a Bunsen burner"),
    discord.Activity(type=discord.ActivityType.listening, name="the sound of a chemical reaction"),
    discord.Activity(type=discord.ActivityType.listening, name="the sound of a lab accident"),

    #  Watching
    discord.Activity(type=discord.ActivityType.watching, name="chemical reactions"),
    discord.Activity(type=discord.ActivityType.watching, name="atoms collide"),
    discord.Activity(type=discord.ActivityType.watching, name="a lab safety video"),
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
    discord.Activity(type=discord.ActivityType.watching, name="the universe expand"),
    discord.Activity(type=discord.ActivityType.watching, name="the chemical bonds break"),
    discord.Activity(type=discord.ActivityType.watching, name="the lab rats escape"),
    discord.Activity(type=discord.ActivityType.watching, name="the lab spontaneously combust"),
]

@bot.event
async def on_ready():
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

async def resource_monitor():
    await bot.wait_until_ready()
    while not bot.is_closed():
        stats = get_bot_stats()
        print(f"Bot Resource Usage: {stats}")
        await asyncio.sleep(45)

async def cycle_paired_activities():
    await bot.wait_until_ready()
    while not bot.is_closed():
        # A = Playing... B = Listening to... C = Watching...
        combo_type = random.choice(["A", "B", "C", "A+B", "A+C", "B+C", "B+A", "C+A", "C+B"])
        if combo_type == "A":
            game = random.choice([a for a in activities if isinstance(a, discord.Game)])
            status = random.choice([discord.Status.online, discord.Status.idle, discord.Status.dnd])
            await bot.change_presence(activity=game, status=status)
        elif combo_type == "B":
            listening = random.choice([a for a in activities if isinstance(a, discord.Activity) and a.type == discord.ActivityType.listening])
            status = random.choice([discord.Status.online, discord.Status.idle, discord.Status.dnd])
            await bot.change_presence(activity=listening, status=status)
        elif combo_type == "C":
            watching = random.choice([a for a in activities if isinstance(a, discord.Activity) and a.type == discord.ActivityType.watching])
            status = random.choice([discord.Status.online, discord.Status.idle, discord.Status.dnd])
            await bot.change_presence(activity=watching, status=status)
        elif combo_type == "A+B":
            game = random.choice([a for a in activities if isinstance(a, discord.Game)])
            listening = random.choice([a for a in activities if isinstance(a, discord.Activity) and a.type == discord.ActivityType.listening])
            combined_name = f"{game.name} & listening {listening.name}"
            combined_activity = discord.Game(combined_name)
            status = random.choice([discord.Status.online, discord.Status.idle, discord.Status.dnd])
            await bot.change_presence(activity=combined_activity, status=status)
        elif combo_type == "A+C":
            game = random.choice([a for a in activities if isinstance(a, discord.Game)])
            watching = random.choice([a for a in activities if isinstance(a, discord.Activity) and a.type == discord.ActivityType.watching])
            combined_name = f"{game.name} & watching {watching.name}"
            combined_activity = discord.Game(combined_name)
            status = random.choice([discord.Status.online, discord.Status.idle, discord.Status.dnd])
            await bot.change_presence(activity=combined_activity, status=status)
        elif combo_type == "B+A":
            listening = random.choice([a for a in activities if isinstance(a, discord.Activity) and a.type == discord.ActivityType.listening])
            game = random.choice([a for a in activities if isinstance(a, discord.Game)])
            combined_name = f"{listening.name} & playing {game.name}"
            combined_activity = discord.Activity(type=discord.ActivityType.listening, name=combined_name)
            status = random.choice([discord.Status.online, discord.Status.idle, discord.Status.dnd])
            await bot.change_presence(activity=combined_activity, status=status)
        elif combo_type == "B+C":
            listening = random.choice([a for a in activities if isinstance(a, discord.Activity) and a.type == discord.ActivityType.listening])
            watching = random.choice([a for a in activities if isinstance(a, discord.Activity) and a.type == discord.ActivityType.watching])
            combined_name = f"{listening.name} & watching {watching.name}"
            combined_activity = discord.Activity(type=discord.ActivityType.listening, name=combined_name)
            status = random.choice([discord.Status.online, discord.Status.idle, discord.Status.dnd])
            await bot.change_presence(activity=combined_activity, status=status)
        elif combo_type == "C+A":
            watching = random.choice([a for a in activities if isinstance(a, discord.Activity) and a.type == discord.ActivityType.watching])
            game = random.choice([a for a in activities if isinstance(a, discord.Game)])
            combined_name = f"{watching.name} & playing {game.name}"
            combined_activity = discord.Activity(type=discord.ActivityType.watching, name=combined_name)
            status = random.choice([discord.Status.online, discord.Status.idle, discord.Status.dnd])
            await bot.change_presence(activity=combined_activity, status=status)
        elif combo_type == "C+B":
            watching = random.choice([a for a in activities if isinstance(a, discord.Activity) and a.type == discord.ActivityType.watching])
            listening = random.choice([a for a in activities if isinstance(a, discord.Activity) and a.type == discord.ActivityType.listening])
            combined_name = f"{watching.name} & listening {listening.name}"
            combined_activity = discord.Activity(type=discord.ActivityType.watching, name=combined_name)
            status = random.choice([discord.Status.online, discord.Status.idle, discord.Status.dnd])
            await bot.change_presence(activity=combined_activity, status=status)
        await asyncio.sleep(300)  # 5 minutes

async def unmute_task(self):
    await bot.wait_until_ready()
    while not bot.is_closed():
        now = int(time.time())
        for guild in bot.guilds:
            expired_mutes = get_expired_cases(mod_cursor, guild.id, "mute", now)
            for case_number, user_id in expired_mutes:
                guild_obj = bot.get_guild(guild.id)
                if guild_obj:
                    member = guild_obj.get_member(user_id)
                    mute_role = discord.utils.get(guild_obj.roles, name="Muted")
                    if member and mute_role:
                        try:
                            await member.remove_roles(mute_role, reason="Mute duration expired")
                        except Exception as e:
                            logging.error(f"Failed to unmute {member}: {e}")
        logging.info(f"Unmute task run at {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(now))}")
        await asyncio.sleep(60)

async def unban_task(self):
    await bot.wait_until_ready()
    while not bot.is_closed():
        now = int(time.time())
        for guild in bot.guilds:
            expired_bans = get_expired_cases(mod_cursor, guild.id, "ban", now)
            for case_number, user_id in expired_bans:
                guild_obj = bot.get_guild(guild.id)
                if guild_obj:
                    try:
                        user = await bot.fetch_user(user_id)
                        await guild_obj.unban(user, reason="Temporary ban expired")
                    except Exception as e:
                        logging.error(f"Failed to unban {user_id} in {guild.id}: {e}")
        logging.info(f"Unban task run at {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(now))}")
        await asyncio.sleep(60)

async def main():
    async with bot:
        asyncio.create_task(resource_monitor()) # Monitors resources
        asyncio.create_task(cycle_paired_activities()) # Appens & cycles activities
        asyncio.create_task(unmute_task(bot)) # Unmutes users after mute duration expires
        asyncio.create_task(unban_task(bot)) # Unbans users after ban duration expires
        bot.tree.interaction_check = global_blacklist_check # Global blacklist check for guilds from config.py
        await bot.start(config.BOT_TOKEN) # Starts the bot with the token from config.py

# Run the bot
asyncio.run(main())
# the coconut.png of the bot