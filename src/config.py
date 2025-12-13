# Secrets & others (geeneral config)
# config.py

# Standard Library Imports
import asyncio
import functools
import inspect
import logging
import os
import time
import random
from functools import wraps
from typing import Callable, Optional

# Third-Party Imports
import discord
from discord import File, Interaction, Game, Activity, ActivityType
from dotenv import load_dotenv

# Ensure we import get_logger used below
from logger import get_logger
from extraconfig import (
    ALPHA,
    BOT_OWNER,
    BACKUP_GDRIVE_FOLDER_ID,
    TEST_SERVER,
    FORBIDDEN_GUILDS,
    FORBIDDEN_USERS,
)

# Correctly load .env from project root's .env folder (project_root/.env/.env)
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env", ".env"))

IS_ALPHA = ALPHA  # Set to False when you're ready to switch to the main bot


if IS_ALPHA:
    BOT_TOKEN = os.getenv("BOT_TOKEN_ALPHA")
else:
    BOT_TOKEN = os.getenv("BOT_TOKEN")

LAVALINK_HOST = os.getenv("LAVALINK_HOST", "localhost")
LAVALINK_PORT = os.getenv("LAVALINK_PORT", 2333)
LAVALINK_PASSWORD = os.getenv("LAVALINK_PASSWORD", "youshallnotpass")

# This file's purpose is to store basic configuration (see extraconfig.py for more) and the cooldown decorator.
# Do not, and i implore, do not commit your discord bot token to a public or even private repository i would argue.
# Discord WILL reset your token if they find it online, or people use it maliciously.
# still doesn't stop me from putting code here.

log = get_logger()
log.setLevel(logging.EVENT_LEVEL)
log.trace("Config module initialized")

# Console handler
ch = logging.StreamHandler()
ch.setLevel(logging.EVENT_LEVEL)
formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
ch.setFormatter(formatter)
log.addHandler(ch)

# File handler for errors
fh = logging.FileHandler("errors.log", encoding="utf-8")
fh.setLevel(logging.EVENT_LEVEL)
fh.setFormatter(formatter)
log.addHandler(fh)

# Use a dict to store cooldowns: {(user_id, command_name): timestamp}
_user_command_cooldowns = {}
_command_failures = {}

