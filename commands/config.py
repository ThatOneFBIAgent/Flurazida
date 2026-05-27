# Standard Library Imports
from typing import Optional

# Third-Party Imports
import discord
from discord.ext import commands
from discord import app_commands, Interaction

# Local Imports
from logging_modules.custom_logger import get_logger

log = get_logger()

VALID_MODULES = ["economy", "moderator", "image"]

class ConfigCommands(app_commands.Group):
    def __init__(self, bot):
        super().__init__(name="module", description="Configure bot modules")
        self.bot = bot

    async def _update_module(self, interaction: Interaction, module_name: str, enabled: bool):
        if not interaction.guild:
            return await interaction.response.send_message("❌ This command must be used in a server.", ephemeral=True)
            
        module_name = module_name.lower()
        if module_name not in VALID_MODULES:
            valid_str = ", ".join(VALID_MODULES)
            return await interaction.response.send_message(f"❌ Invalid module. Valid modules are: {valid_str}", ephemeral=True)
            
        if not hasattr(self.bot, "config_sync"):
            return await interaction.response.send_message("❌ Config sync is currently offline.", ephemeral=True)
            
        await interaction.response.defer(ephemeral=False)
        
        settings = self.bot.config_sync.get(interaction.guild.id)
        if not settings:
            settings = {"modules": {}}
        if "modules" not in settings:
            settings["modules"] = {}
            
        settings["modules"][module_name] = enabled
        
        success = await self.bot.config_sync.push_config(interaction.guild.id, settings)
        
        state_str = "enabled" if enabled else "disabled"
        if success:
            await interaction.followup.send(f"✅ Module `{module_name}` has been **{state_str}** for this server.")
        else:
            await interaction.followup.send(f"⚠️ Successfully **{state_str}** `{module_name}` locally, but failed to sync to the dashboard.")

    @app_commands.command(name="enable", description="Enable a module in this server")
    @app_commands.describe(module_name="The module to enable")
    @app_commands.default_permissions(administrator=True)
    async def enable(self, interaction: Interaction, module_name: str):
        await self._update_module(interaction, module_name, True)

    @app_commands.command(name="disable", description="Disable a module in this server")
    @app_commands.describe(module_name="The module to disable")
    @app_commands.default_permissions(administrator=True)
    async def disable(self, interaction: Interaction, module_name: str):
        await self._update_module(interaction, module_name, False)

class ConfigCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
    
    async def cog_load(self):
        self.bot.tree.add_command(ConfigCommands(self.bot))

async def setup(bot):
    await bot.add_cog(ConfigCog(bot))
