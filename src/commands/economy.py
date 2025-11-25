
# Standard Library Imports
import asyncio
import random


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
    check_gun_defense,
    decrement_gun_use,
    remove_item_from_user,
    update_item_uses,
    add_item_to_user
)
from config import cooldown
from discord import ui

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
        
        # Disable button and stop the view to prevent further clicks
        button.disabled = True
        self.stop()
        
        # Update the message to show the button is disabled (don't respond yet!)
        try:
            await interaction.message.edit(view=self)
        except:
            pass  # Message might be deleted or inaccessible
        
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

        if user_id == target_id:
            await interaction.followup.send("❌ You can't rob yourself!", ephemeral=True)
            return

        await add_user(user_id, interaction.user.name)
        await add_user(target_id, target.name)

        # Get robbery modifier for the user (could be items, perks, etc.)
        modifier = await get_robbery_modifier(user_id)
        # Base success chance is 40%
        base_success_chance = 0.4
        success_chance = base_success_chance + modifier

        if success_chance > 0.95:
            success_chance = 0.95  # Cap at 95%
        elif success_chance < 0.05:
            success_chance = 0.05  # Minimum 5%
        
        # 2nd amendment rights in a nutshell
        gun_defense = await check_gun_defense(target_id)
        if gun_defense:
            await decrement_gun_use(target_id)
            await interaction.followup.send(
            f"🔫 {target.mention} defended themselves with a gun! Your robbery failed.",
            ephemeral=False
            )
            return

        success = random.random() < success_chance

        robber_balancer = await get_balance(user_id)
        if robber_balancer < -50:
            return await interaction.followup.send("💸 You can't afford risking another crime!")

        target_balance = await get_balance(target_id)
        if target_balance < 100:
            return await interaction.followup.send(f"💸 {target.mention} doesn't have enough coins to rob!", ephemeral=True)

        if success:
            amount = random.randint(50, min(300, target_balance))
            await update_balance(user_id, amount)
            await update_balance(target_id, -amount)
            messages = [
                f"🦹 You successfully robbed {target.mention} and stole 💰 `{amount}` coins!",
                f"💰 You snuck up on {target.mention} and got away with `{amount}` coins!",
                f"🔪 You threatened {target.mention} and took `{amount}` coins!",
                f"💵 You pickpocketed {target.mention} and made off with `{amount}` coins!",
            ]
            msg_content = random.choice(messages)
        else:
            penalty = random.randint(50, 400)
            await update_balance(user_id, -penalty)
            messages = [
                f"🚨 You got caught trying to rob {target.mention}! You paid a fine of 💰 `{penalty}` coins.",
                f"👮 The police stopped your robbery attempt. Lost 💰 `{penalty}` coins.",
                f"😬 {target.mention} fought back! You lost 💰 `{penalty}` coins.",
                f"🚓 {target.mention} made you trip and the police caught you! You lost 💰`{penalty} coins.`"
            ]
            msg_content = random.choice(messages)
            
        await interaction.followup.send(msg_content, ephemeral=False)

    @app_commands.command(name="rob", description="Rob someone for cash. Risky!")
    @cooldown(cl=600, tm=25.0, ft=3)
    async def rob(self, interaction: discord.Interaction, target: discord.Member):
        await self.run_rob(interaction, target)

    async def run_crime(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False)
        user_id = interaction.user.id
        await add_user(user_id, interaction.user.name)

        commiter_bal = await get_balance(user_id)
        if commiter_bal < -100:
            return await interaction.followup.send("💸 You can't afford risking another crime!")

        success = random.random() > 0.16  
        amount = random.randint(100, 600) if success else -random.randint(300, 600)

        await update_balance(user_id, amount)

        if success:
            messages = [
                f"🕵️‍♂️ You successfully pickpocketed an old man and got 💰 `{amount}` coins.",
                f"🔫 You robbed a small convenience store and walked away with 💰 `{amount}` coins.",
                f"💻 You hacked into a bank’s system and stole 💰 `{amount}` coins. Nice job!",
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


        view = PlayAgainView(self.run_crime, user_id)
        msg = await interaction.followup.send(random.choice(messages), ephemeral=False, view=view)
        view.message = msg

    @app_commands.command(name="crime", description="Commit a crime for cash. Risky!")
    @cooldown(cl=8, tm=25.0, ft=3)
    async def crime(self, interaction: discord.Interaction):
        await self.run_crime(interaction)

    async def run_slut(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False)
        user_id = interaction.user.id
        await add_user(user_id, interaction.user.name)

        success = random.random() > 0.07
        amount = random.randint(50, 300) if success else -random.randint(100, 200)

        await update_balance(user_id, amount)

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

        view = PlayAgainView(self.run_slut, user_id)
        msg = await interaction.followup.send(random.choice(messages), ephemeral=False, view=view)
        view.message = msg

    @app_commands.command(name="slut", description="Do some... work for quick cash.")
    @cooldown(cl=10, tm=25.0, ft=3) # Horny bastards.
    async def slut(self, interaction: discord.Interaction):
        await self.run_slut(interaction)

    async def run_work(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False)
        user_id = interaction.user.id
        await add_user(user_id, interaction.user.name)

        success = random.random() > 0.03
        amount = random.randint(20, 250) if success else -random.randint(400, 800)
        await update_balance(user_id, amount)

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

        view = PlayAgainView(self.run_work, user_id)
        msg = await interaction.followup.send(random.choice(messages), ephemeral=False, view=view)
        view.message = msg

    @app_commands.command(name="work", description="Do a normal job for guaranteed(ish) cash.")
    @cooldown(cl=6, tm=25.0, ft=3)
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

        balance = await get_balance(user_id)
        if balance < amount:
            await interaction.followup.send("❌ You don't have enough coins!", ephemeral=True)
            return

        await update_balance(user_id, -amount)
        await update_balance(target_id, amount)

        await interaction.followup.send(f"💸 You transferred {target.mention} 💰 `{amount}` coins!", ephemeral=False)

    @app_commands.command(name="give", description="Give an item (or items) to another user")
    @cooldown(cl=10, tm=25.0, ft=3)
    async def give(self, interaction: discord.Interaction, target: discord.Member, item_id: int, amount: int):
        await interaction.response.defer(ephemeral=False)
        user_id = interaction.user.id
        target_id = target.id

        if user_id == target_id:
            await interaction.followup.send("❌ You can't give items to yourself!", ephemeral=True)
            return

        await add_user(user_id, interaction.user.name)
        await add_user(target_id, target.name)

        if amount <= 0:
            await interaction.followup.send("❌ Invalid amount!", ephemeral=True)
            return

        items = await get_user_items(user_id)
        item = next((item for item in items if item['item_id'] == item_id), None)

        if not item or item['uses_left'] < amount:
            await interaction.followup.send("❌ You don't have enough of that item!", ephemeral=True)
            return

        # Decrement the item's uses from the sender's inventory
        item['uses_left'] -= amount

        # Remove the item from sender if uses_left is 0
        if item['uses_left'] == 0:
            # Remove the item from the user's inventory in the database
            await remove_item_from_user(user_id, item_id)
        else:
            # Update the item uses in the database
            await update_item_uses(user_id, item_id, item['uses_left'])

        # Add the item to the target's inventory
        await add_item_to_user(target_id, item_id, amount)

        await interaction.followup.send(f"🎁 You gave {target.mention} {amount} of item ID `{item_id}`!", ephemeral=False)


class EconomyCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
    
    async def cog_load(self):
        self.bot.tree.add_command(EconomyCommands())

async def setup(bot):
    await bot.add_cog(EconomyCog(bot))
