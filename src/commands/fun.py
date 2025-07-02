import discord
import time, random, re, asyncio, math, io, aiohttp, subprocess, platform, threading, json
from discord.ext import commands
from discord import app_commands
from discord import Interaction
from config import cooldown

# Constants for dice limits
MAX_DICE = 100
MAX_SIDES = 1000

class Fun(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # table tennis?
    @app_commands.command(name="ping", description="Check the bot's response time!")
    @cooldown(10)
    async def ping(self, interaction: discord.Interaction):
        start_time = time.perf_counter()
        await interaction.response.defer(ephemeral=False)
        end_time = time.perf_counter()
        thinking_time = (end_time - start_time) * 1000
        latency = round(self.bot.latency * 1000, 2)

        embed = discord.Embed(title="🏓 Pong!", color=0x00FF00)
        embed.add_field(name="📡 API Latency", value=f"`{latency}ms`", inline=True)
        embed.add_field(name="⏳ Thinking Time", value=f"`{thinking_time:.2f}ms`", inline=True)
        await interaction.followup.send(embed=embed, ephemeral=False)

    # the bane of my existance
    @app_commands.command(name="roll", description="Roll dice with advanced options (exploding, keep/drop, modifiers).")
    @cooldown(5)
    async def roll(self, interaction: discord.Interaction, dice: str):
        await interaction.response.defer(ephemeral=False) # Deffer by default, take a WHILE.
        # Regex pattern for dice: Xd!Y, Xd!!Y, Xd!Y+Z, XdYkN, XdYdN, etc.
        pattern = re.compile(
            r"(?P<num>\d+)[dD](?P<explode>!?|!!|!\?)?(?P<sides>\d+)"
            r"(?P<keepdrop>[kdKD]\d+)?"
            r"(?P<modifiers>(?:[&+-]\d+)*)"
        )
        match = pattern.fullmatch(dice.replace(" ", ""))

        if not match:
            await interaction.followup.send(
                "❌ **Invalid format!** Use `XdY`, `Xd!Y`, `Xd!!Y`, `XdYkN`, `XdYdN`, `XdY+Z`, etc. Examples: `2d6`, `3d!10+5`, `4d8k2+3`, `5d6!!d2-1`.",
                ephemeral=False
            )
            return

        num_dice = int(match.group("num"))
        die_sides = int(match.group("sides"))
        explode_flag = match.group("explode") or ""
        keepdrop = match.group("keepdrop")
        modifiers = match.group("modifiers") or ""

        if num_dice > MAX_DICE or die_sides > MAX_SIDES:
            await interaction.followup.send(
                f"❌ **Too many dice!** Limit: `{MAX_DICE}d{MAX_SIDES}`.",
                ephemeral=False
            )
            return

        # Exploding dice logic
        def roll_die(sides):
            return random.randint(1, sides)

        def explode_once(rolls, sides, compound=False, show_all=False, depth=0, max_depth=10):
            """Handles single or compounding explosions, returns (final_rolls, all_rolls_for_display)"""
            if depth >= max_depth:
                return rolls, rolls if show_all else [sum(rolls)]
            new_rolls = []
            all_rolls = []
            for roll in rolls:
                if roll == sides:
                    if compound:
                        # Compound: keep rolling and sum all
                        chain = [roll]
                        while True:
                            if len(chain) > max_depth:
                                break
                            next_roll = roll_die(sides)
                            chain.append(next_roll)
                            if next_roll != sides:
                                break
                        new_rolls.append(sum(chain))
                        if show_all:
                            all_rolls.append(chain)
                        else:
                            all_rolls.append([sum(chain)])
                    else:
                        # Normal explode: roll again and add to pool
                        chain = [roll]
                        for _ in range(max_depth):
                            next_roll = roll_die(sides)
                            chain.append(next_roll)
                            if next_roll != sides:
                                break
                        new_rolls.extend(chain)
                        if show_all:
                            all_rolls.append(chain)
                        else:
                            all_rolls.extend(chain)
                else:
                    new_rolls.append(roll)
                    if show_all:
                        all_rolls.append([roll])
                    else:
                        all_rolls.append(roll)
            if compound or show_all:
                # Only one pass needed for compound or show_all
                return new_rolls, all_rolls
            # For normal explode, check for further explosions recursively
            if any(r == sides for r in new_rolls):
                return explode_once(new_rolls, sides, compound, show_all, depth + 1, max_depth)
            return new_rolls, all_rolls

        # Roll initial dice
        rolls = [roll_die(die_sides) for _ in range(num_dice)]
        all_rolls_for_display = [ [r] for r in rolls ]
        mod_details = []
        explosion_type = None

        # Handle explosion flags
        if explode_flag:
            if explode_flag == "!":
                explosion_type = "normal"
                rolls, all_rolls_for_display = explode_once(rolls, die_sides, compound=False, show_all=False)
                mod_details.append("Exploding dice: normal (!)")
            elif explode_flag == "!!":
                explosion_type = "compound"
                rolls, all_rolls_for_display = explode_once(rolls, die_sides, compound=True, show_all=False)
                mod_details.append("Exploding dice: compounding (!!)")
            elif explode_flag == "!?":
                explosion_type = "showall"
                _, all_rolls_for_display = explode_once(rolls, die_sides, compound=False, show_all=True)
                # Flatten for result, but keep all for display
                rolls = [sum(chain) for chain in all_rolls_for_display]
                mod_details.append("Exploding dice: show all rolls (!?)")

        # Handle keep/drop
        kept = None
        dropped = None
        if keepdrop:
            kd = keepdrop.lower()
            if kd.startswith("k"):
                k = int(kd[1:])
                kept = sorted(rolls, reverse=True)[:k]
                mod_details.append(f"Keep highest {k} (k{k})")
            elif kd.startswith("d"):
                d = int(kd[1:])
                dropped = sorted(rolls)[:d]
                mod_details.append(f"Drop lowest {d} (d{d})")

        # Apply modifiers (including & for partial application)
        results = rolls[:]
        if modifiers:
            mods = re.findall(r"([&+-]\d+)", modifiers)
            i = 0
            while i < len(mods):
                mod = mods[i]
                if mod.startswith("&"):
                    try:
                        count = int(mod[1:])
                        if i + 1 < len(mods):
                            next_mod = mods[i + 1]
                            if next_mod.startswith("+") or next_mod.startswith("-"):
                                flat_mod = int(next_mod)
                                results = [r + flat_mod if idx < count else r for idx, r in enumerate(results)]
                                mod_details.append(f"First {count} Rolls: `{flat_mod}`")
                                i += 2
                                continue
                    except ValueError:
                        await interaction.followup.send(
                            "❌ **Invalid & modifier!** Must be an integer.",
                            ephemeral=False
                        )
                        return
                elif mod.startswith("+") or mod.startswith("-"):
                    try:
                        flat_mod = int(mod)
                        results = [r + flat_mod for r in results]
                        mod_details.append(f"All Rolls: `{flat_mod}`")
                    except ValueError:
                        await interaction.followup.send(
                            "❌ **Invalid modifier!** Modifiers must be integers.",
                            ephemeral=False
                        )
                        return
                i += 1

        # Prepare display text
        def format_all_rolls(all_rolls):
            # For showall, display each chain
            out = []
            for chain in all_rolls:
                if isinstance(chain, list) and len(chain) > 1:
                    out.append(" + ".join(map(str, chain)))
                else:
                    out.append(str(chain[0]) if isinstance(chain, list) else str(chain))
            return ", ".join(out)

        rolls_text = (
            format_all_rolls(all_rolls_for_display)
            if explosion_type == "showall"
            else ", ".join(map(str, rolls))
        )
        final_text = ", ".join(map(str, results))

        # Show keep/drop results
        if kept is not None:
            kept_text = ", ".join(map(str, kept))
            mod_details.append(f"Kept: `{kept_text}`")
        if dropped is not None:
            dropped_text = ", ".join(map(str, dropped))
            mod_details.append(f"Dropped: `{dropped_text}`")

        mod_text = "\n".join(mod_details) if mod_details else "No modifiers applied"

        embed = discord.Embed(title="🎲 Dice Roll", color=0x3498db)
        embed.add_field(name="🎯 Rolls", value=f"`{rolls_text}`", inline=True)
        embed.add_field(name="✨ Modifiers", value=f"`{mod_text}`", inline=True)
        embed.add_field(name="🏆 Final Result", value=f"`{final_text}`", inline=False)
        embed.set_footer(text=f"Dice rolled: {dice}")

        # Truncate field values if they exceed Discord's per-field limit
        

        # Check if any field is too long or total embed is too large
        fields_too_long = any(len(field.value) > 1024 for field in embed.fields)
        total_embed_length = (
            len(embed.title or "") +
            len(embed.description or "") +
            sum(len(field.name or "") + len(field.value or "") for field in embed.fields) +
            len(embed.footer.text or "") if embed.footer else 0
        )
        too_long = fields_too_long or total_embed_length > 6000
        
        if too_long:
            # Output to file instead, but show error for final result
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
            "Cannot predict now", "Concentrate and ask again",

            # Sarcastic
            "Sure, and pigs might fly too", "Only if you believe hard enough",
            "Yeah, no.", "Not even in the multiverse", "When hell freezes over",
            "Ask your mom", "Absolutely. In your dreams.", "I plead the fifth",
            "If I had a coin, I'd flip it", "Define 'possible'...",

            # Meme-y
            "You already know the answer", "That’s a skill issue", "Cringe question tbh",
            "The Council has denied your request", "It is what it is", "Try Alt+F4",
            "Your chances are as good as Genshin gacha rates", "lmao no",
            "This message will self-destruct", "Roll a D20 and get back to me",

            # Cryptic & cursed
            "The void whispers yes", "The answer lies beneath your bed",
            "You've already made your choice", "Don’t open the door tonight",
            "There’s something behind you", "Only the cursed know for sure",
            "It was never meant to be asked", "The prophecy says nothing of this",

            # Chaotic neutral
            "Yes but also no", "No but also yes", "42", "Meh",
            "I flipped a coin but lost it", "You get what you get", 
            "You don’t want to know", "Absolutely. Wait, what was the question?",
            "¯\_(ツ)_/¯", "Hold on, I'm updating my firmware",

            # Straight up lying
            "Yes, trust me bro", "No, but say yes anyway", 
            "Definitely. Just ignore the consequences", 
            "It’s fine. Probably.", "For legal reasons, I must say yes"
        ]

        answer = random.choice(responses)
        embed = discord.Embed(title="🎱 Magic 8-Ball", color=0x3498db)
        embed.add_field(name="Question", value=f"`{question}`", inline=False)
        embed.add_field(name="Answer", value=f"`{answer}`", inline=False)
        await interaction.followup.send(embed=embed, ephemeral=False)

    @app_commands.command(name="hack", description="Hack another user! Totally 100% legit.")
    @cooldown(20)
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
    
    @app_commands.command(name="info", description="Get information about the bot.")
    async def info_of_bot(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False)
        bot = self.bot.user
        embed = discord.Embed(title=f"Bot Info: {bot.name}", color=0x3498db)
        embed.add_field(name="Bot ID", value=bot.id, inline=False)
        embed.add_field(name="Created By", value=f"Iza Carlos (_izacarlos)", inline=True)
        embed.add_field(name="Created At", value=bot.created_at.strftime("%Y-%m-%d %H:%M:%S"), inline=False)
        embed.add_field(name="Commands", value=len(self.bot.tree.get_commands()), inline=True) # shows the correct number of slash commands
        embed.set_thumbnail(url=bot.avatar.url if bot.avatar else None)

        await interaction.followup.send(embed=embed, ephemeral=False)
    # stupid dum dum discord reserves bot_ for their own shit, i'm angry
    
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
            embed.set_thumbnail(url=discord.Embed.Empty)

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
    @cooldown(5)
    async def letter(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False)
        letter = random.choice("abcdefghijklmnopqrstuvwxyz")
        embed = discord.Embed(title="🔤 Random Letter", color=0x3498db)
        embed.add_field(name="Generated Letter", value=f"`{letter}`", inline=False)
        await interaction.followup.send(embed=embed, ephemeral=False) # What are we letter-gatekeeping now?

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
    

async def setup(bot):
    await bot.add_cog(Fun(bot))
