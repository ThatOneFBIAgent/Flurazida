# Secrets & others (geeneral config)
# config.py
import os, asyncio
from discord import Interaction
from dotenv import load_dotenv
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '..', '.env', '.env'))
BOT_TOKEN = os.getenv("BOT_TOKEN")
BOT_OWNER = 853154444850364417
FORBIDDEN_GUILDS = {
    1368777209375883405: {"reason": "Have fun with cat bot"},
    1375886954889085088: {"reason": "N/a"}
}

# look at the dumma code in main.py for the rest of the config
# This file is used to store configuration settings for the bot.
# it does not mean you'll get my bot's token, i'm not that dumb
# and you shouldn't be either.

# Do not host config.py with raw info on your repository, instead use environment variables or a secure vault, or just host it on your pc.

# still doesn't stop me from putting code here.
 
import time, discord
from functools import wraps
from discord import Interaction

# user id: cooldown
_user_cooldowns = {}

def cooldown(seconds: int):
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            # If this is a method, args[0] is self, args[1] is interaction
            # If this is a function, args[0] is interaction
            if isinstance(args[0], Interaction):
                interaction = args[0]
                rest_args = args[1:]
            else:
                interaction = args[1]
                rest_args = args[2:]

            user_id = interaction.user.id
            now = time.time()

            if user_id in _user_cooldowns:
                elapsed = now - _user_cooldowns[user_id]
                if elapsed < seconds:
                    print(f"Cooldown triggered for user {user_id}")
                    await interaction.response.send_message(
                        f"🕒 You're on cooldown! Try again in {round(seconds - elapsed, 1)}s.",
                        ephemeral=True
                    )
                    return
            _user_cooldowns[user_id] = now
            return await func(*args, **kwargs)
        return wrapper
    return decorator

# Cooldown wrapper, at any point in any code for commands you can do @cooldown(int) AFTER @app_command.commands such as:
# @app_commands.command(name="example", description="An example command")
# @cooldown(10)  # 10 seconds cooldown
# (rest of code)...
# Calculated in seconds. 1m = 60s, 10m = 600s, 1h = 3600s

ACITIVIES = [
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