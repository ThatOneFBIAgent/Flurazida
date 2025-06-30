import discord, asyncio, logging, time, sys
from discord.ext import commands
from discord import app_commands
from discord import Interaction
from database import get_cases_for_guild, get_case, insert_case, remove_case, edit_case_reason

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)

class Moderator(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="mute", description="Mutes selected user for a period of time, default is 1 hour.")
    @app_commands.describe(
        user="The user to mute",
        reason="The reason for the mute",
        duration="Duration of the mute (default is 1 hour, format: 1h, 30m, etc.)"
    )
    @app_commands.checks.has_permissions(timeout_members=True, moderate_members=True)
    @app_commands.checks.bot_has_permissions(timeout_members=True, moderate_members=True, manage_roles=True)
    async def mute(self, interaction: Interaction, user: discord.Member, reason: str = None, duration: str = "1h"):
        """Mutes a user for a specified duration."""
        if user == interaction.user:
            return await interaction.response.send_message("❌ You cannot mute yourself!", ephemeral=True)
        if user.id == self.bot.user.id:
            return await interaction.response.send_message("❌ You cannot mute the bot!", ephemeral=True)
        if user.top_role >= interaction.user.top_role:
            return await interaction.response.send_message("❌ You cannot mute a user with a higher or equal role!", ephemeral=True)
        if self.bot.user.top_role >= user.top_role:
            return await interaction.response.send_message("❌ I cannot mute a user with a higher or equal role than my own!", ephemeral=True)
        if user.guild_permissions.administrator:
            return await interaction.response.send_message("❌ You cannot mute an administrator!", ephemeral=True)
        if manage_roles := interaction.guild.me.guild_permissions.manage_roles or interaction.guild.me.guild_permissions.moderate_members or interaction.guild.me.guild_permissions.timeout_members:
            if not manage_roles:
                return await interaction.response.send_message("❌ I do not have sufficient permissions to mute!", ephemeral=True)
        

        # Default duration is 1 hour
        if duration is None:
            duration = 3600  # 1 hour in seconds
        if reason is None:
            reason = "No reason provided"
        
        # Parse duration
        duration_seconds = 0
        if duration.endswith("d"):
            duration_seconds = int(duration[:-1]) * 86400
        elif duration.endswith("h"):
            duration_seconds = int(duration[:-1]) * 3600
        elif duration.endswith("m"):
            duration_seconds = int(duration[:-1]) * 60

        # Mute logic (create if not exists mute role and add to user)
        try:
            mute_role = discord.utils.get(interaction.guild.roles, name="Muted")
            if not mute_role:
                mute_role = await interaction.guild.create_role(name="Muted", reason="Mute role created by bot")
                await interaction.guild.edit_role_permissions(
                    mute_role,
                    send_messages=False,
                    speak=False,
                    add_reactions=False,
                    connect=False
                )
            # Log the mute in the database
            insert_case(
                interaction.guild.id, user.id, user.name, reason, "mute", interaction.user.id, int(time.time())
            )
            await interaction.response.send_message(f"✅ **{user.mention} has been muted for {duration}**", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("❌ I do not have permission to mute this user!", ephemeral=True)
        except Exception as e:
            logging.error(f"Error muting user: {e}")
            await interaction.response.send_message("❌ An error occurred while trying to mute the user.", ephemeral=True)
    


