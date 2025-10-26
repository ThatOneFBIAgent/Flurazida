import random, discord, asyncio
from discord import Interaction
from discord.ext import commands
from discord import app_commands
from database import get_balance, update_balance, add_user, get_user_items, get_robbery_modifier, check_gun_defense, decrement_gun_use
from database import remove_item_from_user, update_item_uses, add_item_to_user
from config import cooldown, safe_command

class EconomyCommands(commands.Cog):
    def __init__(self):
        super().__init__(name="economy", desciption="Economy related commands")

    # @safe_command(timeout=15.0)
    @app_commands.command(name="rob", description="Rob someone for cash. Risky!")
    @cooldown(600) # 600s = 10 minutes, stop the stinky rats from draining people.
    async def rob(self, interaction: discord.Interaction, target: discord.Member):
        await interaction.response.defer(ephemeral=False)
        user_id = interaction.user.id
        target_id = target.id

        if user_id == target_id:
            await interaction.followup.send("❌ You can't rob yourself!", ephemeral=True)
            return

        add_user(user_id, interaction.user.name)
        add_user(target_id, target.name)

        # Get robbery modifier for the user (could be items, perks, etc.)
        modifier = get_robbery_modifier(user_id)
        # Base success chance is 40%
        base_success_chance = 0.4
        success_chance = base_success_chance + modifier

        if success_chance > 0.95:
            success_chance = 0.95  # Cap at 95%
        elif success_chance < 0.05:
            success_chance = 0.05  # Minimum 5%
        
        # 2nd amendment rights in a nutshell
        gun_defense = check_gun_defense(target_id)
        if gun_defense:
            decrement_gun_use(target_id)
            await interaction.followup.send(
            f"🔫 {target.mention} defended themselves with a gun! Your robbery failed.",
            ephemeral=False
            )
            return

        success = random.random() < success_chance

        target_balance = get_balance(target_id)
        if target_balance < 100:
            await interaction.followup.send(f"💸 {target.mention} doesn't have enough coins to rob!", ephemeral=True)
            return

        if success:
            amount = random.randint(50, min(300, target_balance))
            update_balance(user_id, amount)
            update_balance(target_id, -amount)
            messages = [
                f"🦹 You successfully robbed {target.mention} and stole 💰 `{amount}` coins!",
                f"💰 You snuck up on {target.mention} and got away with `{amount}` coins!",
                f"🔪 You threatened {target.mention} and took `{amount}` coins!",
                f"💵 You pickpocketed {target.mention} and made off with `{amount}` coins!",
            ]
            await interaction.followup.send(random.choice(messages), ephemeral=False)
        else:
            penalty = random.randint(50, 400)
            update_balance(user_id, -penalty)
            messages = [
                f"🚨 You got caught trying to rob {target.mention}! You paid a fine of 💰 `{penalty}` coins.",
                f"👮 The police stopped your robbery attempt. Lost 💰 `{penalty}` coins.",
                f"😬 {target.mention} fought back! You lost 💰 `{penalty}` coins.",
                f"🚓 {target.mention} made you trip and the police caught you! You lost 💰`{penalty} coins.`"
            ]
            await interaction.followup.send(random.choice(messages), ephemeral=False)

    # @safe_command(timeout=15.0)
    @app_commands.command(name="crime", description="Commit a crime for cash. Risky!")
    @cooldown(8)
    async def crime(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False)
        user_id = interaction.user.id
        add_user(user_id, interaction.user.name)

        success = random.random() > 0.4  
        amount = random.randint(100, 600) if success else -random.randint(300, 600)

        update_balance(user_id, amount)

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

        await interaction.followup.send(random.choice(messages), ephemeral=False)

    # @safe_command(timeout=15.0)
    @app_commands.command(name="slut", description="Do some... work for quick cash.")
    @cooldown(10) # Horny bastards.
    async def slut(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False)
        user_id = interaction.user.id
        add_user(user_id, interaction.user.name)

        success = random.random() > 0.1
        amount = random.randint(50, 300) if success else -random.randint(100, 200)

        update_balance(user_id, amount)

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

        await interaction.followup.send(random.choice(messages), ephemeral=False)

    # @safe_command(timeout=15.0)
    @app_commands.command(name="work", description="Do a normal job for guaranteed(ish) cash.")
    @cooldown(4)
    async def work(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False)
        user_id = interaction.user.id
        add_user(user_id, interaction.user.name)

        success = random.random() > 0.05
        amount = random.randint(20, 250) if success else -random.randint(400, 800)
        update_balance(user_id, amount)

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

        await interaction.followup.send(random.choice(messages), ephemeral=False)

    # @safe_command(timeout=15.0)
    @app_commands.command(name="balance", description="Check your current balance")
    @cooldown(2)
    async def balance(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False)
        user_id = interaction.user.id
        balance = get_balance(user_id)
        await interaction.followup.send(f"💰 Your balance: **{balance}** coins")

    # @safe_command(timeout=20.0)
    @app_commands.command(name="inventory", description="Check your inventory")
    @cooldown(4)
    async def inventory(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False)
        user_id = interaction.user.id
        add_user(user_id, interaction.user.name)

        items = get_user_items(user_id)
        if items:
            inventory_message = "\n".join(
                # Format each inventory item with its name, ID, and remaining uses
                [f"🔹 {item['item_name']} (ID: {item['item_id']}) - Uses left: {item['uses_left']}" for item in items]
            )
            await interaction.followup.send(f"📦 Your inventory:\n{inventory_message}", ephemeral=True)

        else:
            await interaction.followup.send("📦 You have no items in your inventory!", ephemeral=True)
    
    # @safe_command(timeout=20.0)
    @app_commands.command(name="transfer", description="Give money to another user")
    @cooldown(6) # Should mitigate some db spam since it makes 6 instances.. for some reason?
    async def transfer(self, interaction: discord.Interaction, target: discord.Member, amount: int):
        await interaction.response.defer(ephemeral=False)
        user_id = interaction.user.id
        target_id = target.id

        if user_id == target_id:
            await interaction.followup.send("❌ You can't transfer money to yourself!", ephemeral=True)
            return

        add_user(user_id, interaction.user.name)
        add_user(target_id, target.name)

        if amount <= 0:
            await interaction.followup.send("❌ Invalid amount!", ephemeral=True)
            return

        balance = get_balance(user_id)
        if balance < amount:
            await interaction.followup.send("❌ You don't have enough coins!", ephemeral=True)
            return

        update_balance(user_id, -amount)
        update_balance(target_id, amount)

        await interaction.followup.send(f"💸 You transferred {target.mention} 💰 `{amount}` coins!", ephemeral=False)

    # @safe_command(timeout=15.0)
    @app_commands.command(name="give", description="Give an item (or items) to another user")
    @cooldown(10)
    async def give(self, interaction: discord.Interaction, target: discord.Member, item_id: int, amount: int):
        await interaction.response.defer(ephemeral=False)
        user_id = interaction.user.id
        target_id = target.id

        if user_id == target_id:
            await interaction.followup.send("❌ You can't give items to yourself!", ephemeral=True)
            return

        add_user(user_id, interaction.user.name)
        add_user(target_id, target.name)

        if amount <= 0:
            await interaction.followup.send("❌ Invalid amount!", ephemeral=True)
            return

        items = get_user_items(user_id)
        item = next((item for item in items if item['item_id'] == item_id), None)

        if not item or item['uses_left'] < amount:
            await interaction.followup.send("❌ You don't have enough of that item!", ephemeral=True)
            return

        # Decrement the item's uses from the sender's inventory
        item['uses_left'] -= amount

        # Remove the item from sender if uses_left is 0
        if item['uses_left'] == 0:
            # Remove the item from the user's inventory in the database
            remove_item_from_user(user_id, item_id)
        else:
            # Update the item uses in the database
            update_item_uses(user_id, item_id, item['uses_left'])

        # Add the item to the target's inventory
        add_item_to_user(target_id, item_id, amount)

        await interaction.followup.send(f"🎁 You gave {target.mention} {amount} of item ID `{item_id}`!", ephemeral=False)


class EconomyCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
    
    async def cog_load(self):
        self.bot.tree.add_command(EconomyCommands())

async def setup(bot):
    await bot.add_cog(EconomyCog(bot))
