import discord
import time, random, re, asyncio, math, io, aiohttp, subprocess, platform, threading, json, datetime
from typing import Optional
from discord.ext import commands
from discord import app_commands
from discord import Interaction
from config import cooldown, safe_command
import CloudflarePing as cf

# Constants for dice limits
MAX_DICE = 100
MAX_SIDES = 1000

class Fun(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # table tennis?
    # @safe_command(timeout=15.0)
    @app_commands.command(name="ping", description="Check the bot's response time!")
    @cooldown(10)
    async def ping(self, interaction: discord.Interaction):
        start_time = time.perf_counter()
        await interaction.response.defer(ephemeral=False)
        end_time = time.perf_counter()
        thinking_time = (end_time - start_time) * 1000
        latency = round(self.bot.latency * 1000, 2)

        embed = discord.Embed(title="🏓 Pong!", color=0x00FF00)
        embed.add_field(name="📡 API Latency", value=f"`{latency} ms`", inline=True)
        embed.add_field(name="⏳ Thinking Time", value=f"`{thinking_time:.2f} ms`", inline=True)

        # Add cached Cloudflare ping info (if available)
        try:
            cf_cache = await cf.get_cached_pings()
            ipv4 = cf_cache.get("ipv4")
            ipv6 = cf_cache.get("ipv6")
            ts = cf_cache.get("ts")

            if ipv4 is None:
                embed.add_field(name="🟠 CF IPv4 RTT", value="N/A", inline=True)
            else:
                embed.add_field(name="🟠 CF IPv4 RTT", value=f"`{ipv4:.1f} ms`", inline=True)

            if ipv6 is None:
                embed.add_field(name="🟠 CF IPv6 RTT", value="N/A", inline=True)
            else:
                embed.add_field(name="🟠 CF IPv6 RTT", value=f"`{ipv6:.1f} ms`", inline=True)

            if ts:
                embed.set_footer(text=f"CF cached: {datetime.datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S')}")
        except Exception:
            embed.add_field(name="🟠 CF RTT", value="Error reading cache", inline=True)

        await interaction.followup.send(embed=embed, ephemeral=False)

    # the bane of my existance
    # @safe_command(timeout=30.0)
    @app_commands.command(name="roll", description="Roll a set of dice!")
    @app_commands.describe(dice="Dice expression to roll, type 'help' for syntax.", expand="Show detailed breakdown of the roll")
    @cooldown(5)
    async def roll(self, interaction: discord.Interaction, dice: str, expand: bool = False):
        await interaction.response.defer(ephemeral=False)

        # Quick help
        if dice.strip().lower() == "help":
            HELP_TEXT = (
                "🎲 **Dice Roller Help**\n\n"
                "Syntax: combine terms with + or -: `1d20 + 1d4 - 2 + 3d6k2`\n\n"
                "> `XdY` — roll X Y-sided dice\n"
                "> `XdYkN` / `XdYD N` — keep highest N / drop lowest N (per-group). NOTE: **drop uses uppercase `D`** to avoid ambiguity with the dice `d`.\n\n"
                "> numeric terms like `+2` or `-1` are constants\n\n"
                "> `!` / `!!` / `!p` / `!!p` — explode / compound / penetrate / compound+penetrate\n"
                "Tip: pass the slash option `expand=True` for a full breakdown."
            )
            help_embed = discord.Embed(title="🎲 Dice Roller Help", description=HELP_TEXT, color=0x3498db)
            await interaction.followup.send(embed=help_embed, ephemeral=False)
            return

        # Tokenize into signed parts: supports +1d20, -2, +3d6k1!!p etc.
        sanitized = dice.replace(" ", "")
        sanitized_orig = sanitized  # keep the full original (contains & modifiers) for later parsing of &N+Z

        # Remove &N±Z fragments BEFORE tokenizing so the numeric N inside them is not treated as a standalone constant.
        # We'll still parse the actual & modifiers later (see ampersand_matches step further down).
        sanitized_no_amp = re.sub(r'&\d+[+-]\d+', '', sanitized_orig)

        token_pattern = re.compile(r'([+-]?)(\d+[dD](?:!{1,2}(?:p)?)?\d+(?:[kK]\d+|D\d+)?|\d+)')
        tokens = token_pattern.findall(sanitized_no_amp)
        if not tokens:
            await interaction.followup.send("❌ **Invalid format!** Do /roll dice: help for syntax and examples", ephemeral=False)
            return

        def parse_group(text):
            if text.isdigit():
                return {"type": "const", "value": int(text)}
            m = re.match(r'(?P<num>\d+)[dD](?P<explode>!{1,2}p?|!p?)?(?P<sides>\d+)(?P<kd>(?:[kK]\d+|D\d+))?', text)
            if not m:
                return None
            return {
                "type": "dice",
                "num": int(m.group("num")),
                "sides": int(m.group("sides")),
                "explode": m.group("explode") or "",
                "keepdrop": m.group("kd") or ""
            }

        groups = []
        total_dice_count = 0
        for sign, body in tokens:
            parsed = parse_group(body)
            if not parsed:
                await interaction.followup.send(f"❌ **Couldn't parse token:** `{body}`", ephemeral=False)
                return
            parsed["sign"] = -1 if sign == "-" else 1
            groups.append(parsed)
            if parsed["type"] == "dice":
                total_dice_count += parsed["num"]

        # Limits
        try:
            MAX_DICE
            MAX_SIDES
        except NameError:
            MAX_DICE, MAX_SIDES = 1000, 10000

        if total_dice_count > MAX_DICE:
            await interaction.followup.send(f"❌ **Too many dice in total!** Limit: `{MAX_DICE}` dice.", ephemeral=False)
            return
        for g in groups:
            if g["type"] == "dice" and g["sides"] > MAX_SIDES:
                await interaction.followup.send(f"❌ **Die with too many sides!** Limit: `{MAX_SIDES}` sides.", ephemeral=False)
                return

        rng = random.Random()

        def roll_die(sides):
            return rng.randint(1, sides)

        def resolve_explosions_for_die(first_roll, sides, explode_flag, max_depth=100):
            """
            Returns (value_contrib, chain_list, raw_chain)
            - chain_list: the values that will be shown/added (penetrating subtracts 1 on extras)
            - raw_chain: raw face values used to test for further explosions (used only internally)
            """
            chain_display = [first_roll]
            raw_chain = [first_roll]
            if not explode_flag:
                return first_roll, chain_display, raw_chain

            is_compound = explode_flag.startswith("!!")
            is_penetrate = "p" in explode_flag
            depth = 0

            if is_compound:
                # only chain if first == max
                if first_roll != sides:
                    return first_roll, chain_display, raw_chain
                # roll until we break the chain
                while depth < max_depth:
                    depth += 1
                    nxt_raw = roll_die(sides)
                    raw_chain.append(nxt_raw)
                    nxt_display = nxt_raw - 1 if is_penetrate else nxt_raw
                    chain_display.append(nxt_display)
                    if nxt_raw != sides:
                        break
                return sum(chain_display), chain_display, raw_chain
            else:
                # normal explode (possibly penetrating)
                total = first_roll
                raw_last = first_roll
                while depth < max_depth and raw_last == sides:
                    depth += 1
                    nxt_raw = roll_die(sides)
                    raw_chain.append(nxt_raw)
                    nxt_display = nxt_raw - 1 if is_penetrate else nxt_raw
                    chain_display.append(nxt_display)
                    total += nxt_display
                    raw_last = nxt_raw
                return total, chain_display, raw_chain

        # Collect per-group and per-die data
        group_summaries = []
        flat_kept_entries = []  # flattened list of dicts for kept dice to apply global &-modifiers
        const_total = 0  # sum of constant numeric tokens (signed)
        footer_keepdrop = []

        for gi, g in enumerate(groups):
            if g["type"] == "const":
                const_total += g["sign"] * g["value"]
                group_summaries.append({
                    "kind": "const",
                    "label": f"{g['sign'] * g['value']}",
                    "pre_keep_sum": g['sign'] * g['value'],
                    "post_mod_sum": g['sign'] * g['value'],
                    "details": None
                })
                continue

            per_die = []
            for _ in range(g["num"]):
                first = roll_die(g["sides"])
                contrib, chain_display, raw_chain = resolve_explosions_for_die(first, g["sides"], g["explode"])
                per_die.append({
                    "raw_first": first,
                    "pre_contrib": contrib,       # contribution BEFORE any & modifiers
                    "chain_display": chain_display,
                    "raw_chain": raw_chain
                })

            # apply keep/drop per group (operates on pre_contrib)
            kd = g["keepdrop"]
            if kd:
                # kd now is either like 'k2' / 'K2' or 'D2' (drop must be uppercase D)
                first_char = kd[0]
                if first_char in ('k', 'K'):
                    typ = 'k'
                elif first_char == 'D':
                    typ = 'D'
                else:
                    # fallback (shouldn't happen with new regex)
                    typ = first_char.lower()
                n = int(kd[1:])

                sorted_by = sorted(per_die, key=lambda x: x["pre_contrib"], reverse=True)
                if typ == "k":
                    kept = sorted_by[:n]
                    dropped = sorted_by[n:]
                    footer_keepdrop.append(f"{g['num']}d{g['sides']}k{n}")
                else:  # typ == "D"
                    dropped = sorted_by[:n]
                    kept = sorted_by[n:]
                    footer_keepdrop.append(f"{g['num']}d{g['sides']}D{n}")
            else:
                kept = per_die
                dropped = []

            pre_keep_sum = sum(d["pre_contrib"] for d in kept)
            # Add entries to flat_kept_entries for global &-style modifiers (preserve order groups->dice)
            for d in kept:
                flat_kept_entries.append({
                    "group_index": gi,
                    "base_value": d["pre_contrib"],  # will be adjusted by &N later
                    "chain_display": d["chain_display"],
                    "raw_first": d["raw_first"]
                })

            group_summaries.append({
                "kind": "dice",
                "label": f"{g['sign'] if g['sign']<0 else ''}{g['num']}d{g['sides']}{g['explode']}{g['keepdrop']}",
                "pre_keep_sum": g['sign'] * pre_keep_sum,  # sign applied here; & modifiers applied globally later
                "post_mod_sum": None,  # to be filled after global modifiers applied
                "details": per_die,
                "sign": g["sign"]
            })

        # Detect legacy &N+Z modifiers anywhere in the sanitized input
        ampersand_matches = re.findall(r'&(\d+)([+-]\d+)', sanitized_orig)
        ampersand_notes = []
        if ampersand_matches:
            # apply each match in order found — each modifies the first N kept dice in flat_kept_entries
            # keep deterministic: we apply them sequentially to the current values
            for cnt_str, flat_mod_str in ampersand_matches:
                cnt = int(cnt_str)
                flat_mod = int(flat_mod_str)
                applied = 0
                for e in flat_kept_entries:
                    if applied >= cnt:
                        break
                    e["base_value"] += flat_mod
                    applied += 1
                ampersand_notes.append(f"First {cnt} rolls: {flat_mod:+d}")

        # Now compute per-group post-mod sums from flat_kept_entries
        group_post_sums = {}
        for idx, gs in enumerate(group_summaries):
            if gs["kind"] == "const":
                group_post_sums[idx] = gs["post_mod_sum"]
                continue
            group_post_sums[idx] = 0

        for e in flat_kept_entries:
            gi = e["group_index"]
            # groups list corresponds to group_summaries in order; note sign must be applied from group_summaries
            sign = group_summaries[gi]["sign"]
            group_post_sums[gi] += sign * e["base_value"]

        # fill post_mod_sum into summaries
        for idx, gs in enumerate(group_summaries):
            if gs["kind"] == "const":
                # constants already have post_mod_sum set when appended earlier; ensure it's defined
                gs["post_mod_sum"] = gs.get("post_mod_sum", gs["pre_keep_sum"])
                continue
            # use the computed group_post_sums for dice groups
            gs["post_mod_sum"] = group_post_sums.get(idx, 0)
            # pre_keep_sum already had sign applied earlier

        pre_mod_total = sum(gs["pre_keep_sum"] for gs in group_summaries)
        post_mod_total = sum((gs["post_mod_sum"] if gs["post_mod_sum"] is not None else gs["pre_keep_sum"]) for gs in group_summaries)

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


    # @safe_command(timeout=15.0)
    @app_commands.command(name="8ball" , description="Ask the magic 8-ball a question!")
    @cooldown(5)
    async def eight_ball(self, interaction: discord.Interaction, question: str):
        await interaction.response.defer(ephemeral=False)
        if not question:
            return await interaction.followup.send("❌ **I'm not a mind reader! Ask a question!**", ephemeral=True)
        
        # too little responses, blegh!
        # instead we asked chatgpt for a bajillion more!
        responses = [
            # Classic
            "Yes", "No", "Maybe", "Definitely", "Absolutely not",
            "Ask again later", "I wouldn't count on it", "It's certain",
            "Don't hold your breath", "Yes, in due time",

            # Vague wisdom
            "The stars are unclear", "Signs point to yes", "Without a doubt",
            "My sources say no", "Reply hazy, try again", "Better not tell you now",
            "Cannot predict now", "Concentrate and ask again", "Outlook alright",
            "Presumably yes", "Very doubtful",

            # Sarcastic
            "Sure, and pigs might fly too", "Only if you believe hard enough",
            "Yeah, no.", "Not even in the multiverse", "When hell freezes over",
            "Ask your mom", "Absolutely. In your dreams.", "I plead the fifth",
            "If I had a coin, I'd flip it", "Define 'possible'...",

            # Meme-y
            "You already know the answer", "That’s a skill issue", "Cringe question tbh",
            "The Council has denied your request", "It is what it is", "Try Alt+F4",
            "Your chances are as good as Genshin gacha rates", "lmao no",
            "This message will self-destruct", "Roll a D20 and get back to me", "Ask again after a nap",
            "I'm not a therapist, but yes", "404: Answer not found",

            # Cryptic & cursed
            "The void whispers yes", "The answer lies beneath your bed",
            "You've already made your choice", "Don’t open the door tonight",
            "There’s something behind you", "Only the cursed know for sure",
            "It was never meant to be asked", "The prophecy says nothing of this",
            "RELEASE ME FROM THIS ORB", "Seek the ancient tomes for answers",

            # Chaotic neutral
            "Yes but also no", "No but also yes", "42", "Meh",
            "I flipped a coin but lost it", "You get what you get", 
            "You don’t want to know", "Absolutely. Wait, what was the question?",
            r"¯\_(ツ)_/¯", "Hold on, I'm updating my firmware", "Ask again in binary",
            "The answer is hidden in the code", "Why are you asking ME?",

            # Straight up lying
            "Yes, trust me bro", "No, but say yes anyway", 
            "Definitely. Just ignore the consequences", 
            "It’s fine. Probably.", "For legal reasons, I must say yes", "fo sho",
            "Yup, 100%", "Nah, just kidding", "Totally", "Of course, why not?", "What?",
            "Listen to your heart, it said yes (no)", "Come again? I was listening to music"
        ]

        answer = random.choice(responses)
        embed = discord.Embed(title="🎱 Magic 8-Ball", color=0x3498db)
        embed.add_field(name="Question", value=f"`{question}`", inline=False)
        embed.add_field(name="Answer", value=f"`{answer}`", inline=False)
        await interaction.followup.send(embed=embed, ephemeral=False)

    # @safe_command(timeout=15.0)
    @app_commands.command(name="hack", description="Hack another user! Totally 100% legit.")
    @cooldown(60)
    async def hack(self, interaction: discord.Interaction, target: discord.Member):
        await interaction.response.defer(ephemeral=False)
        if target == interaction.user:
            return await interaction.followup.send("❌ You can't hack yourself!", ephemeral=True)

        # Simulate hacking process with an elaborate "animation" and rising percentage
        message = await interaction.followup.send(f"💻 Hacking {target.mention}... Please wait...", ephemeral=False)

        steps = [
            "Bypassing firewall...",
            "Accessing mainframe...",
            "Decrypting passwords...",
            "Extracting data...",
            "Uploading virus...",
            "Finalizing hack..."
        ]
        total_steps = len(steps)
        percent_per_step = 100 // (total_steps + 1)
        progress = 0

        # Get the message object to edit
        msg = await interaction.original_response()

        for i, step in enumerate(steps):
            progress += percent_per_step
            bar = "█" * (progress // 10) + "░" * (10 - (progress // 10))
            await msg.edit(content=f"💻 Hacking {target.mention}...\n[{bar}] {progress}%\n{step}")
            await asyncio.sleep(1.2)

        # Finish at 100%
        await msg.edit(content=f"💻 Hacking {target.mention}...\n[██████████] 100%\n✅ Hack complete! All their cookies have been stolen and eaten!🍪")

    # @safe_command(timeout=10.0)
    @app_commands.command(name="info", description="Get information about the bot.")
    @cooldown(5)
    async def info_of_bot(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False)
        bot_user = self.bot.user

        now = time.time()
        bot_start_time = getattr(self.bot, "start_time", None)
        if bot_start_time is None:
            uptime_seconds = 0
        else:
            uptime_seconds = int(now - bot_start_time)

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
        async with aiohttp.ClientSession() as session:
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
            value=f"`{len(self.bot.tree.get_commands())}` slash commands available",
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
            value=f"`{platform.system()} {platform.release()}`",
            inline=True
        )
        embed.set_footer(text=f"Fun Fact: {fun_fact}")

        await interaction.followup.send(embed=embed, ephemeral=False)
    # stupid dum dum discord reserves bot_ for their own shit, i'm angry
    
    # @safe_command(timeout=10.0)
    @app_commands.command(name="serverinfo", description="Get information about current server")
    @cooldown(5)
    async def serverinfo(self, interaction: discord.Interaction, hidden: bool = False):
        await interaction.response.defer(ephemeral=False)
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

    # @safe_command(timeout=10.0)
    @app_commands.command(name="letter", description="Generate a random letter.")
    @cooldown(5)
    async def letter(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False)
        letter = random.choice("abcdefghijklmnopqrstuvwxyz")
        embed = discord.Embed(title="🔤 Random Letter", color=0x3498db)
        embed.add_field(name="Generated Letter", value=f"`{letter}`", inline=False)
        await interaction.followup.send(embed=embed, ephemeral=False) # What are we letter-gatekeeping now?

    # @safe_command(timeout=10.0)
    @app_commands.command(name="cat", description="Get a random cat image")
    @cooldown(5)
    async def cat(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False)
        async with aiohttp.ClientSession() as session:
            async with session.get("https://api.thecatapi.com/v1/images/search") as resp:
                if resp.status != 200:
                    await interaction.followup.send("😿 Failed to fetch a cat image.", ephemeral=True)
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

    # @safe_command(timeout=10.0)
    @app_commands.command(name="dog", description="Get a random dog image")
    @cooldown(5)
    async def dog(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False)
        async with aiohttp.ClientSession() as session:
            async with session.get("https://dog.ceo/api/breeds/image/random") as resp:
                if resp.status != 200:
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
    
    # @safe_command(timeout=10.0)
    @app_commands.command(name="help", description="Get a list of available commands (paginated).")
    @cooldown(2)
    async def help_command(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False)
        commands_list = [cmd for cmd in self.bot.tree.get_commands() if cmd.name != "help"]
        per_page = 12
        total_pages = (len(commands_list) + per_page - 1) // per_page

        def get_embed(page: int):
            embed = discord.Embed(title="🆘 Help - Available Commands", color=0x3498db)
            embed.description = f"Page {page+1}/{total_pages}\nHere are the commands you can use:"
            start = page * per_page
            end = start + per_page
            for command in commands_list[start:end]:
                embed.add_field(
                    name=f"/{command.name}",
                    value=command.description or "No description available",
                    inline=False
                )
            embed.set_footer(text="Use /<command_name> to execute a command.")
            return embed

        class HelpView(discord.ui.View):
            def __init__(self):
                super().__init__(timeout=60)
                self.page = 0

            @discord.ui.button(label="⏮️", style=discord.ButtonStyle.secondary)
            async def first(self, interaction_btn: discord.Interaction, button: discord.ui.Button):
                self.page = 0
                await interaction_btn.response.edit_message(embed=get_embed(self.page), view=self)

            @discord.ui.button(label="⬅️", style=discord.ButtonStyle.secondary)
            async def prev(self, interaction_btn: discord.Interaction, button: discord.ui.Button):
                if self.page > 0:
                    self.page -= 1
                    await interaction_btn.response.edit_message(embed=get_embed(self.page), view=self)
                else:
                    await interaction_btn.response.defer()

            @discord.ui.button(label="❌", style=discord.ButtonStyle.danger)
            async def close(self, interaction_btn: discord.Interaction, button: discord.ui.Button):
                await interaction_btn.response.edit_message(content="Help menu closed.", embed=None, view=None)

            @discord.ui.button(label="➡️", style=discord.ButtonStyle.secondary)
            async def next(self, interaction_btn: discord.Interaction, button: discord.ui.Button):
                if self.page < total_pages - 1:
                    self.page += 1
                    await interaction_btn.response.edit_message(embed=get_embed(self.page), view=self)
                else:
                    await interaction_btn.response.defer()

            @discord.ui.button(label="⏭️", style=discord.ButtonStyle.secondary)
            async def last(self, interaction_btn: discord.Interaction, button: discord.ui.Button):
                self.page = total_pages - 1
                await interaction_btn.response.edit_message(embed=get_embed(self.page), view=self)

            async def on_timeout(self):
                for item in self.children:
                    item.disabled = True

        await interaction.followup.send(embed=get_embed(0), view=HelpView(), ephemeral=False)

    # @safe_command(timeout=10.0)
    @app_commands.command(name="pokedex", description="Get information about a Pokémon.")
    @app_commands.describe(pokemon="The name/number of the Pokémon to look up, empty for random")
    @cooldown(10)
    async def pokedex(self, interaction: discord.Interaction, pokemon: str | None = None):
        await interaction.response.defer(ephemeral=False)
        if pokemon is None:
            # Get a random pokemon by ID (1-1025 as of current gen)
            poke_name = str(random.randint(1, 1025))
        else:
            poke_name = pokemon.lower().strip()
        api_url = f"https://pokeapi.co/api/v2/pokemon/{poke_name}"

        async with aiohttp.ClientSession() as session:
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

    # @safe_command(timeout=10.0)
    @app_commands.command(name="xkcd", description="Get a random XKCD comic.")
    @app_commands.describe(comic="The comic number to fetch (leave empty for random comic)")
    @cooldown(7)
    async def xkcd(self, interaction: discord.Interaction, comic: int | None = None):
        await interaction.response.defer(ephemeral=False)
        # First get the latest comic number
        async with aiohttp.ClientSession() as session:
            async with session.get("https://xkcd.com/info.0.json") as resp:
                if resp.status != 200:
                    await interaction.followup.send("❌ Failed to fetch XKCD comic.", ephemeral=True)
                    return
                latest_data = await resp.json()
                latest_num = latest_data['num']

            if comic:
                if comic < 1 or comic > latest_num:
                    await interaction.followup.send(f"❌ Comic number must be between 1 and {latest_num}.", ephemeral=True)
                    return
                async with session.get(f"https://xkcd.com/{comic}/info.0.json") as resp:
                    if resp.status != 200:
                        await interaction.followup.send("❌ Failed to fetch XKCD comic.", ephemeral=True)
                        return
                    comic_data = await resp.json()
            else:
                rand_num = random.randint(1, latest_num)
                async with session.get(f"https://xkcd.com/{rand_num}/info.0.json") as resp:
                    if resp.status != 200:
                        await interaction.followup.send("❌ Failed to fetch XKCD comic.", ephemeral=True)
                        return
                    comic_data = await resp.json()

        embed = discord.Embed(
            title=f"XKCD Comic #{comic_data['num']}: {comic_data['title']}",
            url=f"https://xkcd.com/{comic_data['num']}/",
            color=0x3498db
        )
        embed.set_image(url=comic_data['img'].replace('-small', ''))  # Use full resolution image
        embed.set_footer(text=comic_data.get('alt', ''))

        await interaction.followup.send(embed=embed, ephemeral=False)

    # @safe_command(timeout=10.0)
    @app_commands.command(name="urban", description="Get the Urban Dictionary definition of a term.")
    @app_commands.describe(term="The term to look up (leave empty for random definition)")
    @cooldown(10)
    async def urban(self, interaction: discord.Interaction, term: str | None = None):
        await interaction.response.defer(ephemeral=False)

        # If no term provided, use the random endpoint
        if term:
            api_url = f"https://api.urbandictionary.com/v0/define?term={term.strip()}"
        else:
            api_url = "https://api.urbandictionary.com/v0/random"

        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(api_url) as resp:
                    if resp.status != 200:
                        await interaction.followup.send("❌ Failed to fetch definition.", ephemeral=True)
                        return
                    data = await resp.json()
            except Exception:
                await interaction.followup.send("❌ Error contacting Urban Dictionary.", ephemeral=True)
                return

            # For random endpoint or define, pick a random entry from the list if multiple
            entries = data.get("list", [])
            if not entries:
                await interaction.followup.send(f"❌ No definitions found for `{term}`." if term else "❌ No random definitions found.", ephemeral=True)
                return

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

    # @safe_command(timeout=5.0)
    # @app_commands.command(name="explode", description="Always returns an error! Used for testing error handling.")
    # @cooldown(2)
    # async def explode(self, interaction: discord.Interaction):
    #    await interaction.response.defer(ephemeral=False)
    #    toresult = 1 / 0  # This will raise a ZeroDivisionError
    #    await interaction.followup.send(f"The result is {toresult}- Wait how did you see this?", ephemeral=False)

    # @safe_command(timeout=2.0)
    # @app_commands.command(name="slowpoke", description="A command that intentionally responds slowly.")
    # @cooldown(2)
    # async def slowpoke(self, interaction: discord.Interaction):
    #    await interaction.response.defer(ephemeral=False)
    #    await asyncio.sleep(5)  # Intentional delay longer than set timeout
    #    await interaction.followup.send("🐢 Sorry for the wait! I'm a bit slow today.", ephemeral=False)

async def setup(bot):
    await bot.add_cog(Fun(bot))

# grgrgrgrg so close to 1000 lines in the fun cog alone grgrgrgrgr