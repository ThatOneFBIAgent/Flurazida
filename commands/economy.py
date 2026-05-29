# Standard Library Imports
import asyncio
import random
import time
import math


# Third-Party Imports
import discord
from discord import Interaction, app_commands
from discord.ext import commands


# Local Imports
from discord.ext import tasks
from database.manager import db
from database import (
    get_top_global_wealth,
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
    get_bank,
    get_bank_max,
    deposit_bank,
    withdraw_bank,
    decrease_bank,
)
from database.items import get_item_by_id
from config import cooldown, check_cooldown, update_cooldown
from logging_modules.custom_logger import get_logger

log = get_logger()
from discord import ui

# Constants 
DAILY_COOLDOWN    = 43_200      # 12 hours — the minimum gap between claims
DAILY_STREAK_RESET = 129_600    # 36 h gap resets daily streak
MONTHLY_COOLDOWN  = 2_548_800   # ~29.5 days (30 days − 12 h margin of error)
WEEKLY_COOLDOWN   = 561_600      # 6.5 days (7 days - 12 h margin of error)

RANK_PREFIXES = {
    1: "🥇",
    2: "🥈",
    3: "🥉",
    4: "4️⃣",
    5: "5️⃣",
}

# run_* callback -> (slash @cooldown command name, seconds)
PLAY_AGAIN_COOLDOWNS = {
    "run_crime": ("crime", 8),
    "run_slut": ("slut", 10),
    "run_work": ("work", 7),
}


def _play_again_timeout(cooldown_seconds: float) -> float:
    return max(30.0, float(cooldown_seconds) + 25.0)


# Classes (Views, Paginators, etc.)

