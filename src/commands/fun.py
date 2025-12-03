# Standard Library Imports
import asyncio
import base64
import config
import io
import json
import math
import os
import platform
import psutil
import random
import re
import subprocess
import threading
import time
import secrets
from datetime import timezone, timedelta, datetime
from typing import Optional


# Third-Party Imports
import aiohttp
import discord
from discord.ext import commands
from discord import app_commands, Interaction, Embed


# Local Imports
import CloudflarePing as cf
import config
from config import BOT_TOKEN, cooldown, IS_ALPHA
from extraconfig import BOT_OWNER
from logger import get_logger
from utils.roll_logic import execute_roll
from utils.eightball_responses import EIGHTBALL_RESPONSES

log = get_logger()

exchange_cache = {}
CACHE_DURATION = 86400
MAX_AMOUNT = 1e8
MAX_VALUE = 1e8

class FunCommands(app_commands.Group):
    def __init__(self, bot):
        super().__init__(name="fun", description="Fun commands like dice rolling, 8ball, ping, etc.")
        self.bot = bot
        self.process = psutil.Process(os.getpid())
        psutil.cpu_percent(interval=None) 

    # table tennis?
    @app_commands.command(name="ping", description="Check the bot's response time!")
    @cooldown(cl=10, tm=30.0, ft=3)
    async def ping(self, interaction: discord.Interaction):
        start_time = time.perf_counter()
        log.trace(f"Ping invoked by {interaction.user.id}")
        await interaction.response.defer(ephemeral=False)
        end_time = time.perf_counter()
        thinking_time = (end_time - start_time) * 1000
        latency = round(self.bot.latency * 1000, 2)

        embed = discord.Embed(title="🏓 Pong!", color=0x00FF00)
        embed.add_field(name="📡 API Latency", value=f"`{latency} ms`", inline=True)
        embed.add_field(name="⏳ Thinking Time", value=f"`{thinking_time:.2f} ms`", inline=True)

        # Detect environment (used for IPv6 handling)
        is_railway = "RAILWAY_PROJECT_ID" in os.environ
        is_docker = os.path.exists("/.dockerenv")

        try:
            cf_cache = await cf.get_cached_pings() or {}
            ipv4 = cf_cache.get("ipv4")
            ipv6 = cf_cache.get("ipv6")
            ts = cf_cache.get("ts")

            # IPv4 RTT
            embed.add_field(
                name="🟠 CF IPv4 RTT",
                value=f"`{ipv4:.1f} ms`" if ipv4 is not None else "N/A",
                inline=False,
            )

            # IPv6 RTT or reason it's missing
            if ipv6 is not None:
                embed.add_field(name="🟣 CF IPv6 RTT", value=f"`{ipv6:.1f} ms`", inline=False)
            else:
                if is_railway:
                    ipv6_text = "Not available"
                elif is_docker:
                    ipv6_text = "Not available, Docker lacks IPv6"
                else:
                    ipv6_text = "N/A"
                embed.add_field(name="🟣 CF IPv6 RTT", value=ipv6_text, inline=False)

            # Footer with timestamp and environment note
            footer_note = []
            if ts:
                footer_note.append(
                    f"CF cached: {datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S')}"
                )
            if is_railway:
                footer_note.append("Running on Railway (IPv6 disabled)")
            elif is_docker:
                footer_note.append("Running in Docker (IPv6 disabled)")

            if footer_note:
                embed.set_footer(text=" | ".join(footer_note))

        except Exception as e:
            log.warning(f"Cloudflare ping cache read failed: {e}")
            embed.add_field(name="🟠 CF RTT", value="Unavailable", inline=False)

        await interaction.followup.send(embed=embed, ephemeral=False)

    # the bane of my existance
    @app_commands.command(name="roll", description="Roll a set of dice!")
    @app_commands.describe(dice="Dice expression to roll, type 'help' for syntax.", expand="Show detailed breakdown of the roll")
    @cooldown(cl=5, tm=20.0, ft=3)
    async def roll(self, interaction: discord.Interaction, dice: str, expand: bool = False):
        await interaction.response.defer(ephemeral=False)
        log.trace(f"Roll invoked by {interaction.user.id}: {dice}")

        # Quick help
        if dice.strip().lower() == "help":
            HELP_TEXT = (
                "🎲 **Dice Roller Help**\n\n"
                "Syntax: combine terms with + or -: `1d20 + 1d4 - 2 + 3d6k2`\n\n"
                "`XdY` — roll X Y-sided dice\n"
                "`XdYkN` / `XdYD N` — keep highest N / drop lowest N (per-group). NOTE: **drop uses uppercase `D`** to avoid ambiguity with the dice `d`.\n\n"
                "numeric terms like `+2` or `-1` are constants\n\n"
                "`!` / `!!` / `!p` / `!!p` — explode / compound / penetrate / compound+penetrate\n"
                "Simplified inputs, such as `20` will auto convert to a 1d20.\n"
                "Tip: pass the slash option `expand=True` for a full breakdown."
            )
            help_embed = discord.Embed(title="🎲 Dice Roller Help", description=HELP_TEXT, color=0x3498db)
            await interaction.followup.send(embed=help_embed, ephemeral=False)
            return

        # Execute roll using roll_logic.py
        try:
            roll_result = execute_roll(dice)
        except ValueError as e:
            await interaction.followup.send(f"❌ **{str(e)}** Do /roll dice: help for syntax and examples", ephemeral=False)
            return
        except Exception as e:
            log.error(f"Error executing roll: {e}", exc_info=True)
            await interaction.followup.send("❌ **An error occurred while rolling dice.**", ephemeral=False)
            return

        group_summaries = roll_result["group_summaries"]
        footer_keepdrop = roll_result["footer_keepdrop"]
        ampersand_notes = roll_result["ampersand_notes"]
        const_total = roll_result["const_total"]
        pre_mod_total = roll_result["pre_mod_total"]
        post_mod_total = roll_result["post_mod_total"]

        # Build output
        # CONTRACTED (simple): "@user rolled (dice): (result)"
        if not expand:
            compact_parts = []
            for gs in group_summaries:
                if gs["kind"] == "const":
                    # constants shown as their signed value/label
                    compact_parts.append(f"{gs['label']}")
                    continue

                per_die = gs["details"]
                die_texts = []
                for d in per_die:
                    # show explosion chains compactly (e.g. (6 + 4)=10) or single face values
                    if len(d["chain_display"]) > 1:
                        die_texts.append("(" + " + ".join(map(str, d["chain_display"])) + f")={d['pre_contrib']}")
                    else:
                        die_texts.append(str(d["pre_contrib"]))
                compact_parts.append(f"{gs['label']}: " + ", ".join(die_texts))

            simple_text = f"{interaction.user.mention} rolled `{dice}`: " + " | ".join(compact_parts)
            if footer_keepdrop:
                simple_text += "  _(keeps/drops applied — use expand for totals)_"
            await interaction.followup.send(simple_text, ephemeral=False)
            return

        # EXPANDED: Build embed with before/after and modifiers
        embed = discord.Embed(title=f"🎲 Dice Roll — {interaction.user.display_name}", color=0x3498db)
        # Before modifiers: per-group show each die's chain_display and pre_keep_sum (signed)
        before_lines = []
        for gs in group_summaries:
            if gs["kind"] == "const":
                before_lines.append(f"`{gs['label']}` → pre-keep sum: `{gs['pre_keep_sum']}`")
                continue
            # show each die (chain) before modifiers; indicate dropped dice too
            per_die = gs["details"]
            die_texts = []
            for d in per_die:
                if len(d["chain_display"]) > 1:
                    die_texts.append("(" + " + ".join(map(str, d["chain_display"])) + f")={d['pre_contrib']}")
                else:
                    die_texts.append(str(d["pre_contrib"]))
            before_lines.append(f"`{gs['label']}`: " + ", ".join(die_texts) + f" → pre-keep sum: `{gs['pre_keep_sum']}`")

        embed.add_field(name="🔍 Before Modifiers", value="\n".join(before_lines), inline=False)

        # Modifiers section
        mod_lines = []
        if ampersand_notes:
            mod_lines.extend(ampersand_notes)
        if footer_keepdrop:
            mod_lines.append("Keep/Drop: " + ", ".join(footer_keepdrop))
        mod_lines.append(f"Constants total: `{const_total}`")
        if not mod_lines:
            mod_lines = ["No modifiers applied"]
        embed.add_field(name="✨ Modifiers Applied", value="\n".join(mod_lines), inline=False)

        # After modifiers: per-group totals and final totals
        after_lines = []
        for idx, gs in enumerate(group_summaries):
            if gs["kind"] == "const":
                after_lines.append(f"`{gs['label']}` → `{gs['post_mod_sum']}`")
                continue
            after_lines.append(f"`{gs['label']}` → before: `{gs['pre_keep_sum']}` → after: `{gs['post_mod_sum']}`")

        embed.add_field(name="🏁 After Modifiers", value="\n".join(after_lines), inline=False)
        embed.add_field(name="📊 Totals", value=f"Pre-mod total: `{pre_mod_total}`\nPost-mod total: `{post_mod_total}`", inline=False)

        embed.set_footer(text=f"Dice rolled: {dice}")
        # size-check (like before)
        fields_too_long = any(len(field.value) > 1024 for field in embed.fields)
        total_embed_length = (
            len(embed.title or "") +
            len(embed.description or "") +
            sum((len(field.name or "") + len(field.value or "")) for field in embed.fields) +
            (len(embed.footer.text or "") if embed.footer else 0)
        )
        too_long = fields_too_long or total_embed_length > 6000
        if too_long:
            file = io.BytesIO(json.dumps(embed.to_dict(), indent=2).encode('utf-8'))
            file.name = "dice_roll_embed.json"
            error_embed = discord.Embed(title="🎲 Dice Roll (Output to File)", color=0x3498db)
            error_embed.add_field(name="⚠️ Error", value="Embed content exceeded Discord's size limit. Output is in file", inline=False)
            error_embed.set_footer(text=f"Dice rolled: {dice}")
            await interaction.followup.send(embed=error_embed, file=discord.File(file, filename="dice_roll_embed.json"), ephemeral=False)
            file.close()
        else:
            await interaction.followup.send(embed=embed, ephemeral=False)

    @app_commands.command(name="8ball" , description="Ask the magic 8-ball a question!")
    @cooldown(cl=5, tm=20.0, ft=3)
    async def eight_ball(self, interaction: discord.Interaction, question: str):
        await interaction.response.defer(ephemeral=False)
        log.trace(f"8ball invoked by {interaction.user.id}: {question}")
        if not question:
            return await interaction.followup.send("❌ **I'm not a mind reader! Ask a question!**", ephemeral=True)
        
        index = secrets.randbelow(len(EIGHTBALL_RESPONSES))  # Using secrets for better randomness
        answer = EIGHTBALL_RESPONSES[index]
        embed = discord.Embed(title="🎱 Magic 8-Ball", color=0x3498db)
        embed.add_field(name="Question", value=f"`{question}`", inline=False)
        embed.add_field(name="Answer", value=f"`{answer}`", inline=False)
        await interaction.followup.send(embed=embed, ephemeral=False)

    @app_commands.command(name="hack", description="Hack another user! Totally 100% legit.")
    @cooldown(cl=60, tm=25.0, ft=3)
    async def hack(self, interaction: discord.Interaction, target: discord.Member):
        log.trace(f"Hack invoked by {interaction.user.id} on {target.id}")
        await interaction.response.defer(ephemeral=False)

        if target == interaction.user:
            return await interaction.followup.send("❌ You can't hack yourself (unless you want to be roasted by your own IP).", ephemeral=True)

        # Tiny friendly disclaimer so we dont get banished by discord
        disclaimer = "-# ⚠️ **Disclaimer:** This is a simulated gag — no personal data is accessed or stored."
        msg = await interaction.followup.send(f"💻 Initializing hack sequence on {target.mention}...\n{disclaimer}", ephemeral=False)
        try:
            msg = await interaction.original_response()
        except Exception:
            # fallback if original_response not available
            pass

        # Flavorful staged messages
        stages = [
            "Scanning ports (why are there so many open ports?)",
            "Bypassing cookie consent... delicious crumbs detected",
            "Probing social media for embarrassing karaoke clips",
            "Cracking password (this is probably 'password123' tbh)",
            "Injecting tasteful malware (just kidding, it's glitter)",
            "Compiling list of suspiciously common hobbies...",
            "Accessing private folder: `mildly_awkward_memes/`",
            "Uploading hypebeast.exe to cloud (takes a sec)",
            "Planting digital cactus 🌵 — can't remove remotely, oops"
        ]

        # Randomized leak pool (completely fictional/fake)
        fake_emails = [
            f"{target.name.lower()}{random.randint(1,999)}@example.com",
            f"{target.name.lower()}.{random.randint(10,99)}@mailinator.com",
            f"{target.name[0].lower()}{secrets.token_hex(2)}@nope.invalid"
        ]
        fake_passwords = ["hunter2", "ilovepizza", "correcthorsebatterystaple", "123456789", "letmeinpls"]
        fake_ips = [f"10.{random.randint(0,255)}.{random.randint(0,255)}.{random.randint(0,255)}",
                    f"172.1{random.randint(6,31)}.{random.randint(0,255)}.{random.randint(0,255)}"]
        fake_files = ["mildly_awkward_meme.png", "favorite_thanksgiving_dish.txt", "guitar_solo.mid", "shopping_list.xlsx", "super_duper_secret.py"]

        progress = 0
        bar_len = 20

        # chance to "backfire" on the invoker (comedic)
        backfire = random.random() < 0.06  # 6% chance to backfire
        if backfire:
            final_status = "BACKFIRE"
            final_text = f"💥 Whoops — security flagged your machine! You got pwned instead. Better luck next time, {interaction.user.mention}."
        else:
            final_status = "SUCCESS"
            final_text = f"✅ Hack complete! Collected a tasteful pile of totally-fictional evidence on {target.display_name}."

        # perform stages with progress updates
        for i, stage in enumerate(random.sample(stages, k=min(len(stages), 6))):
            # jitter progress increments: first steps slow, later steps jump more
            increment = random.randint(6, 20) if i > 2 else random.randint(6, 12)
            progress = min(99, progress + increment)
            blocks = "█" * (progress * bar_len // 100)
            spaces = "░" * (bar_len - len(blocks))
            content = f"💻 Hacking **{target.display_name}**...\n`[{blocks}{spaces}] {progress}%`\n🔧 {stage}"
            try:
                await msg.edit(content=content)
            except Exception:
                # some clients don't allow edit in this context; ignore
                pass
            await asyncio.sleep(random.uniform(0.9, 1.6))

        # final flash to 100
        progress = 100
        blocks = "█" * bar_len
        await asyncio.sleep(0.6)
        try:
            await msg.edit(content=f"💻 Hacking **{target.display_name}**...\n`[{blocks}] 100%`\n🎯 Finalizing...")
        except Exception:
            pass
        await asyncio.sleep(0.9)

        # Build a quirky embed of fake 'leaked' info (strictly fictional)
        embed = discord.Embed(title=f"📂 Leak — {target.display_name}", color=0xE74C3C)
        if backfire:
            embed.description = final_text
            embed.add_field(name="Effect", value="You have been roasted. Console: `¯\\_(ツ)_/¯`", inline=False)
            embed.add_field(name="Remediation", value="Reboot, unplug, beg for mercy.", inline=False)
        else:
            embed.add_field(name="Primary Email", value=random.choice(fake_emails), inline=True)
            embed.add_field(name="Favorite Password (leaked)", value=random.choice(fake_passwords), inline=True)
            embed.add_field(name="Last Known IP", value=random.choice(fake_ips), inline=True)
            embed.add_field(name="Top Secret Files", value=", ".join(random.sample(fake_files, 2)), inline=False)
            embed.add_field(name="Note", value="All results are fabricated for entertainment. No personal data was accessed.", inline=False)
            embed.set_footer(text=final_text)

        # Final send/replace
        try:
            await msg.edit(content=None, embed=embed)
        except Exception:
            # fallback: send as a new message
            await interaction.followup.send(embed=embed)

        return

    @app_commands.command(name="info", description="Get information about the bot.")
    @cooldown(cl=5, tm=20.0, ft=3)
    async def info_of_bot(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False)
        log.trace(f"Info invoked by {interaction.user.id}")
        bot_user = self.bot.user

        now = time.time()
        bot_start_time = getattr(self.bot, "start_time", time.time())
        if bot_start_time is None:
            uptime_seconds = 0
        else:
            uptime_seconds = int(now - bot_start_time)

        if platform.system() == "Windows" and platform.release() == "11":
            System = "Loc. Machine Testing"
        elif platform.system() == "Linux":
            System = "VPS/Railway Hosting"
        else:
            System = "Other"

        def count_all_slash_commands(commands_list):
            total = 0
            for cmd in commands_list:
                if isinstance(cmd, discord.app_commands.ContextMenu):
                    continue
                elif isinstance(cmd, discord.app_commands.Group):
                    total += len(cmd._children)
                else:
                    total += 1
            return total

        def format_uptime(seconds):
            days, seconds = divmod(seconds, 86400)
            hours, seconds = divmod(seconds, 3600)
            minutes, seconds = divmod(seconds, 60)
            parts = []
            if days: parts.append(f"{days}d")
            if hours: parts.append(f"{hours}h")
            if minutes: parts.append(f"{minutes}m")
            parts.append(f"{seconds}s")
            return " ".join(parts)

        # Get fun fact
        fun_fact = "Fetching fun fact..."
        session = self.bot.http_session
        if session:
            try:
                async with session.get("https://uselessfacts.jsph.pl/random.json?language=en") as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        fun_fact = data.get("text", "No fun fact found.")
                    else:
                        fun_fact = "Could not fetch fun fact."
            except Exception:
                fun_fact = "Error fetching fun fact."

        embed = discord.Embed(
            title=f"🤖 Bot Info: {bot_user.name}",
            description="Here's some info about me, your friendly bot!",
            color=0x3498db
        )
        embed.set_thumbnail(url=bot_user.avatar.url if bot_user.avatar else None)
        embed.add_field(name="🆔 Bot ID", value=f"`{bot_user.id}`", inline=True)
        embed.add_field(name="👤 Created By", value="Iza Carlos (`_izacarlos`)", inline=True)
        embed.add_field(
            name="📅 Created At",
            value=f"`{bot_user.created_at.strftime('%Y-%m-%d %H:%M:%S')}`",
            inline=False
        )
        embed.add_field(
            name="🛠️ Commands",
            value=f"`{count_all_slash_commands(self.bot.tree.get_commands())}` slash commands available",
            inline=True
        )
        embed.add_field(
            name="⏳ Uptime",
            value=f"`{format_uptime(uptime_seconds)}`",
            inline=True
        )
        embed.add_field(
            name="💻 Python Version",
            value=f"`{platform.python_version()}`",
            inline=True
        )
        embed.add_field(
            name="🖥️ Host OS",
            value=f"`{platform.system()} {platform.release()}` ",
            inline=True
        )
        embed.add_field(
            name="💾 Hosting Env.",
            value=f"`{System}`",
            inline=True
        )
        embed.add_field(
            name="📋 Terms of Service",
            value="[View Terms of Service](https://github.com/ThatOneFBIAgent/Flurazida/blob/main/other/TOS.md)",
            inline=False
        )
        embed.add_field(
            name="🔒 Privacy policy",
            value="[View Privacy policy](https://github.com/ThatOneFBIAgent/Flurazida/blob/main/other/Privacy.md)",
            inline=False
        )
        embed.set_footer(text=f"Fun Fact: {fun_fact}")

        await interaction.followup.send(embed=embed, ephemeral=False)
    # stupid dum dum discord reserves bot_ for their own shit, i'm angry
    
    @app_commands.command(name="serverinfo", description="Get information about current server")
    @cooldown(cl=5, tm=20.0, ft=3)
    async def serverinfo(self, interaction: discord.Interaction, hidden: bool = False):
        await interaction.response.defer(ephemeral=False)
        log.trace(f"Serverinfo invoked by {interaction.user.id}")
        guild = interaction.guild
        embed = discord.Embed(
            title=f"{guild.name} Info",
            color=0x5865f2
        )

        # Server icon
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        else:
            embed.set_thumbnail(url=None)

        # Owner
        owner = guild.owner
        if not owner:
            try:
                owner = await self.bot.fetch_user(guild.owner_id)
            except Exception:
                owner = None
        owner_display = owner.mention if owner else f"`{guild.owner_id}`"

        embed.add_field(name="Server ID", value=str(guild.id), inline=False)
        embed.add_field(name="Owner", value=owner_display, inline=True)
        embed.add_field(name="Member Count", value=str(guild.member_count), inline=True)
        embed.add_field(
            name="Created At",
            value=guild.created_at.strftime("%Y-%m-%d %H:%M:%S"),
            inline=False
        )
        embed.add_field(name="Roles", value=str(len(guild.roles)), inline=True)
        embed.add_field(name="Channels", value=str(len(guild.channels)), inline=True)
        embed.add_field(name="Boosts", value=str(getattr(guild, "premium_subscription_count", 0)), inline=True)

        # Member counts
        total_members = guild.member_count
        if hasattr(guild, "members") and guild.members:  # Requires members intent!
            bot_count = sum(1 for m in guild.members if m.bot)
            human_count = total_members - bot_count
        else:
            # Fallback if members intent is not enabled
            bot_count = "?"
            human_count = "?"

        embed.add_field(name="Total Members", value=str(total_members), inline=True)
        embed.add_field(name="Humans", value=str(human_count), inline=True)
        embed.add_field(name="Bots", value=str(bot_count), inline=True)

        # User info (just from interaction.user)
        user = interaction.user
        embed.add_field(
            name=f"Your Info ({user.display_name})",
            value=f"• ID: `{user.id}`\n• Joined: {user.joined_at.strftime('%Y-%m-%d %H:%M:%S') if hasattr(user, 'joined_at') and user.joined_at else 'Unknown'}",
            inline=False
        )

        # Permissions
        user = interaction.user
        if isinstance(user, discord.Member):
            perms = user.guild_permissions
            is_admin = perms.administrator
            is_mod = perms.manage_messages or perms.kick_members or perms.ban_members or perms.manage_guild
            is_owner = user.id == guild.owner_id

            perm_text = []
            if is_owner:
                perm_text.append("Owner")
            elif is_admin:
                perm_text.append("Administrator")
            elif is_mod:
                perm_text.append("Moderator")
            else:
                perm_text.append("Member")

            embed.add_field(
                name="Your Permissions",
                value=", ".join(perm_text),
                inline=True
            )
        else:
            embed.add_field(
                name="Your Permissions",
                value="Unknown (not a guild member)",
                inline=True
            )

        embed.set_footer(text=f"Requested by {user.name} ({user.id})")

        await interaction.followup.send(embed=embed, ephemeral=hidden)

    @app_commands.command(name="letter", description="Generate a random letter.")
    @cooldown(cl=5, tm=20.0, ft=3)
    async def letter(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False)
        letter = random.choice("abcdefghijklmnopqrstuvwxyz")
        embed = discord.Embed(title="🔤 Random Letter", color=0x3498db)
        embed.add_field(name="Generated Letter", value=f"`{letter}`", inline=False)
        await interaction.followup.send(embed=embed, ephemeral=False) # What are we letter-gatekeeping now?

    @app_commands.command(name="cat", description="Get a random cat image")
    @cooldown(cl=5, tm=20.0, ft=3)
    async def cat(self, interaction: discord.Interaction):
        log.trace(f"Cat invoked by {interaction.user.id}")
        await interaction.response.defer(ephemeral=False)
        session = self.bot.http_session
        if not session:
            log.error("HTTP session missing for cat command")
            await interaction.followup.send("😿 HTTP session not available.", ephemeral=True)
            return
        async with session.get("https://api.thecatapi.com/v1/images/search") as resp:
            if resp.status != 200:
                log.error(f"Cat API failed: {resp.status}")
                await interaction.followup.send("❌ Failed to fetch cat image.", ephemeral=True)
                return
            data = await resp.json()
            if not data or "url" not in data[0]:
                await interaction.followup.send("😿 No cat image found.", ephemeral=True)
                return
            cat_url = data[0]["url"]
            cat_id = data[0].get("id", "unknown")
            embed = discord.Embed(title="🐱 Random Cat", color=0x3498db)
            embed.set_image(url=cat_url)
            embed.set_footer(text=f"Cat ID: {cat_id}")
            await interaction.followup.send(embed=embed, ephemeral=False)

    @app_commands.command(name="dog", description="Get a random dog image")
    @cooldown(cl=5, tm=20.0, ft=3)
    async def dog(self, interaction: discord.Interaction):
        log.trace(f"Dog invoked by {interaction.user.id}")
        await interaction.response.defer(ephemeral=False)
        session = self.bot.http_session
        if not session:
            log.error("HTTP session missing for dog command")
            await interaction.followup.send("🐶 HTTP session not available.", ephemeral=True)
            return
        async with session.get("https://dog.ceo/api/breeds/image/random") as resp:
            if resp.status != 200:
                log.error(f"Dog API failed: {resp.status}")
                await interaction.followup.send("🐶 Failed to fetch a dog image.", ephemeral=True)
                return
            data = await resp.json()
            if "message" not in data or not data["message"]:
                await interaction.followup.send("🐶 No dog image found.", ephemeral=True)
                return
            dog_url = data["message"]
            embed = discord.Embed(title="🐶 Random Dog", color=0x3498db)
            embed.set_image(url=dog_url)
            await interaction.followup.send(embed=embed, ephemeral=False)

    @app_commands.command(name="help", description="Get a list of available commands (paginated).")
    @cooldown(cl=2, tm=10.0, ft=3)
    async def help_command(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False)
        log.trace(f"Help invoked by {interaction.user.id}")

        def walk_commands(commands_list):
            """Flatten commands recursively into (full_name, description) tuples."""
            result = []
            for cmd in commands_list:
                # Skip context menu commands
                if isinstance(cmd, discord.app_commands.ContextMenu):
                    continue
                if isinstance(cmd, discord.app_commands.Group):
                    # recurse into children
                    for child_name, child_cmd in cmd._children.items():
                        full_name = f"{cmd.name} {child_name}"
                        result.append((full_name, child_cmd.description or "No description available"))
                else:
                    result.append((cmd.name, cmd.description or "No description available"))
            return result

        all_cmds = walk_commands(self.bot.tree.get_commands())
        per_page = 12
        total_pages = (len(all_cmds) + per_page - 1) // per_page

        def get_embed(page: int):
            embed = discord.Embed(title="🆘 Help - Available Commands", color=0x3498db)
            embed.description = f"Page {page+1}/{total_pages}\nHere are the commands you can use:"
            start = page * per_page
            end = start + per_page
            for name, desc in all_cmds[start:end]:
                embed.add_field(name=f"/{name}", value=desc, inline=False)
            embed.set_footer(text="Use /<command_name> to execute a command.")
            return embed

        class HelpView(discord.ui.View):
            def __init__(self):
                super().__init__(timeout=60)
                self.page = 0

            @discord.ui.button(label="⏮️", style=discord.ButtonStyle.secondary)
            async def first(self, i, b): self.page = 0; await i.response.edit_message(embed=get_embed(self.page), view=self)
            @discord.ui.button(label="⬅️", style=discord.ButtonStyle.secondary)
            async def prev(self, i, b):
                if self.page > 0: self.page -= 1; await i.response.edit_message(embed=get_embed(self.page), view=self)
                else: await i.response.defer()
            @discord.ui.button(label="❌", style=discord.ButtonStyle.danger)
            async def close(self, i, b): await i.response.edit_message(content="Help menu closed.", embed=None, view=None)
            @discord.ui.button(label="➡️", style=discord.ButtonStyle.secondary)
            async def next(self, i, b):
                if self.page < total_pages-1: self.page += 1; await i.response.edit_message(embed=get_embed(self.page), view=self)
                else: await i.response.defer()
            @discord.ui.button(label="⏭️", style=discord.ButtonStyle.secondary)
            async def last(self, i, b): self.page = total_pages-1; await i.response.edit_message(embed=get_embed(self.page), view=self)
            async def on_timeout(self):
                for item in self.children: item.disabled = True

        await interaction.followup.send(embed=get_embed(0), view=HelpView(), ephemeral=False)

    @app_commands.command(name="pokedex", description="Get information about a Pokémon.")
    @app_commands.describe(pokemon="The name/number of the Pokémon to look up, empty for random")
    @cooldown(cl=10, tm=30.0, ft=3)
    async def pokedex(self, interaction: discord.Interaction, pokemon: str | None = None):
        await interaction.response.defer(ephemeral=False)
        if pokemon is None:
            # Get a random pokemon by ID (1-1025 as of current gen)
            poke_name = str(random.randint(1, 1025))
        else:
            poke_name = pokemon.lower().strip()
        api_url = f"https://pokeapi.co/api/v2/pokemon/{poke_name}"

        session = self.bot.http_session
        if not session:
            log.error("HTTP session missing for xkcd command")
            await interaction.followup.send("❌ HTTP session not available.", ephemeral=True)
            return
        async with session.get(api_url) as resp:
            if resp.status != 200:
                await interaction.followup.send(f"❌ Pokémon `{pokemon}` not found.", ephemeral=True)
                return
            data = await resp.json()

        # Build embed
        embed = discord.Embed(
            title=f"Pokédex Entry: {data['name'].title()} (#{data['id']})",
            color=0x3498db
        )
        sprite_url = data['sprites']['front_default']
        if sprite_url:
            embed.set_thumbnail(url=sprite_url)

        types = ", ".join(t['type']['name'].title() for t in data['types'])
        abilities = ", ".join(a['ability']['name'].title() for a in data['abilities'])
        height_m = data['height'] / 10  # decimeters to meters
        weight_kg = data['weight'] / 10  # hectograms to kilograms

        embed.add_field(name="Type", value=types or "Unknown", inline=True)
        embed.add_field(name="Abilities", value=abilities or "Unknown", inline=True)
        embed.add_field(name="Height", value=f"{height_m} m", inline=True)
        embed.add_field(name="Weight", value=f"{weight_kg} kg", inline=True)

        stats_lines = []
        for stat in data['stats']:
            stat_name = stat['stat']['name'].replace('-', ' ').title()
            stat_value = stat['base_stat']
            stats_lines.append(f"**{stat_name}:** {stat_value}")
        embed.add_field(name="Base Stats", value="\n".join(stats_lines), inline=False)

        await interaction.followup.send(embed=embed, ephemeral=False)

    @app_commands.command(name="xkcd", description="Get a random XKCD comic.")
    @app_commands.describe(comic="The comic number to fetch (leave empty for random comic)")
    @cooldown(cl=7, tm=25.0, ft=3)
    async def xkcd(self, interaction: discord.Interaction, comic: int | None = None):
        await interaction.response.defer(ephemeral=False)
        log.trace(f"XKCD invoked by {interaction.user.id}: {comic}")
        session = self.bot.http_session
        if not session:
            await interaction.followup.send("❌ HTTP session not available.", ephemeral=True)
            return
        # First get the latest comic number
        async with session.get("https://xkcd.com/info.0.json") as resp:
            if resp.status != 200:
                log.error(f"XKCD API failed (latest): {resp.status}")
                await interaction.followup.send("❌ Failed to fetch XKCD comic.", ephemeral=True)
                return
            latest_data = await resp.json()
            latest_num = latest_data['num']

        if comic:
            if comic < 1 or comic > latest_num:
                log.error(f"XKCD API failed (comic {comic}): Comic number must be between 1 and {latest_num}")
                return await interaction.followup.send(f"❌ Comic number must be between 1 and {latest_num}.", ephemeral=True)
            async with session.get(f"https://xkcd.com/{comic}/info.0.json") as resp:
                if resp.status != 200:
                    log.error(f"XKCD API failed (comic {comic}): {resp.status}")
                    return await interaction.followup.send("❌ Failed to fetch XKCD comic.", ephemeral=True)
                comic_data = await resp.json()
        else:
            rand_num = random.randint(1, latest_num)
            async with session.get(f"https://xkcd.com/{rand_num}/info.0.json") as resp:
                if resp.status != 200:
                    log.error(f"XKCD API failed (comic {rand_num}): {resp.status}")
                    return await interaction.followup.send("❌ Failed to fetch XKCD comic.", ephemeral=True)
                comic_data = await resp.json()

        embed = discord.Embed(
            title=f"XKCD Comic #{comic_data['num']}: {comic_data['title']}",
            url=f"https://xkcd.com/{comic_data['num']}/",
            color=0x3498db
        )
        embed.set_image(url=comic_data['img'].replace('-small', ''))  # Use full resolution image
        embed.set_footer(text=comic_data.get('alt', ''))

        await interaction.followup.send(embed=embed, ephemeral=False)

    @app_commands.command(name="urban", description="Search Urban Dictionary.")
    @app_commands.describe(term="The term to search for")
    @cooldown(cl=5, tm=10.0, ft=3)
    async def urban(self, interaction: discord.Interaction, term: str = None):
        log.trace(f"Urban invoked by {interaction.user.id}: {term}")
        await interaction.response.defer(ephemeral=False)
        
        if term:
            api_url = f"https://api.urbandictionary.com/v0/define?term={term}"
        else:
            api_url = "https://api.urbandictionary.com/v0/random"

        session = self.bot.http_session
        if not session:
            log.error("HTTP session missing for Urban Dictionary")
            await interaction.followup.send("❌ HTTP session not available.", ephemeral=True)
            return
        try:
            async with session.get(api_url) as resp:
                if resp.status != 200:
                    log.error(f"Urban Dictionary API failed: {resp.status}")
                    await interaction.followup.send("❌ Failed to fetch definition.", ephemeral=True)
                    return
                data = await resp.json()
        except Exception as e:
            log.error(f"Urban Dictionary error: {e}")
            await interaction.followup.send("❌ Error contacting Urban Dictionary.", ephemeral=True)
            return

        entries = data.get("list", [])
        if not entries:
            log.warningtrace(f"No Urban Dictionary definition found for: {term}")
            await interaction.followup.send(f"❌ No definitions found for `{term}`." if term else "❌ No random definitions found.", ephemeral=True)
            return

        log.successtrace(f"Urban Dictionary definition fetched for {interaction.user.id}: {term or 'random'}")
        entry = random.choice(entries)
        definition = entry.get("definition", "No definition provided.").strip()
        example = entry.get("example", "").strip()
        thumbs_up = entry.get("thumbs_up", 0)
        thumbs_down = entry.get("thumbs_down", 0)
        author = entry.get("author", "Unknown")
        word = entry.get("word", term or "random")

        embed = discord.Embed(
            title=f"Urban Dictionary: {word}",
            color=0xdfdc00
        )
        embed.add_field(name="Definition", value=(definition[:1000] + "…") if len(definition) > 1000 else definition, inline=False)
        if example:
            embed.add_field(name="Example", value=(example[:1000] + "…") if len(example) > 1000 else example, inline=False)
        embed.add_field(name="👍 Upvotes", value=str(thumbs_up), inline=True)
        embed.add_field(name="👎 Downvotes", value=str(thumbs_down), inline=True)
        embed.set_footer(text=f"Defined by {author}")

        await interaction.followup.send(embed=embed, ephemeral=False)

    @app_commands.command(name="debug", description="Shows system and bot stats.")
    @cooldown(cl=16, tm=15.0, ft=3)
    async def debug(self, interaction: discord.Interaction):
        log.trace(f"Debug invoked by {interaction.user.id}")
        await interaction.response.defer(ephemeral=True)

        # Core data
        current_shard = interaction.guild.shard_id if interaction.guild else interaction.client.shard_id
        cpu_count = psutil.cpu_count(logical=True)
        cpu_freq = psutil.cpu_freq()
        total_mem = psutil.virtual_memory().total / (1024 * 1024)
        used_mem = psutil.virtual_memory().used / (1024 * 1024)
        mem = self.process.memory_full_info()
        latency = round(self.bot.latency * 1000, 2)
        now = time.time()
        bot_start_time = getattr(self.bot, "start_time", time.time())
        if bot_start_time is None:
            uptime_seconds = 0
        else:
            uptime_seconds = int(now - bot_start_time)

        shard_stats = []
        for shard_id, shard in self.bot.shards.items():
            shard_latency = round(shard.latency * 1000, 2)
            # estimate memory per shard
            mem_mb = mem.rss / (len(self.bot.shards) or 1) / (1024 * 1024)
            marker = " < You're here" if shard_id == current_shard else ""
            shard_stats.append(f"`Shard {shard_id}` | 🧠 {mem_mb:.1f} MB | 📶 {shard_latency} ms{marker}")

        def format_uptime(seconds: int):
            days, seconds = divmod(seconds, 86400)
            hours, seconds = divmod(seconds, 3600)
            minutes, seconds = divmod(seconds, 60)
            parts = []
            if days: parts.append(f"{days}d")
            if hours: parts.append(f"{hours}h")
            if minutes: parts.append(f"{minutes}m")
            parts.append(f"{seconds}s")
            return " ".join(parts)

        # Embed
        embed = discord.Embed(
            title="📊 Bot Diagnostics",
            color=discord.Color.blurple(),
            description=f"**Status snapshot for:** `{self.bot.user}`"
        )

        embed.add_field(
            name="📡 Core",
            value=(
                f"**Latency:** `{latency}` ms\n"
                f"**Uptime:** `{format_uptime(uptime_seconds)}`\n"
                f"**Python:** `{platform.python_version()}`\n"
                f"**discord.py:** `{discord.__version__}`"
            ),
            inline=False
        )

        embed.add_field(
            name="🧠 System",
            value=(
                f"**CPU:** `{cpu_count}` cores @ `{cpu_freq.current:.0f}` MHz\n"
                f"**RAM:** `{used_mem:.0f}` / `{total_mem:.0f}` MB\n"
                f"**OS:** {platform.system()} {platform.release()}"
            ),
            inline=True
        )

        embed.add_field(
            name="🌍 Network",
            value=(
                f"**Guilds:** `{len(self.bot.guilds)}`\n"
                f"**Users:** `{len(self.bot.users)}`\n"
            ),
            inline=True
        )

        embed.add_field(
            name="🧩 Shards",
            value="\n".join(shard_stats),
            inline=False
        )
        footer_note = []
        if IS_ALPHA:
            footer_note.append("Alpha version")
        else:
            footer_note.append("Stable version")
        footer_note = " | ".join(footer_note)

        embed.set_footer(text=f"{footer_note} | {interaction.client.user.name}")
        log.successtrace(f"Debug info sent to {interaction.user.id}")
        await interaction.followup.send(embed=embed, ephemeral=False)

    @app_commands.command(name="base64", description="Encode or decode a message in Base64.")
    @app_commands.describe(action="Choose to encode or decode", message="The message to encode/decode")
    @cooldown(cl=10, tm=30.0, ft=3)
    async def base64_command(self, interaction: discord.Interaction, action: str, message: str):
        log.trace(f"Base64 invoked by {interaction.user.id}: {action}")
        await interaction.response.defer(ephemeral=False)
        action = action.lower()
        if action not in ["encode", "decode"]:
            log.warningtrace(f"Invalid base64 action by {interaction.user.id}: {action}")
            await interaction.followup.send("❌ Action must be either 'encode' or 'decode'.", ephemeral=True)
            return

        if action == "encode":
            encoded_bytes = base64.b64encode(message.encode('utf-8'))
            encoded_str = encoded_bytes.decode('utf-8')
            embed = discord.Embed(title="🔐 Base64 Encode", color=0x3498db)
            embed.add_field(name="Original Message", value=f"`{message}`", inline=False)
            embed.add_field(name="Encoded Message", value=f"`{encoded_str}`", inline=False)
        else:  # decode
            try:
                decoded_bytes = base64.b64decode(message.encode('utf-8'))
                decoded_str = decoded_bytes.decode('utf-8')
                embed = discord.Embed(title="🔓 Base64 Decode", color=0x3498db)
                embed.add_field(name="Encoded Message", value=f"`{message}`", inline=False)
                embed.add_field(name="Decoded Message", value=f"`{decoded_str}`", inline=False)
            except Exception as e:
                log.warningtrace(f"Base64 decode failed for {interaction.user.id}: {e}")
                await interaction.followup.send("❌ Failed to decode the provided Base64 message. Ensure it is valid Base64.", ephemeral=True)
                return

        log.successtrace(f"Base64 {action} successful for {interaction.user.id}")
        await interaction.followup.send(embed=embed, ephemeral=False)

    @app_commands.command(name="explode", description="Always returns an error! Used for testing error handling.")
    @cooldown(cl=2, tm=10.0, ft=3)
    async def explode(self, interaction: discord.Interaction):
       log.trace(f"Explode invoked by {interaction.user.id}")
       await interaction.response.defer(ephemeral=False)

       if interaction.user.id != BOT_OWNER:
           log.warningtrace(f"Explode blocked for non-owner {interaction.user.id}")
           return await interaction.followup.send("❌ This is a dev only command!")

       toresult = 1 / 0  # This will raise a ZeroDivisionError
       await interaction.followup.send(f"The result is {toresult}- Wait how did you see this?", ephemeral=False)

    @app_commands.command(name="slowpoke", description="A command that intentionally responds slowly.")
    @cooldown(cl=2, tm=4.0, ft=3)
    async def slowpoke(self, interaction: discord.Interaction):
        log.trace(f"Slowpoke invoked by {interaction.user.id}")
        await interaction.response.defer()

        if interaction.user.id != BOT_OWNER:
            log.warningtrace(f"Slowpoke blocked for non-owner {interaction.user.id}")
            return await interaction.followup.send("❌ This is a dev only command!")

        await asyncio.sleep(5) # This will outtime the timeout limit
        log.successtrace(f"Slowpoke finished for {interaction.user.id}")
        await interaction.followup.send("🐢 Sorry for the wait! I'm a bit slow today.")

    @app_commands.command(name="exchange", description="Convert between two currencies (e.g. USD → EUR).")
    @app_commands.describe(amount="Amount to convert", from_currency="Base currency (e.g. USD)", to_currency="Target currency (e.g. EUR)")
    @cooldown(cl=10, tm=30.0, ft=3)
    async def exchange(self, interaction: Interaction, amount: float, from_currency: str, to_currency: str):
        log.trace(f"Exchange invoked by {interaction.user.id}: {amount} {from_currency} -> {to_currency}")
        await interaction.response.defer(ephemeral=False)

        # basic validation
        if amount <= 0:
            log.warningtrace(f"Invalid exchange amount by {interaction.user.id}: {amount}")
            return await interaction.followup.send("❌ Amount must be positive.", ephemeral=True)
        if amount > MAX_AMOUNT:
            log.warningtrace(f"Exchange amount exceeds limit by {interaction.user.id}: {amount}")
            return await interaction.followup.send("🚫 Amount exceeds safe conversion limit (1e8).", ephemeral=True)

        from_currency = from_currency.strip().upper()
        to_currency = to_currency.strip().upper()

        if not re.fullmatch(r"[A-Z]{3}", from_currency) or not re.fullmatch(r"[A-Z]{3}", to_currency):
            log.warningtrace(f"Invalid currency codes by {interaction.user.id}: {from_currency}, {to_currency}")
            return await interaction.followup.send("❌ Invalid currency codes (use 3-letter ISO codes like USD, EUR).", ephemeral=True)

        # cache lookup
        now = time.time()
        cached = exchange_cache.get(from_currency)
        if cached and now - cached["timestamp"] < CACHE_DURATION:
            rates = cached["rates"]
        else:
            url = f"https://open.er-api.com/v6/latest/{from_currency}"
            session = self.bot.http_session
            if not session:
                log.error("HTTP session missing for exchange command")
                return await interaction.followup.send("❌ HTTP session not available.", ephemeral=True)
            try:
                resp = await session.get(url, timeout=10)
                data = await resp.json()
            except Exception as e:
                log.error(f"Exchange API error: {e}")
                return await interaction.followup.send(f"❌ API error: {e}", ephemeral=True)

            if data.get("result") != "success":
                log.error(f"Exchange API returned failure: {data}")
                return await interaction.followup.send("❌ Failed to retrieve exchange data.", ephemeral=True)

            rates = data.get("rates", {})
            exchange_cache[from_currency] = {"timestamp": now, "rates": rates}

        if to_currency not in rates:
            log.warningtrace(f"Target currency not found: {to_currency}")
            return await interaction.followup.send(f"❌ Target currency `{to_currency}` not available.", ephemeral=True)

        rate = rates[to_currency]
        converted = amount * rate
        log.successtrace(f"Exchange successful for {interaction.user.id}: {amount} {from_currency} -> {converted} {to_currency}")

        embed = Embed(title="💱 Currency Exchange", color=0x00AAFF)
        embed.add_field(name="From", value=f"`{amount:,.2f} {from_currency}`", inline=True)
        embed.add_field(name="To", value=f"`{converted:,.2f} {to_currency}`", inline=True)
        embed.add_field(name="Rate", value=f"1 {from_currency} → {rate:.4f} {to_currency}", inline=False)
        embed.set_footer(text="Rates cached up to 24 h | Data: open.er-api.com")

        await interaction.followup.send(embed=embed)

    @app_commands.command(name="heights", description="Convert between meters, feet, and inches.")
    @app_commands.describe(value="Height value (positive number)", unit="Unit of input (m, ft, or in)")
    @cooldown(cl=10, tm=30.0, ft=3)
    async def heights(self, interaction: Interaction, value: float, unit: str):
        log.trace(f"Heights invoked by {interaction.user.id}: {value} {unit}")
        await interaction.response.defer(ephemeral=False)

        if value <= 0:
            log.warningtrace(f"Invalid height value by {interaction.user.id}: {value}")
            return await interaction.followup.send("❌ Height must be positive.", ephemeral=True)
        if value > MAX_VALUE:
            log.warningtrace(f"Height value exceeds limit by {interaction.user.id}: {value}")
            return await interaction.followup.send("🚫 Height too large (max 1e8).", ephemeral=True)

        unit = unit.strip().lower()
        embed = Embed(title="📏 Height Conversion", color=0x33CC33)

        try:
            if unit in ("m", "meter", "meters"):
                meters = value
                feet_total = meters * 3.28084
                feet = int(feet_total)
                inches = (feet_total - feet) * 12
                embed.add_field(name="Input", value=f"`{meters:.3f} m`", inline=False)
                embed.add_field(name="Feet/Inches", value=f"`{feet} ft {inches:.1f} in`", inline=False)
                embed.add_field(name="Inches", value=f"`{feet_total*12:.1f} in`", inline=False)

            elif unit in ("ft", "feet", "foot"):
                feet = value
                meters = feet * 0.3048
                inches_total = feet * 12
                embed.add_field(name="Input", value=f"`{feet:.3f} ft`", inline=False)
                embed.add_field(name="Meters", value=f"`{meters:.3f} m`", inline=False)
                embed.add_field(name="Inches", value=f"`{inches_total:.1f} in`", inline=False)

            elif unit in ("in", "inch", "inches"):
                inches = value
                meters = inches * 0.0254
                feet_total = inches / 12
                feet = int(feet_total)
                rem_in = (feet_total - feet) * 12
                embed.add_field(name="Input", value=f"`{inches:.1f} in`", inline=False)
                embed.add_field(name="Meters", value=f"`{meters:.3f} m`", inline=False)
                embed.add_field(name="Feet/Inches", value=f"`{feet} ft {rem_in:.1f} in`", inline=False)

            else:
                log.warningtrace(f"Invalid height unit by {interaction.user.id}: {unit}")
                return await interaction.followup.send("❌ Unit must be `m`, `ft`, or `in`.", ephemeral=True)

        except Exception as e:
            log.error(f"Height conversion error: {e}")
            return await interaction.followup.send(f"❌ Conversion error: {e}", ephemeral=True)

        log.successtrace(f"Height conversion successful for {interaction.user.id}")
        await interaction.followup.send(embed=embed)

class FunCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
    
    async def cog_load(self):
        self.bot.tree.add_command(FunCommands(self.bot))

async def setup(bot):
    await bot.add_cog(FunCog(bot))