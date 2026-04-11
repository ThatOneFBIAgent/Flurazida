# Standard Library Imports
import asyncio
import re


# Third-Party Imports
import discord
from discord.ext import commands
from discord import app_commands, Interaction


# Local Imports
from database import buy_item, modify_robber_multiplier, use_item
from config import cooldown
from logging_modules.custom_logger import get_logger


log = get_logger()

from database.items import SHOP_ITEMS
# absolutely overly redundant id system becuase fuck you that's why (i can't index for shit)

SHOP_PAGE_TIMEOUT = 180


class ShopView(discord.ui.View):
    def __init__(self, user_id, page=0):
        super().__init__(timeout=SHOP_PAGE_TIMEOUT)  # Buttons expire after timeout
        self.user_id = user_id
        self.page = page
        self.pages = [SHOP_ITEMS[i:i + 4] for i in range(0, len(SHOP_ITEMS), 4)]

    def format_shop_page(self):
        """Formats the current page of shop items into an embed"""
        embed = discord.Embed(title="🛒 Shop", description=f"Page {self.page + 1}/{len(self.pages)}", color=discord.Color.blue())
        for item in self.pages[self.page]:
            embed.add_field(name=f"**{item['name']}**", value=f"💰 {item['price']} coins\n🔹 {item['effect']}", inline=False)
        return embed

    @discord.ui.button(label="⬅️", style=discord.ButtonStyle.grey)
    async def previous_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Moves to the previous page"""
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("❌ This isn't your shop session!", ephemeral=True)
        if self.page > 0:
            self.page -= 1
            await interaction.response.edit_message(embed=self.format_shop_page(), view=self)

    @discord.ui.button(label="🛒 Buy Item", style=discord.ButtonStyle.green)
    async def on_buy_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Prompts the user with a modal form to enter the item name"""
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("❌ This isn't your shop session!", ephemeral=True)

        class BuyItemModal(discord.ui.Modal, title="Buy Item"):
            item_name = discord.ui.TextInput(
                label="Item Name",
                placeholder="Enter the name of the item you want to buy",
                required=True,
                max_length=50,
            )

            def __init__(self, user_id: int):
                super().__init__()
                self.user_id = user_id

            async def on_submit(self, interaction: discord.Interaction):
                if interaction.user.id != self.user_id:
                    return await interaction.response.send_message("❌ Not your modal!", ephemeral=True)

                name = self.item_name.value.strip().lower()
                item_data = next((item for item in SHOP_ITEMS if item["name"].lower() == name), None)
                if not item_data:
                    return await interaction.response.send_message(f"❌ '{self.item_name.value}' not found!", ephemeral=True)

                success = await buy_item(self.user_id, item_data["id"], item_data["name"], item_data["price"])
                if success:
                    await interaction.response.send_message(
                        f"✅ **{interaction.user.mention} bought {item_data['name']} for {item_data['price']} coins!**"
                    )
                else:
                    await interaction.response.send_message("❌ Not enough money!", ephemeral=True)


        await interaction.response.send_modal(BuyItemModal(self.user_id))

    @discord.ui.button(label="➡️", style=discord.ButtonStyle.grey)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Moves to the next page"""
        if interaction.user.id != self.user_id:
            return await interaction.followup.send("❌ This isn't your shop session!", ephemeral=True)
        if self.page < len(self.pages) - 1:
            self.page += 1
            await interaction.response.edit_message(embed=self.format_shop_page(), view=self)

    @discord.ui.button(label="❌ Cancel", style=discord.ButtonStyle.red)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Cancels the shop interaction"""
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("❌ This isn't your shop session!", ephemeral=True)
        await interaction.response.edit_message(content="🛑 **Shop session cancelled.**", embed=None, view=None)
        self.stop()

    @staticmethod
    def leetspeak_to_text(text):
        """Convert l33tspeak to normal text for better item recognition"""
        leet_dict = {"4": "a", "3": "e", "1": "i", "0": "o", "5": "s", "7": "t"}
        return re.sub(r"[431057]", lambda x: leet_dict[x.group()], text)
        # gen no idea why i made this func i am so sorry

class ShopCommands(app_commands.Group):
    def __init__(self):
        super().__init__(name="shop", description="Shop related commands")

    # so this was "shop", but i changed it to "view" becuase it appears as fucking /shop shop in discord.
    @app_commands.command(name="view", description="View and buy items from the shop.")
    @cooldown(cl=5, tm=15.0, ft=3)
    async def shop(self, interaction: discord.Interaction):
        """Displays shop items using embeds and buttons"""
        await interaction.response.defer()
        log.info(f"Shop view invoked by {interaction.user.id}")
        if not SHOP_ITEMS:
            return await interaction.response.send_message("❌ The shop is empty!", ephemeral=True)

        view = ShopView(interaction.user.id)
        await interaction.followup.send(embed=view.format_shop_page(), view=view, ephemeral=False)

    @app_commands.command(name="use", description="Use an item from your inventory")
    @app_commands.describe(item_name="The name of the item you want to use")
    @cooldown(cl=5, tm=15.0, ft=3)
    async def use(self, interaction: discord.Interaction, item_name: str):
        """Handles using an item properly"""
        await interaction.response.defer()
        log.info(f"Use item invoked by {interaction.user.id}: {item_name}")
        item_name = item_name.lower()
        item_data = next((item for item in SHOP_ITEMS if item["name"].lower() == item_name), None)

        if not item_data:
            return await interaction.response.send_message(f"❌ **'{item_name}' is not a valid item!**", ephemeral=True)

        # Use the centralized item effect logic
        result_message = await use_item(interaction.user.id, item_data["id"])
        await interaction.followup.send(result_message, ephemeral=True)

class ShopCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
    
    async def cog_load(self):
        self.bot.tree.add_command(ShopCommands())

async def setup(bot):
    await bot.add_cog(ShopCog(bot))