class PlayAgainView(ui.View):
    def __init__(self, callback, user_id, *args, **kwargs):
        self.callback = callback
        self.user_id = user_id
        self.args = args
        self.kwargs = kwargs
        self.cooldown_key, self.cooldown_seconds = PLAY_AGAIN_COOLDOWNS.get(
            callback.__name__, (callback.__name__, 5)
        )
        super().__init__(timeout=_play_again_timeout(self.cooldown_seconds))

    @ui.button(label="🔄 Do Again", style=discord.ButtonStyle.primary)
    async def play_again(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("🚫 This isn't your command!", ephemeral=True)

        is_on_cooldown, retry_after = check_cooldown(
            self.user_id, self.cooldown_key, self.cooldown_seconds
        )
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
        
        # Update cooldown timestamp before running (same key as @cooldown on slash commands)
        update_cooldown(self.user_id, self.cooldown_key)

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

_heist_victim_cooldowns = {}
_rob_victim_cooldowns = {}

class HeistView(ui.View):
    def __init__(self, leader_id, target_id, target_name, target_bank_bal, timeout=30):
        super().__init__(timeout=timeout)
        self.leader_id = leader_id
        self.target_id = target_id
        self.target_name = target_name
        self.target_bank_bal = target_bank_bal
        self.participants = [leader_id]
        self.defended = False
        self.defense_item_used = None
        self.message = None

    @ui.button(label="🦹 Join Heist", style=discord.ButtonStyle.danger)
    async def join_heist(self, interaction: discord.Interaction, button: ui.Button):
        user_id = interaction.user.id
        if user_id == self.target_id:
            return await interaction.response.send_message("❌ You can't join a heist against your own bank! Click 'Defend Bank' instead.", ephemeral=True)
        if user_id in self.participants:
            return await interaction.response.send_message("❌ You are already part of this heist crew!", ephemeral=True)
        # Maximum crew size to prevent oversized heists
        if len(self.participants) >= 6:
            return await interaction.response.send_message("❌ Heist crew is full (max 6 members)!", ephemeral=True)
            
        # Ensure participant has enough wallet balance to pay fine if it fails
        wallet = await get_balance(user_id)
        if wallet < 100:
            return await interaction.response.send_message("❌ You need at least 💰 `100` coins in your wallet to risk joining a heist!", ephemeral=True)
            
        self.participants.append(user_id)
        await interaction.response.send_message("🦹 You joined the heist crew! Get ready...", ephemeral=True)
        
        # Update the heist embed
        embed = self.message.embeds[0]
        crew_mentions = ", ".join(f"<@{pid}>" for pid in self.participants)
        embed.set_field_at(2, name="Crew Members", value=crew_mentions, inline=False)
        try:
            await self.message.edit(embed=embed, view=self)
        except:
            pass

    @ui.button(label="🔫 Defend Bank", style=discord.ButtonStyle.success)
    async def defend_bank(self, interaction: discord.Interaction, button: ui.Button):
        user_id = interaction.user.id
        if user_id != self.target_id:
            return await interaction.response.send_message("❌ This is not your bank to defend!", ephemeral=True)
            
        # Check active defense items
        items = await get_user_items(user_id)
        
        # Priority: Gun (10) > Taser (5) > Pocket Sand (20) > Parking Cone (24)
        def_item = None
        for item_id in [10, 5, 20, 24]:
            found = next((i for i in items if str(i["item_id"]) == str(item_id) and i["uses_left"] > 0), None)
            if found:
                def_item = found
                break
                
        if not def_item:
            return await interaction.response.send_message(
                "❌ You don't have any active defense items (Loaded Gun, Taser, Pocket sand, Parking cone) in your inventory!",
                ephemeral=True
            )
            
        self.defended = True
        self.defense_item_used = int(def_item["item_id"])
        self.stop() # Stop the 30s timer
        
        # Decrement item uses
        item_id = int(def_item["item_id"])
        new_uses = def_item["uses_left"] - 1
        if new_uses <= 0:
            await remove_item_from_user(user_id, item_id)
        else:
            await update_item_uses(user_id, item_id, new_uses)
            
        # Disable all buttons
        for child in self.children:
            child.disabled = True
            
        # Create defense result embed
        embed = discord.Embed(
            title="🛡️ Heist Foiled!",
            color=discord.Color.green()
        )
        
        penalty_msgs = []
        if item_id == 10: # Loaded Gun
            embed.title = "🔫 Heist Foiled by Armed Citizen!"
            embed.description = f"<@{self.target_id}> drew their **Loaded Gun** and fired warnings! The heist crew scattered in panic!"
            # Fine participants
            for pid in self.participants:
                fine = random.randint(200, 500)
                await update_balance(pid, -fine)
                penalty_msgs.append(f"• <@{pid}> was shot and paid 💰 `{fine}` coins in medical bills.")
        elif item_id == 5: # Taser
            embed.title = "⚡ Heist Foiled by Taser!"
            embed.description = f"<@{self.target_id}> stepped in with a **Taser** and shocked <@{self.leader_id}>! The rest of the crew fled!"
            for pid in self.participants:
                fine = random.randint(100, 300)
                await update_balance(pid, -fine)
                penalty_msgs.append(f"• <@{pid}> was tased/shocked and lost 💰 `{fine}` coins.")
        elif item_id == 20: # Pocket Sand
            embed.title = "🏜️ Heist Foiled by Pocket Sand!"
            embed.description = f"<@{self.target_id}> threw **Pocket sand** into <@{self.leader_id}>'s eyes! The robbery was called off!"
            await add_user_effect(self.leader_id, "no_rob", 10800) # 3 hours
            for pid in self.participants:
                fine = random.randint(50, 200)
                await update_balance(pid, -fine)
                penalty_msgs.append(f"• <@{pid}> dropped 💰 `{fine}` coins.")
        elif item_id == 24: # Parking Cone
            embed.title = "🚧 Heist Foiled by Parking Cone!"
            embed.description = f"<@{self.target_id}> slammed a **Parking Cone** over <@{self.leader_id}>'s head! Total embarrassment!"
            await add_user_effect(self.leader_id, "cone_penalty", 3600) # 1 hour
            for pid in self.participants:
                fine = random.randint(50, 200)
                await update_balance(pid, -fine)
                penalty_msgs.append(f"• <@{pid}> tripped over a cone and lost 💰 `{fine}` coins.")
                
        if penalty_msgs:
            embed.add_field(name="Casualties & Fines", value="\n".join(penalty_msgs), inline=False)
            
        await interaction.response.edit_message(embed=embed, view=self)

class LeaderboardPaginator(discord.ui.View):
    def __init__(self, data, title, is_server, interaction):
        super().__init__(timeout=120)
        self.data = data
        self.title = title
        self.is_server = is_server
        self.interaction = interaction
        self.current_page = 0
        self.items_per_page = 10
        self.max_pages = math.ceil(len(self.data) / self.items_per_page)
        if self.max_pages == 0:
            self.max_pages = 1
        self.update_buttons()

    def update_buttons(self):
        self.prev_btn.disabled = self.current_page == 0
        self.next_btn.disabled = self.current_page >= self.max_pages - 1

    def generate_embed(self):
        embed = discord.Embed(title=self.title, color=discord.Color.gold())
        start_idx = self.current_page * self.items_per_page
        page_data = self.data[start_idx:start_idx + self.items_per_page]

        if not page_data:
            embed.description = "No one has any money!"
            return embed

        desc = ""
        for i, (uid, wealth) in enumerate(page_data, start_idx + 1):
            name = f"User {uid}"
            if self.is_server:
                member = self.interaction.guild.get_member(uid)
                if member:
                    name = member.display_name
            else:
                user = self.interaction.client.get_user(uid)
                if user:
                    name = user.name
            prefix = RANK_PREFIXES.get(i, f"**{i}.**")
            desc += f"{prefix} {name} — 💰 **{wealth:,}**\n"

        embed.description = desc
        embed.set_footer(text=f"Page {self.current_page + 1}/{self.max_pages}")
        return embed

    @discord.ui.button(label="◀️", style=discord.ButtonStyle.secondary)
    async def prev_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_page > 0:
            self.current_page -= 1
            self.update_buttons()
            await interaction.response.edit_message(embed=self.generate_embed(), view=self)

    @discord.ui.button(label="▶️", style=discord.ButtonStyle.secondary)
    async def next_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_page < self.max_pages - 1:
            self.current_page += 1
            self.update_buttons()
            await interaction.response.edit_message(embed=self.generate_embed(), view=self)

# Helpers

def wallet_emoji(balance: int) -> str:
    """Returns a contextual emoji based on wallet balance."""
    if balance < 0:      return "💸"   # in debt
    if balance == 0:     return "🪙"   # dead broke
    if balance < 500:    return "💵"   # a little something
    if balance < 5_000:  return "💰"   # comfortable
    if balance < 50_000: return "🤑"   # doing well
    if balance < 500_000: return "💎" # stacked
    return "👑"                        # obscene wealth

def bank_emoji(bank: int, bank_max: int) -> str:
    """Returns a contextual emoji based on bank fill percentage."""
    if bank_max <= 0:
        return "🏦"
    pct = bank / bank_max
    if pct == 0:         return "🏚️"   # empty vault
    if pct < 0.25:       return "🏦"   # mostly empty
    if pct < 0.50:       return "🏛️"   # quarter full
    if pct < 0.80:       return "💼"   # half full
    if pct < 1.0:        return "🔐"   # nearly full
    return "🏴‍☠️"                       # totally maxed

# Commands

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

        # Check rob victim cooldown (5 minutes to prevent harassment)
        now = time.time()
        if target_id in _rob_victim_cooldowns:
            elapsed = now - _rob_victim_cooldowns[target_id]
            if elapsed < 300: # 5 minutes
                remaining = int(300 - elapsed)
                return await interaction.followup.send(
                    f"❌ {target.mention} was recently targeted in a robbery! You must wait **{remaining}s** before robbing them again.",
                    ephemeral=True
                )

        _rob_victim_cooldowns[target_id] = now

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
                f"💵 You sold fake tickets and made 💰 `{amount}` coins.",
                f"🎰 You rigged a poker game downtown and walked away with 💰 `{amount}` coins.",
                f"🚗 You chopped a stolen car for parts and earned 💰 `{amount}` coins.",
                f"📦 You intercepted a delivery truck and helped yourself to 💰 `{amount}` coins worth of goods.",
                f"🎭 You ran a shell game on tourists and conned them out of 💰 `{amount}` coins.",
                f"🏠 You broke into a mansion while the owners were on vacation. Scored 💰 `{amount}` coins.",
                f"💎 You swiped a necklace from a jewelry store during a 'distraction'. Made 💰 `{amount}` coins.",
                f"🖨️ You ran a counterfeit operation for a week. Printed 💰 `{amount}` coins worth of fake bills.",
                f"📱 You sold a stranger's lost phone and pocketed 💰 `{amount}` coins.",
                f"🃏 You marked the deck at a card game and hustled the table for 💰 `{amount}` coins.",
                f"🐟 You smuggled exotic fish across the border. Netted 💰 `{amount}` coins.",
            ]
        else:
            messages = [
                f"🚓 You got caught stealing a candy bar and had to pay a fine of 💰 `{abs(amount)}` coins.",
                f"🛑 You tried scamming someone but got scammed instead! Lost 💰 `{abs(amount)}` coins.",
                f"🚔 The cops caught you red-handed. You paid a fine of 💰 `{abs(amount)}` coins.",
                f"💸 You got caught trying to rob a bank! Lost 💰 `{abs(amount)}` coins.",
                f"👮 You got arrested for public indecency! Lost 💰 `{abs(amount)}` coins.",
                f"🐕 A police dog sniffed you out mid-heist. Paid 💰 `{abs(amount)}` coins in bail.",
                f"📸 A security camera caught your whole face. Fined 💰 `{abs(amount)}` coins.",
                f"🤝 Your getaway driver snitched on you. Lost 💰 `{abs(amount)}` coins in legal fees.",
                f"📵 You butt-dialed 911 mid-robbery. Cost you 💰 `{abs(amount)}` coins.",
                f"🪤 The 'distracted tourist' was an undercover cop. Lost 💰 `{abs(amount)}` coins.",
                f"🔦 You forgot to disable the motion sensors. Paid 💰 `{abs(amount)}` coins in fines.",
                f"🧾 You left your ID at the crime scene. Lost 💰 `{abs(amount)}` coins.",
                f"💀 The guy you pickpocketed turned out to be a mob boss. Lost 💰 `{abs(amount)}` coins... the easy way.",
                f"🎥 Someone live-streamed you committing the crime. Paid 💰 `{abs(amount)}` coins in damages.",
                f"🏃 You tripped running from the scene and got caught. Lost 💰 `{abs(amount)}` coins.",
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
                    f"☢️ Someone sent a link in the group chat. You made 💰 `{amount}` coins.",
                    f"📸 You started a spicy subscription page and raked in 💰 `{amount}` coins this month.",
                    f"🛥️ A billionaire invited you on his yacht for the weekend. You came back with 💰 `{amount}` coins.",
                    f"🎬 You starred in a low-budget film with a *very* generous wardrobe budget. Earned 💰 `{amount}` coins.",
                    f"💌 A lonely millionaire kept sliding into your DMs. You slid right back and made 💰 `{amount}` coins.",
                    f"🍾 You attended a 'networking event' in Vegas. Very productive. Made 💰 `{amount}` coins.",
                    f"🎤 You performed at a bachelorette party and the tips were incredible. Pocketed 💰 `{amount}` coins.",
                    f"🧴 A brand paid you to promote their 'personal wellness' products. Earned 💰 `{amount}` coins.",
                    f"🌹 You played the long game on a dating app and cashed out 💰 `{amount}` coins.",
                    f"🏨 A mysterious stranger booked you a 5-star hotel room and left an envelope. Inside: 💰 `{amount}` coins.",
                ]
            else:
                messages = [
                    f"👎 Nobody was interested in your services. You lost 💰 `{abs(amount)}` coins.",
                    f"🚔 The cops fined you for public indecency. Lost 💰 `{abs(amount)}` coins.",
                    f"🤮 You got sick and had to spend 💰 `{abs(amount)}` coins on meds.",
                    f"🤓 You were too ugly and had to spend 💰 `{abs(amount)}` coins on plastic surgery.",
                    f"💔 Your sugar daddy/mommy found someone younger. Lost 💰 `{abs(amount)}` coins in the breakup.",
                    f"📵 Your subscription page got reported and taken down. Lost 💰 `{abs(amount)}` coins.",
                    f"🧾 The 'millionaire' was broke. You covered dinner. Lost 💰 `{abs(amount)}` coins.",
                    f"😂 Someone recognized you at the grocery store. Paid 💰 `{abs(amount)}` coins in therapy.",
                    f"👻 The client ghosted you after you bought a new outfit for the occasion. Down 💰 `{abs(amount)}` coins.",
                    f"🎭 The modeling agency was a scam. Lost 💰 `{abs(amount)}` coins upfront.",
                    f"📸 Your face ended up on a billboard. Lawyer fees cost you 💰 `{abs(amount)}` coins.",
                    f"🍷 You got catfished by someone with a fake Rolex. Lost 💰 `{abs(amount)}` coins paying for the dinner.",
                    f"🐀 Your roommate reported you to management. Lost 💰 `{abs(amount)}` coins in deposits.",
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
                f"🛠️ You did some handyman work and earned 💰 `{amount}` coins.",
                f"🚑 You worked an overtime shift as a paramedic and earned 💰 `{amount}` coins.",
                f"🎨 You freelanced some graphic design work and invoiced 💰 `{amount}` coins.",
                f"🌿 You mowed lawns in the neighborhood all day and pocketed 💰 `{amount}` coins.",
                f"📦 You helped someone move apartments and made 💰 `{amount}` coins in cash.",
                f"🍕 You delivered pizzas all night and the tips were great. Earned 💰 `{amount}` coins.",
                f"🎧 You DJ'd a local house party and walked away with 💰 `{amount}` coins.",
                f"🐕 You walked dogs in the park all weekend and made 💰 `{amount}` coins.",
                f"📊 You filed quarterly reports and somehow stayed awake. Earned 💰 `{amount}` coins.",
                f"🏗️ You worked a construction site all week and collected 💰 `{amount}` coins.",
                f"🛒 You restocked shelves at a supermarket overnight and earned 💰 `{amount}` coins.",
                f"🎓 You tutored some struggling students and charged 💰 `{amount}` coins.",
                f"✈️ You worked a flight as cabin crew and landed 💰 `{amount}` coins in pay and tips.",
            ]
        else:
            messages = [
                f"👎 Your boss found you smoking on the clock! You lost 💰 `{abs(amount)}` coins.",
                f"👥 A coworker found out you had 2 jobs! You lost 💰 `{abs(amount)}` coins.",
                f"💸 You got caught stealing from the till! You lost 💰 `{abs(amount)}` coins.",
                f"🚔 You got caught slacking off all week. Docked 💰 `{abs(amount)}` coins from your pay.",
                f"👮 You got caught doing something illegal at work! You lost 💰 `{abs(amount)}` coins.",
                f"😴 You fell asleep during a client presentation. Lost 💰 `{abs(amount)}` coins in commission.",
                f"📧 You accidentally replied-all to a company-wide email. HR fined you 💰 `{abs(amount)}` coins somehow.",
                f"🖨️ You jammed the printer so badly they docked 💰 `{abs(amount)}` coins from your check.",
                f"🤳 You were on your phone during a safety briefing and caused an incident. Lost 💰 `{abs(amount)}` coins.",
                f"🐌 You missed every deadline this week. Client refund cost you 💰 `{abs(amount)}` coins.",
                f"🍔 You ate a coworker's labeled lunch for the third time. Paid them 💰 `{abs(amount)}` coins.",
                f"💻 You got a virus on the work computer from a suspicious site. IT bill: 💰 `{abs(amount)}` coins.",
                f"📦 You broke 3 deliveries in one day. Replacements cost 💰 `{abs(amount)}` coins.",
                f"🎮 Your boss found your gaming setup under the desk. Confiscated and fined 💰 `{abs(amount)}` coins.",
                f"🕐 You showed up 3 hours late every day this week. Lost 💰 `{abs(amount)}` coins in deductions.",
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
    @app_commands.describe(member="The member whose balance you want to check")
    @cooldown(cl=2, tm=25.0, ft=3)
    async def balance(self, interaction: discord.Interaction, member: discord.Member = None):
        await interaction.response.defer(ephemeral=False)
        target = member or interaction.user
        target_id = target.id
        await add_user(target_id, target.name)
        
        wallet = await get_balance(target_id)
        bank = await get_bank(target_id)
        bank_max = await get_bank_max(target_id)
        
        w_emoji = wallet_emoji(wallet)
        b_emoji = bank_emoji(bank, bank_max)

        embed = discord.Embed(
            title=f"{w_emoji} {target.display_name}'s Balance",
            color=discord.Color.green()
        )
        embed.set_thumbnail(url=target.display_avatar.url)
        embed.add_field(name=f"{w_emoji} Wallet", value=f"💰 `{wallet:,}` coins", inline=True)
        embed.add_field(name=f"{b_emoji} Bank", value=f"💰 `{bank:,}` / `{bank_max:,}` coins", inline=True)
        embed.add_field(name="📊 Total", value=f"💰 `{wallet + bank:,}` coins", inline=False)
        
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="deposit", description="Deposit coins from your wallet to your bank")
    @app_commands.describe(amount="Amount of coins to deposit (use 'all' to deposit maximum possible)")
    @cooldown(cl=3, tm=15.0, ft=3)
    async def deposit(self, interaction: discord.Interaction, amount: str):
        await interaction.response.defer(ephemeral=False)
        user_id = interaction.user.id
        await add_user(user_id, interaction.user.name)
        
        wallet = await get_balance(user_id)
        bank = await get_bank(user_id)
        bank_max = await get_bank_max(user_id)
        
        remaining_space = bank_max - bank
        if remaining_space <= 0:
            return await interaction.followup.send("❌ Your bank is already full!", ephemeral=True)
            
        if amount.lower().strip() in ["all", "max"]:
            dep_amount = min(wallet, remaining_space)
        else:
            try:
                dep_amount = int(amount)
            except ValueError:
                return await interaction.followup.send("❌ Please enter a valid number or 'all'!", ephemeral=True)
                
        if dep_amount <= 0:
            return await interaction.followup.send("❌ You must deposit at least 💰 `1` coin!", ephemeral=True)
            
        if dep_amount > wallet:
            return await interaction.followup.send("❌ You do not have that many coins in your wallet!", ephemeral=True)
            
        if dep_amount > remaining_space:
            return await interaction.followup.send(f"❌ You can't deposit that much! Your bank has only 💰 `{remaining_space:,}` coins of space left.", ephemeral=True)
            
        success = await deposit_bank(user_id, dep_amount)
        if success:
            await interaction.followup.send(f"✅ Successfully deposited 💰 `{dep_amount:,}` coins into your bank!")
        else:
            await interaction.followup.send("❌ Deposit failed due to insufficient funds or bank capacity!")

    @app_commands.command(name="withdraw", description="Withdraw coins from your bank to your wallet")
    @app_commands.describe(amount="Amount of coins to withdraw (use 'all' to withdraw all)")
    @cooldown(cl=3, tm=15.0, ft=3)
    async def withdraw(self, interaction: discord.Interaction, amount: str):
        await interaction.response.defer(ephemeral=False)
        user_id = interaction.user.id
        await add_user(user_id, interaction.user.name)
        
        bank = await get_bank(user_id)
        if bank <= 0:
            return await interaction.followup.send("❌ Your bank is completely empty!", ephemeral=True)
            
        if amount.lower().strip() in ["all", "max"]:
            with_amount = bank
        else:
            try:
                with_amount = int(amount)
            except ValueError:
                return await interaction.followup.send("❌ Please enter a valid number or 'all'!", ephemeral=True)
                
        if with_amount <= 0:
            return await interaction.followup.send("❌ You must withdraw at least 💰 `1` coin!", ephemeral=True)
            
        if with_amount > bank:
            return await interaction.followup.send("❌ You do not have that many coins in your bank!", ephemeral=True)
            
        success = await withdraw_bank(user_id, with_amount)
        if success:
            await interaction.followup.send(f"✅ Successfully withdrew 💰 `{with_amount:,}` coins from your bank!")
        else:
            await interaction.followup.send("❌ Withdrawal failed!")

    @app_commands.command(name="heist", description="Organize a crew to pull a bank heist on someone!")
    @app_commands.describe(target="The member whose bank you want to heist")
    @cooldown(cl=5400, tm=35.0, ft=3) # 1.5 hours heist cooldown for command users
    async def heist(self, interaction: discord.Interaction, target: discord.Member):
        await interaction.response.defer(ephemeral=False)
        leader = interaction.user
        leader_id = leader.id
        target_id = target.id
        
        if leader_id == target_id:
            return await interaction.followup.send("❌ You can't heist your own bank!", ephemeral=True)
            
        await add_user(leader_id, leader.name)
        await add_user(target_id, target.name)
        
        # Check target bank balance (needs >= 500)
        target_bank = await get_bank(target_id)
        if target_bank < 500:
            return await interaction.followup.send(f"❌ {target.mention}'s bank balance is too low (needs at least 💰 `500` coins) to make a heist worthwhile!", ephemeral=True)
            
        # Check leader wallet balance (needs >= 100)
        leader_wallet = await get_balance(leader_id)
        if leader_wallet < 100:
            return await interaction.followup.send("❌ You need at least 💰 `100` coins in your wallet to start a heist!", ephemeral=True)
            
        # Check heist victim cooldown (20 minutes)
        now = time.time()
        if target_id in _heist_victim_cooldowns:
            elapsed = now - _heist_victim_cooldowns[target_id]
            if elapsed < 1200: # 20 minutes
                remaining_min = round((1200 - elapsed) / 60, 1)
                return await interaction.followup.send(
                    f"❌ {target.mention} was recently targeted in a heist! You must wait **{remaining_min}m** before heisting them again.",
                    ephemeral=True
                )
                
        # Register victim heist cooldown immediately to avoid race conditions
        _heist_victim_cooldowns[target_id] = now
        
        # Create heist embed
        embed = discord.Embed(
            title="🚨 Bank Heist Initiated!",
            description=f"{leader.mention} is organizing a crew to hit {target.mention}'s bank vault!",
            color=discord.Color.red()
        )
        # Show the victim's avatar as a thumbnail
        embed.set_thumbnail(url=target.display_avatar.url)
        embed.add_field(name="🏦 Target Vault", value=f"💰 `{target_bank:,}` coins", inline=True)
        embed.add_field(name="🕒 Time Remaining", value="**30 seconds** to join or defend!", inline=True)
        embed.add_field(name="Crew Members", value=f"{leader.mention}", inline=False)
        embed.set_footer(text="Target: click 'Defend Bank' if you hold a defense item!")
        
        view = HeistView(
            leader_id=leader_id,
            target_id=target_id,
            target_name=target.name,
            target_bank_bal=target_bank
        )
        
        msg = await interaction.followup.send(embed=embed, view=view)
        view.message = msg
        
        # Ping the victim as a plain message so they receive a notification when this is a slash command
        try:
            is_prefix_invocation = hasattr(interaction, "message") and isinstance(getattr(interaction, "message"), discord.Message)
        except Exception:
            is_prefix_invocation = False

        if not is_prefix_invocation:
            try:
                await interaction.channel.send(f"{target.mention} — your bank is being targeted by a heist organized by {leader.mention}! React or click 'Defend Bank' to respond.")
            except Exception:
                pass

        # Wait 30 seconds for crew to gather / defense
        await asyncio.sleep(30)
        
        # If defended, view has already handled results
        if view.defended:
            return
            
        # Disable buttons
        for child in view.children:
            child.disabled = True
        try:
            await msg.edit(view=view)
        except:
            pass
        
        crew = view.participants
        crew_count = len(crew)
        
        # Cooldown all participating members for 1.5 hours
        from config import _user_command_cooldowns
        for pid in crew:
            _user_command_cooldowns[(pid, "heist")] = time.time()
            
        # Success calculations
        # Base: 20%, +8% per additional crew member (keeps raw probability modest)
        success_chance = 0.20 + (crew_count - 1) * 0.08

        # Check Hackatron 9900 (ID 8) boosts among crew members — stack boosts per device
        hackatron_count = 0
        for pid in crew:
            items = await get_user_items(pid)
            hack_item = next((i for i in items if str(i["item_id"]) == "8" and i["uses_left"] > 0), None)
            if hack_item:
                hackatron_count += 1
                new_uses = hack_item["uses_left"] - 1
                if new_uses <= 0:
                    await remove_item_from_user(pid, 8)
                else:
                    await update_item_uses(pid, 8, new_uses)

        if hackatron_count > 0:
            # Each Hackatron gives a small stackable boost; overall chance is hard-capped at 80%
            success_chance += 0.04 * hackatron_count

        # Hard cap to keep success probability at or below 80%
        success_chance = min(0.80, success_chance)
        
        success = random.random() < success_chance
        res_embed = discord.Embed()
        
        if success:
            # Stolen amount: 15% to 40% of target bank, capped at 15,000
            stolen_pct = random.uniform(0.15, 0.40)
            stolen_amount = int(target_bank * stolen_pct)
            stolen_amount = min(15000, stolen_amount)
            
            # Deduct from bank
            await decrease_bank(target_id, stolen_amount)
            
            # Split crew findings with leader bias (15% extra for leader, rest split equally)
            payout_details = []
            if crew_count == 1:
                await update_balance(leader_id, stolen_amount)
                payout_details.append(f"• <@{leader_id}> (Leader) took home all 💰 `{stolen_amount:,}` coins!")
            else:
                leader_bias = int(stolen_amount * 0.15)
                remaining_stolen = stolen_amount - leader_bias
                split_share = remaining_stolen // crew_count
                leftover = remaining_stolen % crew_count
                
                # Leader payout
                leader_share = split_share + leader_bias + leftover
                await update_balance(leader_id, leader_share)
                payout_details.append(f"• <@{leader_id}> (Leader) took home 💰 `{leader_share:,}` coins! (Includes 15% planner bias)")
                
                # Other crew payouts
                for pid in crew:
                    if pid == leader_id:
                        continue
                    await update_balance(pid, split_share)
                    payout_details.append(f"• <@{pid}> took home 💰 `{split_share:,}` coins!")
                    
            res_embed.title = "💰 Bank Heist Successful!"
            res_embed.description = f"The heist crew successfully breached {target.mention}'s bank vault and got away with 💰 `{stolen_amount:,}` coins!"
            res_embed.color = discord.Color.gold()
            res_embed.add_field(name="Heist Split Summary", value="\n".join(payout_details), inline=False)
            if hackatron_count > 0:
                res_embed.set_footer(text=f"📟 Heist security bypassed by {hackatron_count}x Hackatron 9900!")
        else:
            # Heist failed! Fines proportional to potential heist value
            stolen_pct = random.uniform(0.15, 0.40)
            stolen_amount = min(15000, int(target_bank * stolen_pct))
            
            # Proportional fine: 20% of potential steal amount, split, capped between 100 and 1,500 coins per crew member
            potential_fine = int((stolen_amount * 0.20) / crew_count)
            fine_per_member = max(100, min(1500, potential_fine))
            
            fine_details = []
            for pid in crew:
                await update_balance(pid, -fine_per_member)
                fine_details.append(f"• <@{pid}> was arrested and paid a fine of 💰 `{fine_per_member}` coins.")
                
            res_embed.title = "🚨 Heist Failed!"
            res_embed.description = f"The heist crew failed to crack {target.mention}'s vault! The silent alarm triggered and everyone was arrested!"
            res_embed.color = discord.Color.red()
            res_embed.add_field(name="Police Fines", value="\n".join(fine_details), inline=False)
            
        await interaction.channel.send(embed=res_embed)

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

    # ===================== Daily / Weekly / Monthly =====================
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

    async def run_weekly(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False)
        user_id = interaction.user.id
        await add_user(user_id, interaction.user.name)

        now = int(time.time())
        last_claim, _ = await get_last_claim(user_id, "weekly")
        elapsed = now - last_claim

        if last_claim > 0 and elapsed < WEEKLY_COOLDOWN:  # 6.5 days 12 hours of margin
            remaining = WEEKLY_COOLDOWN - elapsed
            days    = int(remaining // 86_400)
            hours   = int((remaining % 86_400) // 3600)
            return await interaction.followup.send(
                f"⏳ You already claimed your weekly! Come back in **{days}d {hours}h**.",
                ephemeral=True
            )

        reward = random.randint(2_000, 5_000)
        await update_balance(user_id, reward)
        await set_last_claim(user_id, "weekly", now, 0)
        log.successtrace(f"Weekly claimed by {user_id}: {reward} coins")

        embed = discord.Embed(
            title="📆 Weekly Reward!",
            description="A nice chunk of coins to start your week.",
            color=discord.Color.blue()
        )
        embed.add_field(name="Coins Earned", value=f"💰 **{reward:,}**", inline=False)
        embed.set_footer(text="See you next week!")
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="weekly", description="Claim your weekly coins. A solid boost for your week!")
    @cooldown(cl=5, tm=25.0, ft=3)
    async def weekly(self, interaction: discord.Interaction):
        await self.run_weekly(interaction)

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

        reward = random.randint(12_000, 20_000)
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

    @app_commands.command(name="leaderboard", description="View the wealthiest users.")
    @app_commands.choices(scope=[
        app_commands.Choice(name="Global", value="global"),
        app_commands.Choice(name="Server", value="server"),
    ])
    @cooldown(cl=10, tm=15.0, ft=3)
    async def leaderboard(self, interaction: discord.Interaction, scope: app_commands.Choice[str] = None):
        scope_val = scope.value if scope else "global"
        await interaction.response.defer(ephemeral=False)

        if scope_val == "global":
            if hasattr(self, "cog") and self.cog.global_leaderboard_cache:
                top_users = self.cog.global_leaderboard_cache
            else:
                top_users = await get_top_global_wealth(50)
            top_users = top_users[:50]
            title = "🌍 Global Economy Leaderboard"
            view = LeaderboardPaginator(top_users, title, False, interaction)
            await interaction.followup.send(embed=view.generate_embed(), view=view)
        else:
            if not interaction.guild:
                return await interaction.followup.send("❌ You can only view the server leaderboard inside a server.")

            member_ids = [m.id for m in interaction.guild.members if not m.bot]
            if not member_ids:
                return await interaction.followup.send("No valid members found.")

            conn = await db.get_economy()
            if len(member_ids) > 900:
                member_ids = member_ids[:900]

            placeholders = ",".join("?" for _ in member_ids)
            async with conn.execute(
                f"SELECT user_id, balance + bank AS wealth FROM users WHERE user_id IN ({placeholders}) ORDER BY wealth DESC LIMIT 50",
                tuple(member_ids)
            ) as cursor:
                top_users = await cursor.fetchall()

            title = f"🏢 {interaction.guild.name} Leaderboard"
            view = LeaderboardPaginator(top_users, title, True, interaction)
            await interaction.followup.send(embed=view.generate_embed(), view=view)

class EconomyCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.global_leaderboard_cache = []
        self.update_leaderboard.start()
    
    async def cog_load(self):
        economy_cmds = EconomyCommands()
        economy_cmds.cog = self
        self.bot.tree.add_command(economy_cmds)

    def cog_unload(self):
        self.update_leaderboard.cancel()

    @tasks.loop(minutes=15)
    async def update_leaderboard(self):
        try:
            self.global_leaderboard_cache = await get_top_global_wealth(100)
        except Exception as e:
            log.error(f"Failed to update economy leaderboard cache: {e}")

    @update_leaderboard.before_loop
    async def before_update_leaderboard(self):
        await self.bot.wait_until_ready()

async def setup(bot):
    await bot.add_cog(EconomyCog(bot))
