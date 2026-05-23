# Standard Library Imports
import asyncio
import random
import time


# Third-Party Imports
import discord
from discord import Interaction, app_commands
from discord.ext import commands


# Local Imports
from database import (
    get_balance,
    update_balance,
    add_user,
    get_user_items,
    get_robbery_modifier,
    get_victim_rob_modifier,
    consume_robber_item_uses,
    check_gun_defense,
    decrement_gun_use,
    check_taser_defense,
    decrement_taser_use,
    remove_item_from_user,
    update_item_uses,
    add_item_to_user,
    atomic_deduct,
    get_last_claim,
    set_last_claim,
    has_active_effect,
    get_effect_remaining_time,
    add_user_effect,
)
from database.items import get_item_by_id
from config import cooldown, check_cooldown, update_cooldown
from logging_modules.custom_logger import get_logger

log = get_logger()
from discord import ui

# ===================== Constants =====================
DAILY_COOLDOWN    = 43_200      # 12 hours — the minimum gap between claims
MONTHLY_COOLDOWN  = 2_548_800   # ~29.5 days (30 days − 12 h margin of error)
DAILY_STREAK_RESET = 129_600    # 36 h gap resets daily streak

class PlayAgainView(ui.View):
    def __init__(self, callback, user_id, *args, **kwargs):
        super().__init__(timeout=30) # adjust at your own risk people WILL spam this
        self.callback = callback
        self.user_id = user_id
        self.args = args
        self.kwargs = kwargs

    @ui.button(label="🔄 Do Again", style=discord.ButtonStyle.primary)
    async def play_again(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("🚫 This isn't your command!", ephemeral=True)
        
        # Cooldown mapping for economy commands
        cooldowns = {
            'run_crime': 8,
            'run_slut': 10,
            'run_work': 7
        }
        
        cmd_name = self.callback.__name__
        cl_duration = cooldowns.get(cmd_name, 5) # default 5s
        
        is_on_cooldown, retry_after = check_cooldown(self.user_id, cmd_name, cl_duration)
        if is_on_cooldown:
            return await interaction.response.send_message(
                f"🕒 You're working too fast! Try again in {round(retry_after, 1)}s.",
                ephemeral=True
            )

        # Disable button and stop the view to prevent further clicks
        button.disabled = True
        self.stop()
        
        # Update the message to show the button is disabled (don't respond yet!)
        try:
            await interaction.message.edit(view=self)
        except:
            pass  # Message might be deleted or inaccessible
        
        # Update cooldown timestamp before running
        update_cooldown(self.user_id, cmd_name)
        
        # Run the callback with the new interaction (callback will respond)
        await self.callback(interaction, *self.args, **self.kwargs)
    
    async def on_timeout(self):
        """Disable the button when the view times out."""
        # Disable all buttons
        for item in self.children:
            if isinstance(item, ui.Button):
                item.disabled = True
        
        # Try to edit the message to show disabled button
        # Note: We need to store the message reference when creating the view
        try:
            if hasattr(self, 'message') and self.message:
                await self.message.edit(view=self)
        except:
            pass  # Message might be deleted or we don't have permission


class EconomyCommands(app_commands.Group):
    def __init__(self):
        super().__init__(name="economy", description="Economy related commands")

    async def run_rob(self, interaction: discord.Interaction, target: discord.Member):
        await interaction.response.defer(ephemeral=False)
        user_id = interaction.user.id
        target_id = target.id
        log.trace(f"User {user_id} attempting to rob {target_id}")

        # ── Guards ─────────────────────────────────────────────────────
        if user_id == target_id:
            return await interaction.followup.send("❌ You can't rob yourself!", ephemeral=True)

        # Check active "no_rob" effect (blinded/prevented from robbing)
        if await has_active_effect(user_id, "no_rob"):
            remaining = await get_effect_remaining_time(user_id, "no_rob")
            return await interaction.followup.send(
                f"❌ You are still recovering from sand in your eyes! You cannot rob anyone for another **{remaining}**.",
                ephemeral=True
            )

        await add_user(user_id, interaction.user.name)
        await add_user(target_id, target.name)

        if await get_balance(user_id) < -50:
            return await interaction.followup.send("💸 You can't afford risking another crime!")

        target_balance = await get_balance(target_id)
        if target_balance < 100:
            return await interaction.followup.send(
                f"💸 {target.mention} doesn't have enough coins to rob!", ephemeral=True
            )

        # ── Check Pocket Sand / Parking Cone defenses ──────────────────────
        victim_items = await get_user_items(target_id)
        
        pocket_sand = next((i for i in victim_items if str(i["item_id"]) == "20" and i["uses_left"] > 0), None)
        if pocket_sand:
            new_uses = pocket_sand["uses_left"] - 1
            if new_uses <= 0:
                await remove_item_from_user(target_id, 20)
            else:
                await update_item_uses(target_id, 20, new_uses)
            await add_user_effect(user_id, "no_rob", 10800) # 3 hours
            log.warningtrace(f"{user_id} was pocket-sanded by {target_id}")
            return await interaction.followup.send(
                f"🏜️ **Pocket sand!** You tried to sneak up on {target.mention}, but they spun around and threw a fistful of sand directly into your eyes!\n"
                f"The robbery failed and you are blinded! You cannot rob anyone for the next 3 hours."
            )

        parking_cone = next((i for i in victim_items if str(i["item_id"]) == "24" and i["uses_left"] > 0), None)
        if parking_cone:
            new_uses = parking_cone["uses_left"] - 1
            if new_uses <= 0:
                await remove_item_from_user(target_id, 24)
            else:
                await update_item_uses(target_id, 24, new_uses)
            await add_user_effect(user_id, "cone_penalty", 3600) # 1 hour
            log.warningtrace(f"{user_id} was parking-coned by {target_id}")
            return await interaction.followup.send(
                f"🚧 **Parking Cone'd!** You attempted to rob {target.mention}, but they swiftly slammed a bright orange traffic cone over your head!\n"
                f"The robbery failed and you stumbled away in embarrassment. Your robbery success chance is reduced by 30% for the next hour."
            )

        # ── Victim defenses (checked in power order: gun › taser) ────────────
        if await check_gun_defense(target_id):
            await decrement_gun_use(target_id)
            fine = random.randint(100, 350)
            await update_balance(user_id, -fine)
            log.warningtrace(f"{user_id} was shot by {target_id}'s gun, fined {fine}")
            return await interaction.followup.send(
                f"🔫 {target.mention} drew their gun and fired! "
                f"You scrambled away and dropped 💰 `{fine}` coins."
            )

        if await check_taser_defense(target_id):
            await decrement_taser_use(target_id)
            fine = random.randint(30, 120)
            await update_balance(user_id, -fine)
            log.warningtrace(f"{user_id} was tased by {target_id}'s taser, fined {fine}")
            return await interaction.followup.send(
                f"⚡ {target.mention} tased you before you could act! "
                f"You stumbled away and dropped 💰 `{fine}` coins."
            )

        # ── Compute effective success chance ─────────────────────────────
        robber_mod = await get_robbery_modifier(user_id)    # e.g. +0.50 from Bolt Cutters
        victim_mod  = await get_victim_rob_modifier(target_id)  # e.g. +0.50 from Padlocked Wallet (decrements it)
        success_chance = max(0.05, min(0.95, 0.40 + robber_mod - victim_mod))
        success = random.random() < success_chance

        # Consume robber's passive item uses (Bolt Cutters, Hackatron) for this attempt
        await consume_robber_item_uses(user_id)

        # ── Resolve ───────────────────────────────────────────────────
        if success:
            amount = random.randint(50, min(300, target_balance))
            await update_balance(user_id, amount)
            await update_balance(target_id, -amount)
            log.successtrace(f"{user_id} robbed {target_id} for {amount} coins")
            messages = [
                f"🦹 You successfully robbed {target.mention} and stole 💰 `{amount}` coins!",
                f"💰 You snuck up on {target.mention} and got away with `{amount}` coins!",
                f"🔪 You threatened {target.mention} and took `{amount}` coins!",
                f"💵 You pickpocketed {target.mention} and made off with `{amount}` coins!",
            ]
        else:
            penalty = random.randint(50, 400)
            await update_balance(user_id, -penalty)
            log.warningtrace(f"{user_id} failed to rob {target_id} and lost {penalty} coins")
            messages = [
                f"🚨 You got caught trying to rob {target.mention}! You paid a fine of 💰 `{penalty}` coins.",
                f"👮 The police stopped your robbery attempt. Lost 💰 `{penalty}` coins.",
                f"😬 {target.mention} fought back! You lost 💰 `{penalty}` coins.",
                f"🚣 {target.mention} made you trip and the police caught you! You lost 💰 `{penalty}` coins."
            ]

        await interaction.followup.send(random.choice(messages), ephemeral=False)

    @app_commands.command(name="rob", description="Rob someone for cash. Risky!")
    @cooldown(cl=600, tm=25.0, ft=3)
    async def rob(self, interaction: discord.Interaction, target: discord.Member):
        await self.run_rob(interaction, target)

    async def run_crime(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False)
        user_id = interaction.user.id
        await add_user(user_id, interaction.user.name)
        log.trace(f"User {user_id} attempting crime")

        commiter_bal = await get_balance(user_id)
        if commiter_bal < -100:
            return await interaction.followup.send("💸 You can't afford risking another crime!")

        # Milk suds penalty: 20% higher failure rate
        fail_chance = 0.16
        if await has_active_effect(user_id, "milk_suds"):
            fail_chance += 0.20

        success = random.random() > fail_chance  
        amount = random.randint(100, 600) if success else -random.randint(300, 600)

        await update_balance(user_id, amount)
        if success:
            log.successtrace(f"User {user_id} committed crime successfully: {amount} coins")
        else:
            log.warningtrace(f"User {user_id} failed crime: {amount} coins")

        # Roll for crime drops: Sea BassHead (14), Fake Gold Bar (17), Fidget Spinner (16), Lint (15), Expired fish (26), USB Stick (21)
        item_found = None
        if success and random.random() < 0.08:
            pool = [14, 17, 16, 15, 26, 21]
            weights = [10, 20, 20, 20, 15, 15]
            item_id = random.choices(pool, weights=weights)[0]
            item_found = get_item_by_id(item_id)
            if item_found:
                await add_item_to_user(user_id, item_found["id"], item_found["name"], uses_left=item_found["uses_left"])

        if success:
            messages = [
                f"🕵️‍♂️ You successfully pickpocketed an old man and got 💰 `{amount}` coins.",
                f"🔫 You robbed a small convenience store and walked away with 💰 `{amount}` coins.",
                f"💻 You hacked into a bank's system and stole 💰 `{amount}` coins. Nice job!",
                f"💰 You successfully scammed someone and made 💰 `{amount}` coins.",
                f"💵 You sold fake tickets and made 💰 `{amount}` coins."
            ]
        else:
            messages = [
                f"🚓 You got caught stealing a candy bar and had to pay a fine of 💰 `{abs(amount)}` coins.",
                f"🛑 You tried scamming someone but got scammed instead! Lost 💰 `{abs(amount)}` coins.",
                f"🚔 The cops caught you red-handed. You paid a fine of 💰 `{abs(amount)}` coins.",
                f"💸 You got caught trying to rob a bank! Lost 💰 `{abs(amount)}` coins.",
                f"👮 You got arrested for public indecency! Lost 💰 `{abs(amount)}` coins."
            ]

        msg_text = random.choice(messages)
        if success and item_found:
            msg_text += f"\n🔍 **Look what you found!** You also picked up a **{item_found['name']}**!"

        view = PlayAgainView(self.run_crime, user_id)
        msg = await interaction.followup.send(msg_text, ephemeral=False, view=view)
        view.message = msg

    @app_commands.command(name="crime", description="Commit a crime for cash. Risky!")
    @cooldown(cl=8, tm=25.0, ft=3)
    async def crime(self, interaction: discord.Interaction):
        await self.run_crime(interaction)

    async def run_slut(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False)
        user_id = interaction.user.id
        await add_user(user_id, interaction.user.name)
        log.trace(f"User {user_id} attempting slut command")

        # Milk suds penalty: 20% higher failure rate
        fail_chance = 0.07
        if await has_active_effect(user_id, "milk_suds"):
            fail_chance += 0.20

        success = random.random() > fail_chance
        amount = random.randint(50, 300) if success else -random.randint(100, 200)

        await update_balance(user_id, amount)

        # Roll for slut drops: Rubbers (12), Mug (13), Melted Ice Cream (18), Lint (15), Milk suds (27)
        item_found = None
        if success and random.random() < 0.08:
            pool = [12, 13, 18, 15, 27]
            weights = [50, 10, 20, 10, 10]
            item_id = random.choices(pool, weights=weights)[0]
            item_found = get_item_by_id(item_id)
            if item_found:
                await add_item_to_user(user_id, item_found["id"], item_found["name"], uses_left=item_found["uses_left"])

        if success:
            messages = [
                f"💋 You found a rich sugar daddy/mommy and earned 💰 `{amount}` coins.",
                f"👠 A night well spent. You made 💰 `{amount}` coins.",
                f"🎭 You took a questionable modeling gig and got paid 💰 `{amount}` coins.",
                f"☢️ Someone sent a link in the group chat. You made 💰 `{amount}` coins"
            ]
        else:
            messages = [
                f"👎 Nobody was interested in your services. You lost 💰 `{abs(amount)}` coins.",
                f"🚔 The cops fined you for public indecency. Lost 💰 `{abs(amount)}` coins.",
                f"🤮 You got sick and had to spend 💰 `{abs(amount)}` coins on meds.",
                f"🤓 You were too ugly and had to spend 💰 `{abs(amount)}` coins on plastic surgery."
            ]

        msg_text = random.choice(messages)
        if success and item_found:
            msg_text += f"\n🔍 **Look what you found!** You also picked up a **{item_found['name']}**!"

        view = PlayAgainView(self.run_slut, user_id)
        msg = await interaction.followup.send(msg_text, ephemeral=False, view=view)
        view.message = msg

    @app_commands.command(name="slut", description="Do some... work for quick cash.")
    @cooldown(cl=10, tm=25.0, ft=3) # Horny bastards.
    async def slut(self, interaction: discord.Interaction):
        await self.run_slut(interaction)

    async def run_work(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False)
        user_id = interaction.user.id
        await add_user(user_id, interaction.user.name)
        log.trace(f"User {user_id} attempting work command")

        # Milk suds penalty: 20% higher failure rate
        fail_chance = 0.03
        if await has_active_effect(user_id, "milk_suds"):
            fail_chance += 0.20

        success = random.random() > fail_chance
        amount = random.randint(20, 250) if success else -random.randint(400, 800)
        await update_balance(user_id, amount)

        # Roll for work drops: Coffee Mug (Full) (13), Screwdriver (19), Fidget Spinner (16), Lint (15), Chicken (23)
        item_found = None
        if success and random.random() < 0.08:
            pool = [13, 19, 16, 15, 23]
            weights = [10, 20, 30, 30, 10]
            item_id = random.choices(pool, weights=weights)[0]
            item_found = get_item_by_id(item_id)
            if item_found:
                await add_item_to_user(user_id, item_found["id"], item_found["name"], uses_left=item_found["uses_left"])

        if success:
            messages = [
            f"👨‍💻 You worked as a programmer and got paid 💰 `{amount}` coins.",
            f"🚚 You delivered packages and earned 💰 `{amount}` coins.",
            f"🍔 You worked at a fast-food joint and made 💰 `{amount}` coins.",
            f"🏢 You worked in an office and got paid 💰 `{amount}` coins.",
            f"🛠️ You did some handyman work and earned 💰 `{amount}` coins."
            ]
        else:
            messages = [
            f"👎Your boss found you smoking! You lost 💰`{abs(amount)}` coins",
            f"👥A coworker found you had 2 jobs! You lost 💰`{abs(amount)}` coins",
            f"💸You got caught stealing from the till! You lost 💰`{abs(amount)}` coins",
            f"🚔You got caught slacking off! You lost 💰`{abs(amount)}` coins",
            f"👮You got caught doing something illegal at work! You lost 💰`{abs(amount)}` coins"
            ]

        msg_text = random.choice(messages)
        if success and item_found:
            msg_text += f"\n🔍 **Look what you found!** You also picked up a **{item_found['name']}**!"

        view = PlayAgainView(self.run_work, user_id)
        msg = await interaction.followup.send(msg_text, ephemeral=False, view=view)
        view.message = msg

    @app_commands.command(name="work", description="Do a normal job for guaranteed(ish) cash.")
    @cooldown(cl=7, tm=25.0, ft=3)
    async def work(self, interaction: discord.Interaction):
        await self.run_work(interaction)

    @app_commands.command(name="balance", description="Check your current balance")
    @cooldown(cl=2, tm=25.0, ft=3)
    async def balance(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False)
        user_id = interaction.user.id
        balance = await get_balance(user_id)
        await interaction.followup.send(f"💰 Your balance: **{balance}** coins")

    @app_commands.command(name="inventory", description="Check your inventory")
    @cooldown(cl=4, tm=25.0, ft=3)
    async def inventory(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False)
        user_id = interaction.user.id
        await add_user(user_id, interaction.user.name)

        items = await get_user_items(user_id)
        if items:
            inventory_message = "\n".join(
                # Format each inventory item with its name, ID, and remaining uses
                [f"🔹 {item['item_name']} (ID: {item['item_id']}) - Uses left: {item['uses_left']}" for item in items]
            )
            await interaction.followup.send(f"📦 Your inventory:\n{inventory_message}", ephemeral=True)

        else:
            await interaction.followup.send("📦 You have no items in your inventory!", ephemeral=True)
    
    @app_commands.command(name="transfer", description="Give money to another user")
    @cooldown(cl=8, tm=25.0, ft=3)
    async def transfer(self, interaction: discord.Interaction, target: discord.Member, amount: int):
        await interaction.response.defer(ephemeral=False)
        user_id = interaction.user.id
        target_id = target.id

        if user_id == target_id:
            await interaction.followup.send("❌ You can't transfer money to yourself!", ephemeral=True)
            return

        await add_user(user_id, interaction.user.name)
        await add_user(target_id, target.name)

        userbalance = await get_balance(user_id)
        if userbalance <= 0:
            return await interaction.followup.send("💸 You can't transfer a negative balance!")

        if amount <= 0:
            await interaction.followup.send("❌ Invalid amount!", ephemeral=True)
            return

        success = await atomic_deduct(user_id, amount)
        if not success:
            await interaction.followup.send("❌ You don't have enough coins!", ephemeral=True)
            return

        await update_balance(target_id, amount)
        log.successtrace(f"User {user_id} transferred {amount} coins to {target_id}")

        await interaction.followup.send(f"💸 You transferred {target.mention} 💰 `{amount}` coins!", ephemeral=False)

    @app_commands.command(name="give", description="Give an item (or items) to another user")
    @cooldown(cl=10, tm=25.0, ft=3)
    async def give(self, interaction: discord.Interaction, target: discord.Member, item_id: int, amount: int):
        await interaction.response.defer(ephemeral=False)
        user_id = interaction.user.id
        target_id = target.id

        if user_id == target_id:
            return await interaction.followup.send("❌ You can't give items to yourself!", ephemeral=True)

        await add_user(user_id, interaction.user.name)
        await add_user(target_id, target.name)

        if amount <= 0:
            return await interaction.followup.send("❌ Invalid amount!", ephemeral=True)

        items = await get_user_items(user_id)
        item = next((i for i in items if str(i['item_id']) == str(item_id)), None)

        if not item or item['uses_left'] < amount:
            return await interaction.followup.send("❌ You don't have enough of that item!", ephemeral=True)

        new_uses = item['uses_left'] - amount
        if new_uses == 0:
            await remove_item_from_user(user_id, item_id)
        else:
            await update_item_uses(user_id, item_id, new_uses)

        await add_item_to_user(target_id, item_id, item['item_name'], uses_left=amount)
        log.successtrace(f"User {user_id} gave {amount}x '{item['item_name']}' (ID {item_id}) to {target_id}")

        await interaction.followup.send(
            f"🎁 You gave {target.mention} **{amount}x {item['item_name']}**!", ephemeral=False
        )

    # ===================== Daily / Monthly =====================
    async def run_daily(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False)
        user_id = interaction.user.id
        await add_user(user_id, interaction.user.name)

        now = int(time.time())
        last_claim, streak = await get_last_claim(user_id, "daily")
        elapsed = now - last_claim

        # Cooldown check
        if last_claim > 0 and elapsed < DAILY_COOLDOWN:
            remaining = DAILY_COOLDOWN - elapsed
            hours   = int(remaining // 3600)
            minutes = int((remaining % 3600) // 60)
            return await interaction.followup.send(
                f"⏳ You already claimed your daily! Come back in **{hours}h {minutes}m**.",
                ephemeral=True
            )

        # Streak logic: gap > 36 h resets the chain
        if last_claim > 0 and elapsed > DAILY_STREAK_RESET:
            streak = 0
        streak += 1

        # Reward & multiplier
        base_reward = random.randint(300, 700)
        if   streak >= 30: multiplier = 5.0
        elif streak >= 14: multiplier = 3.0
        elif streak >= 7:  multiplier = 2.0
        elif streak >= 3:  multiplier = 1.5
        else:              multiplier = 1.0
        reward = int(base_reward * multiplier)

        await update_balance(user_id, reward)
        await set_last_claim(user_id, "daily", now, streak)
        log.successtrace(f"Daily claimed by {user_id}: {reward} coins (streak {streak}, x{multiplier})")

        # Build embed
        embed = discord.Embed(
            title="📅 Daily Reward!",
            color=discord.Color.gold()
        )
        embed.add_field(name="Coins Earned",  value=f"💰 **{reward:,}**", inline=True)
        embed.add_field(name="Current Streak", value=f"🔥 **{streak}** day(s)", inline=True)
        if multiplier > 1.0:
            embed.add_field(name="Streak Bonus", value=f"✨ **{multiplier}x**", inline=True)

        milestones = [3, 7, 14, 30]
        next_ms = next((m for m in milestones if m > streak), None)
        if next_ms:
            embed.set_footer(text=f"📊 {next_ms - streak} more day(s) until {next_ms}-day streak bonus!")
        else:
            embed.set_footer(text="👑 Max streak bonus reached. Legendary grind.")

        await interaction.followup.send(embed=embed)

    @app_commands.command(name="daily", description="Claim your daily coins. Streak bonuses await!")
    @cooldown(cl=5, tm=25.0, ft=3)
    async def daily(self, interaction: discord.Interaction):
        await self.run_daily(interaction)

    async def run_monthly(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False)
        user_id = interaction.user.id
        await add_user(user_id, interaction.user.name)

        now = int(time.time())
        last_claim, _ = await get_last_claim(user_id, "monthly")
        elapsed = now - last_claim

        if last_claim > 0 and elapsed < MONTHLY_COOLDOWN:
            remaining = MONTHLY_COOLDOWN - elapsed
            days  = int(remaining // 86_400)
            hours = int((remaining % 86_400) // 3600)
            return await interaction.followup.send(
                f"⏳ Already claimed your monthly! Come back in **{days}d {hours}h**.",
                ephemeral=True
            )

        reward = random.randint(8_000, 18_000)
        await update_balance(user_id, reward)
        await set_last_claim(user_id, "monthly", now, 0)
        log.successtrace(f"Monthly claimed by {user_id}: {reward} coins")

        embed = discord.Embed(
            title="🗓️ Monthly Reward!",
            description="A fat stack of coins, on the house.",
            color=discord.Color.purple()
        )
        embed.add_field(name="Coins Earned", value=f"💰 **{reward:,}**", inline=False)
        embed.set_footer(text="See you next month!")
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="monthly", description="Claim your monthly coins haul.")
    @cooldown(cl=5, tm=25.0, ft=3)
    async def monthly(self, interaction: discord.Interaction):
        await self.run_monthly(interaction)



class EconomyCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
    
    async def cog_load(self):
        self.bot.tree.add_command(EconomyCommands())

async def setup(bot):
    await bot.add_cog(EconomyCog(bot))