# reason we use equals to all three is so even if we forget one it still works with defaults, although that "none" error is annoying
def cooldown(*, cl: int = 0, tm: float = None, ft: int = 3, nw: bool = False):
    """
    Adds cooldown, timeout, and failure tracking to a command.
    When a user repeatedly fails a command, the owner gets a DM with logs.
    NSFW commands will be blocked if they are not in an NSFW channel.
    Args:
    - cl: cooldown in seconds between uses per user (0 = no cooldown)
    - tm: timeout in seconds for command execution (None = no timeout)
    - ft: failure threshold before alerting owner/user (3 = default)
    - nw: check if command is nsfw (default: False)
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            # detect interaction
            if isinstance(args[0], Interaction):
                interaction = args[0]
            else:
                interaction = args[1]

            user_id = interaction.user.id
            command_name = func.__name__
            key = (user_id, command_name)
            now = time.time()

            # --- cooldown check ---
            if cl > 0 and key in _user_command_cooldowns:
                elapsed = now - _user_command_cooldowns[key]
                if elapsed < cl:
                    await interaction.response.send_message(
                        f"🕒 That command's on cooldown! Try again in {round(cl - elapsed, 1)}s.",
                        ephemeral=True,
                    )
                    log.warningtrace(f"[Cooldown] {command_name} by {user_id} (wait {round(cl - elapsed, 1)}s)")
                    return

            _user_command_cooldowns[key] = now

            # --- main run + timeout ---
            if nw:
                if interaction.channel.type == discord.ChannelType.text and interaction.channel.is_nsfw():
                    await interaction.response.send_message(
                        "🔞 This command is not allowed in non-NSFW channels.",
                        ephemeral=True,
                    )
                    return

            try:
                if tm:
                    result = await asyncio.wait_for(func(*args, **kwargs), timeout=tm)
                else:
                    result = await func(*args, **kwargs)

                _command_failures[key] = 0
                log.successtrace(f"[CommandSuccess] {command_name} executed by {user_id}")
                return result

            except asyncio.TimeoutError:
                msg = f"⏰ Command took too long ({tm}s limit reached)."
                log.warning(f"[Timeout] {command_name} by {user_id} exceeded {tm}s")
                await _handle_failure(interaction, key, msg, ft, None)

            except Exception as e:
                msg = f"💥 Something went wrong:\n```{e}```"
                log.exception(f"[CommandError] {command_name} failed for {user_id}: {e}")
                await _handle_failure(interaction, key, msg, ft, e)

        return wrapper
    return decorator


async def _handle_failure(interaction: Interaction, key: tuple, message: str,
                          ft: int, exc: Exception | None):
    """Increment failure count, notify user, and optionally DM owner."""
    user_id, command_name = key
    _command_failures[key] = _command_failures.get(key, 0) + 1
    count = _command_failures[key]

    # when threshold hit, reset and alert owner
    if count >= ft:
        message += "\n\n⚠️ **Found a bug? Report it to the developer!**"
        _command_failures[key] = 0
        await _alert_owner(interaction, command_name, exc)

    # send error to user
    try:
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
    except Exception as send_err:
        log.error(f"[ErrorSendFail] Could not send failure message: {send_err}")


async def _alert_owner(interaction: Interaction, command_name: str, exc: Exception | None):
    """Send a DM with recent log excerpt to the owner."""
    try:
        owner = await interaction.client.fetch_user(BOT_OWNER)
    except Exception as e:
        log.error(f"[OwnerFetchError] {e}")
        return

    try:
        # build message
        text = f"⚠️ **Command failure threshold reached!**\n"
        text += f"Command: `{command_name}`\n"
        text += f"Guild: `{interaction.guild.name if interaction.guild else 'DM'}`\n"
        text += f"User: `{interaction.user} ({interaction.user.id})`\n"
        if exc:
            text += f"Latest exception: `{exc}`\n"

        # attach latest log file (only last 100 lines to avoid huge file)
        if os.path.exists("errors.log"):
            with open("errors.log", "r", encoding="utf-8") as f:
                lines = f.readlines()
            recent = "".join(lines[-100:])  # last 100 lines
            with open("errors_excerpt.log", "w", encoding="utf-8") as f:
                f.write(recent)
            await owner.send(content=text, file=File("errors_excerpt.log"))
        else:
            await owner.send(content=text + "\n⚠️ No log file found to attach.")

    except Exception as e:
        log.error(f"[OwnerAlertFail] Could not DM owner: {e}")


# Bot activity list, add your own activities here, bot will randomly pick one every 10 minutes (also configurable in main.py)
ALLOW_DOUBLE_ACTIVITIES = False # Set to True to allow combinations of two activities
ACTIVITIES = [
    # Games (Playing ...)
    discord.Game("with equations"),
    discord.Game("with solutions"),
    discord.Game("with molecules"),
    discord.Game("acid roulette"),
    discord.Game("with noble gases"),
    discord.Game("finding Avogadro's number: 6.02214076e23"),
    discord.Game("with unstable isotopes"),
    discord.Game("hide and seek with electrons"),
    discord.Game("on the Bunsen burner"),
    discord.Game("molecular tag"),
    discord.Game("with questionable solvents"),
    discord.Game("chemistry in base 16"),
    discord.Game("with Schrödinger's keyboard"),
    discord.Game("in the lab... unsupervised"),
    discord.Game("with forbidden compounds"),
    discord.Game("with polyatomic sadness"),
    discord.Game("with toxic bonding"),
    discord.Game("Minecraft but stoichiometric"),
    discord.Game("Portal 3: Chemical Edition"),
    discord.Game("Factorio: Meth Lab DLC"),
    discord.Game("Breaking Bad (educational edition)"),
    discord.Game("noble gas party simulator"),
    discord.Game("with radioactive decay"),
    discord.Game("crystal hunting"),
    discord.Game("periodic table scavenger hunt"),
    discord.Game("with supercooled liquids"),
    discord.Game("atomic tag"),
    discord.Game("experiment roulette"),
    discord.Game("with reaction kinetics"),
    discord.Game("sublimation race"),
    discord.Game("pH meter challenge"),
    discord.Game("tracking Brownian motion in real time"),
    discord.Game("playing with organometallic catalysts"),
    discord.Game("aligning electron orbitals"),
    discord.Game("nuclear spin tag"),
    discord.Game("plotting a van't Hoff curve"),
    discord.Game("identifying lanthanides by flame color"),
    discord.Game("quantum tunneling hide-and-seek"),
    discord.Game("magnetizing ferrofluids"),
    discord.Game("watching cis-trans isomer races"),
    discord.Game("supercooling water without freezing"),
    discord.Game("attempting a Williamson ether synthesis"),
    discord.Game("balancing redox half-reactions in binary"),
    discord.Game("nucleophilic substitution speedrun"),
    discord.Game("tracking positron emissions"),
    discord.Game("playing with Mössbauer spectroscopy"),
    discord.Game("constructing molecular orbital diagrams"),
    discord.Game("titrating weak acids like a pro"),
    discord.Game("solving the Schrödinger equation for fun"),
    discord.Game("predicting UV-Vis absorption peaks"),
    discord.Game("simulating SN1 vs SN2 pathways"),
    discord.Game("watching benzene rings rotate"),
    discord.Game("isolating carbocations safely"),
    discord.Game("calculating Gibbs free energy for your lunch"),
    discord.Game("detecting dipole moments in molecules"),
    discord.Game("experimenting with superacids"),
    discord.Game("assembling a Grignard reagent"),
    discord.Game("quantum entanglement hide-and-seek"),
    discord.Game("with DFT simulations for giggles"),

    # Listening (Listening To ...)
    discord.Activity(type=discord.ActivityType.listening, name="the periodic table song"),
    discord.Activity(type=discord.ActivityType.listening, name="chemistry facts"),
    discord.Activity(type=discord.ActivityType.listening, name="user hypotheses"),
    discord.Activity(type=discord.ActivityType.listening, name="stoichiometry lectures"),
    discord.Activity(type=discord.ActivityType.listening, name="bubbling beakers"),
    discord.Activity(type=discord.ActivityType.listening, name="endothermic reactions"),
    discord.Activity(type=discord.ActivityType.listening, name="uranium humming"),
    discord.Activity(type=discord.ActivityType.listening, name="complaints about the mole concept"),
    discord.Activity(type=discord.ActivityType.listening, name="lab goggles fog up"),
    discord.Activity(type=discord.ActivityType.listening, name="theoretical screams"),
    discord.Activity(type=discord.ActivityType.listening, name="periodic table diss tracks"),
    discord.Activity(type=discord.ActivityType.listening, name="a centrifuge"),
    discord.Activity(type=discord.ActivityType.listening, name="atoms bonding"),
    discord.Activity(type=discord.ActivityType.listening, name="a lab explosion"),
    discord.Activity(type=discord.ActivityType.listening, name="a chemical spill"),
    discord.Activity(type=discord.ActivityType.listening, name="a Bunsen burner"),
    discord.Activity(type=discord.ActivityType.listening, name="a chemical reaction"),
    discord.Activity(type=discord.ActivityType.listening, name="a lab accident"),
    discord.Activity(type=discord.ActivityType.listening, name="molecules colliding"),
    discord.Activity(type=discord.ActivityType.listening, name="neutrino whispers"),
    discord.Activity(type=discord.ActivityType.listening, name="radioactive decay beats"),
    discord.Activity(type=discord.ActivityType.listening, name="electrons spin tunes"),
    discord.Activity(type=discord.ActivityType.listening, name="quark chatter"),
    discord.Activity(type=discord.ActivityType.listening, name="laser hums"),
    discord.Activity(type=discord.ActivityType.listening, name="NMR peak harmonics"),
    discord.Activity(type=discord.ActivityType.listening, name="Raman spectra whispers"),
    discord.Activity(type=discord.ActivityType.listening, name="crystal lattice vibrations"),
    discord.Activity(type=discord.ActivityType.listening, name="oscillating BZ reactions"),
    discord.Activity(type=discord.ActivityType.listening, name="singlet-triplet transitions"),
    discord.Activity(type=discord.ActivityType.listening, name="forbidden infrared absorptions"),
    discord.Activity(type=discord.ActivityType.listening, name="hyperfine splitting murmurs"),
    discord.Activity(type=discord.ActivityType.listening, name="EPR electron spins"),
    discord.Activity(type=discord.ActivityType.listening, name="π-π stacking chatter"),
    discord.Activity(type=discord.ActivityType.listening, name="J-coupling confessions"),
    discord.Activity(type=discord.ActivityType.listening, name="topological insulator hum"),
    discord.Activity(type=discord.ActivityType.listening, name="van der Waals whispers"),
    discord.Activity(type=discord.ActivityType.listening, name="Hückel aromaticity lectures"),
    discord.Activity(type=discord.ActivityType.listening, name="spin-orbit coupling vibes"),
    discord.Activity(type=discord.ActivityType.listening, name="bond order debates"),
    discord.Activity(type=discord.ActivityType.listening, name="photoelectron spectra"),
    discord.Activity(type=discord.ActivityType.listening, name="Schrödinger cat meows"),
    discord.Activity(type=discord.ActivityType.listening, name="hyperconjugation discussions"),
    discord.Activity(type=discord.ActivityType.listening, name="Pauli principle sermons"),

    # Watching (Watching ...)
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
    discord.Activity(type=discord.ActivityType.watching, name="chemical bonds break"),
    discord.Activity(type=discord.ActivityType.watching, name="lab rats escape"),
    discord.Activity(type=discord.ActivityType.watching, name="the lab spontaneously combust"),
    discord.Activity(type=discord.ActivityType.watching, name="quantum particles behave oddly"),
    discord.Activity(type=discord.ActivityType.watching, name="the lab safety officer nap"),
    discord.Activity(type=discord.ActivityType.watching, name="supercooled liquids crack"),
    discord.Activity(type=discord.ActivityType.watching, name="plasma arcs dance"),
    discord.Activity(type=discord.ActivityType.watching, name="nanobots assemble"),
    discord.Activity(type=discord.ActivityType.watching, name="electron clouds shift"),
    discord.Activity(type=discord.ActivityType.watching, name="chemical equilibrium sway"),
    discord.Activity(type=discord.ActivityType.watching, name="magnetism in action"),
    discord.Activity(type=discord.ActivityType.watching, name="hydrogen bonding in slow motion"),
    discord.Activity(type=discord.ActivityType.watching, name="reaction coordinate diagrams unfold"),
    discord.Activity(type=discord.ActivityType.watching, name="UV-Vis absorbance shift"),
    discord.Activity(type=discord.ActivityType.watching, name="X-ray diffraction patterns dance"),
    discord.Activity(type=discord.ActivityType.watching, name="chirality flips in 3D"),
    discord.Activity(type=discord.ActivityType.watching, name="cis-trans photoisomerization"),
    discord.Activity(type=discord.ActivityType.watching, name="Brownian motion chaos"),
    discord.Activity(type=discord.ActivityType.watching, name="lanthanide contraction in action"),
    discord.Activity(type=discord.ActivityType.watching, name="isotopic fractionation"),
    discord.Activity(type=discord.ActivityType.watching, name="enthalpy vs entropy duels"),
    discord.Activity(type=discord.ActivityType.watching, name="transition state lifetimes"),
    discord.Activity(type=discord.ActivityType.watching, name="molecular vibrations on steroids"),
    discord.Activity(type=discord.ActivityType.watching, name="sigma and pi bonds argue"),
    discord.Activity(type=discord.ActivityType.watching, name="aromaticity collapse"),
    discord.Activity(type=discord.ActivityType.watching, name="quantum dot luminescence"),
    discord.Activity(type=discord.ActivityType.watching, name="photoexcited electron dance"),
    discord.Activity(type=discord.ActivityType.watching, name="ionic liquids misbehave"),
    discord.Activity(type=discord.ActivityType.watching, name="superconducting vortices"),
    discord.Activity(type=discord.ActivityType.watching, name="vibrational spectroscopy showdown"),
    discord.Activity(type=discord.ActivityType.watching, name="Fermi levels shift in real time"),
]

# holy moly that is a lot of activities!
# remove or add as you see fit, but probably keep it above 20 to avoid repetition
# also you can ask chatgpt for more ideas or a change of theme
# have fun!

# 12/12/2025: hear me out, randomized activites based on the time of the day AND rarity!!!!
# fuck yeah lemme whip this bitch
# this'll be implemented later

ACTIVITIES_V2 = {
    'DAY_COMMON': [
        {'act': Game("with equations"), 'w': 10},
        {'act': Game("with solutions"), 'w': 9},
        {'act': Game("with molecules"), 'w': 9},
        {'act': Game("with noble gases"), 'w': 7},
        {'act': Game("hide and seek with electrons"), 'w': 6},
        {'act': Game("periodic table scavenger hunt"), 'w': 6},
        {'act': Game("pH meter challenge"), 'w': 6},
        {'act': Activity(type=ActivityType.listening, name="chemistry facts"), 'w': 8},
        {'act': Activity(type=ActivityType.listening, name="stoichiometry lectures"), 'w': 7},
        {'act': Activity(type=ActivityType.watching, name="chemical reactions"), 'w': 9},
        {'act': Activity(type=ActivityType.watching, name="crystals grow"), 'w': 7},
        {'act': Activity(type=ActivityType.watching, name="atoms collide"), 'w': 7},
    ],

    'DAY_UNCOMMON': [
        {'act': Game("finding Avogadro's number"), 'w': 5},
        {'act': Game("with unstable isotopes"), 'w': 4},
        {'act': Game("reaction kinetics"), 'w': 5},
        {'act': Game("aligning electron orbitals"), 'w': 4},
        {'act': Game("titrating weak acids like a pro"), 'w': 4},
        {'act': Game("predicting UV-Vis peaks"), 'w': 4},
        {'act': Activity(type=ActivityType.listening, name="bubbling beakers"), 'w': 5},
        {'act': Activity(type=ActivityType.listening, name="periodic table diss tracks"), 'w': 3},
        {'act': Activity(type=ActivityType.watching, name="Brownian motion chaos"), 'w': 4},
        {'act': Activity(type=ActivityType.watching, name="hydrogen bonding in slow motion"), 'w': 4},
    ],

    'DAY_RARE': [
        {'act': Game("with forbidden compounds"), 'w': 2},
        {'act': Game("quantum tunneling hide-and-seek"), 'w': 2},
        {'act': Game("constructing molecular orbital diagrams"), 'w': 2},
        {'act': Game("solving Schrödinger's equation for fun"), 'w': 2},
        {'act': Activity(type=ActivityType.listening, name="neutrino whispers"), 'w': 2},
        {'act': Activity(type=ActivityType.listening, name="Pauli principle sermons"), 'w': 1},
        {'act': Activity(type=ActivityType.watching, name="aromaticity collapse"), 'w': 1},
        {'act': Activity(type=ActivityType.watching, name="transition state lifetimes"), 'w': 2},
    ],

    'NIGHT_COMMON': [
        {'act': Game("in the lab... unsupervised"), 'w': 10},
        {'act': Game("experiment roulette"), 'w': 9},
        {'act': Game("atomic tag"), 'w': 8},
        {'act': Activity(type=ActivityType.listening, name="a centrifuge"), 'w': 7},
        {'act': Activity(type=ActivityType.listening, name="laser hums"), 'w': 6},
        {'act': Activity(type=ActivityType.watching, name="users ignore lab safety"), 'w': 9},
        {'act': Activity(type=ActivityType.watching, name="ionic drama unfold"), 'w': 7},
        {'act': Game("crystal hunting"), 'w': 6},
    ],

    'NIGHT_UNCOMMON': [
        {'act': Game("acid roulette"), 'w': 4},
        {'act': Game("with questionable solvents"), 'w': 4},
        {'act': Game("with radioactive decay"), 'w': 3},
        {'act': Game("isolating carbocations safely"), 'w': 3},
        {'act': Game("assembling a Grignard reagent"), 'w': 2},
        {'act': Game("attempting a Williamson ether synthesis"), 'w': 2},
        {'act': Activity(type=ActivityType.listening, name="radioactive decay beats"), 'w': 2},
        {'act': Activity(type=ActivityType.listening, name="a lab explosion"), 'w': 3},
        {'act': Activity(type=ActivityType.watching, name="the lab explode"), 'w': 3},
        {'act': Activity(type=ActivityType.watching, name="ionic liquids misbehave"), 'w': 2},
    ],

    'NIGHT_RARE': [
        {'act': Game("Factorio: Meth Lab DLC"), 'w': 1},
        {'act': Game("Breaking Bad (educational edition)"), 'w': 1},
        {'act': Game("with superacids"), 'w': 1},
        {'act': Game("DFT simulations for giggles"), 'w': 1},
        {'act': Game("playing with organometallic catalysts"), 'w': 1},
        {'act': Activity(type=ActivityType.listening, name="a chemical spill"), 'w': 1},
        {'act': Activity(type=ActivityType.watching, name="the lab spontaneously combust"), 'w': 1},
        {'act': Activity(type=ActivityType.watching, name="moles commit tax fraud"), 'w': 1},
        {'act': Activity(type=ActivityType.watching, name="quantum particles behave oddly"), 'w': 1},
    ],
}

DAY_BUCKET_WEIGHTS = {
    'COMMON': 30,
    'UNCOMMON': 15,
    'RARE': 5,
}

NIGHT_BUCKET_WEIGHTS = {
    'COMMON': 40,
    'UNCOMMON': 7,
    'RARE': 3,
}

def get_activity(now_hour):
    """
    Returns a random activity based on the time of day and rarity
    Args:
        now_hour (int): The current hour of the day
    Returns:
        discord.Activity: A random activity based on the time of day and rarity
    """
    is_day = 7 <= now_hour <= 22

    mode = 'DAY' if is_day else 'NIGHT'
    bucket_weights = DAY_BUCKET_WEIGHTS if is_day else NIGHT_BUCKET_WEIGHTS

    # pick rarity
    rarities, rw = zip(*bucket_weights.items())
    rarity = random.choices(rarities, weights=rw, k=1)[0]

    # pick activity inside rarity bucket
    pool = ACTIVITIES[mode][rarity]
    acts, aw = zip(*[(p['act'], p['w']) for p in pool])
    return random.choices(acts, weights=aw, k=1)[0]
