import discord, asyncio, time, sys
from discord.ext import commands
from discord import app_commands
from discord import Interaction
from database import get_cases_for_guild, get_case, insert_case, remove_case, edit_case_reason, mod_cursor
from logger import get_logger
from config import cooldown

log = get_logger("moderator")

class ModeratorCommands(app_commands.Group):
    def __init__(self, bot):
        super().__init__(name="moderator", description="Moderation related commands")
        self.bot = bot

    # @safe_command(timeout=15.0)
    @app_commands.command(name="mute", description="Mutes selected user for a period of time, default is 1 hour.")
    @app_commands.describe(
        user="The user to mute",
        reason="The reason for the mute",
        duration="Duration of the mute (default is 1 hour, format: 1h, 30m, etc.)"
    )
    @app_commands.checks.has_permissions(moderate_members=True)
    @app_commands.checks.bot_has_permissions(moderate_members=True, manage_roles=True)
    @cooldown(cl=5, tm=15.0, ft=3)
    async def mute(self, interaction: Interaction, user: discord.Member, reason: str = None, duration: str = None):
        await interaction.response.defer(ephemeral=False)
        """Mutes a user for a specified duration."""
        if user == interaction.user:
            return await interaction.followup.send("❌ You cannot mute yourself!", ephemeral=True)
        if user.id == self.bot.user.id:
            return await interaction.followup.send("❌ You cannot mute the bot!", ephemeral=True)
        if user.top_role >= interaction.user.top_role:
            return await interaction.followup.send("❌ You cannot mute a user with a higher or equal role!", ephemeral=True)
        bot_member = interaction.guild.get_member(self.bot.user.id)
        if bot_member is None:
            bot_member = await interaction.guild.fetch_member(self.bot.user.id)
        if user.top_role >= bot_member.top_role:
            return await interaction.followup.send("❌ I cannot mute a user with a higher or equal role than my own!", ephemeral=True)
        if user.guild_permissions.administrator:
            return await interaction.followup.send("❌ You cannot mute an administrator!", ephemeral=True)
        if manage_roles := interaction.guild.me.guild_permissions.manage_roles or interaction.guild.me.guild_permissions.moderate_members or interaction.guild.me.guild_permissions.moderate_members:
            if not manage_roles:
                return await interaction.followup.send("❌ I do not have sufficient permissions to mute!", ephemeral=True)
        

        # Default duration is 1 hour
        if duration is None:
            duration = "1h"  # 1 hour in seconds
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
                for channel in interaction.guild.channels:
                    try:
                        await channel.set_permissions(mute_role, send_messages=False, speak=False, read_message_history=True, read_messages=True)
                    except discord.Forbidden:
                        log.warning(f"Could not set permissions for {mute_role} in {channel.name}.")

            # Log the mute in the database & mute the user
            await user.add_roles(mute_role, reason="Muted by command")
            if duration_seconds > 0:
                expiry_time = int(time.time()) + duration_seconds 
                insert_case(
                    mod_cursor, interaction.guild.id, user.id, user.name, reason, "mute", interaction.user.id, int(time.time()), expiry=expiry_time
                )
            elif duration_seconds == 0:
                expiry_time = 0
                insert_case(
                    mod_cursor, interaction.guild.id, user.id, user.name, reason, "mute", interaction.user.id, int(time.time())
                )
            await interaction.followup.send(f"✅ **{user.mention} has been muted for {duration}**", ephemeral=False)
        except discord.Forbidden:
            await interaction.followup.send("❌ I do not have permission to mute this user!", ephemeral=True)
        except Exception as e:
            log.error(f"Error muting user: {e}")
            await interaction.followup.send("❌ An error occurred while trying to mute the user.", ephemeral=True)

    # @safe_command(timeout=15.0)
    @app_commands.command(name="unmute", description="Unmutes a user.")
    @app_commands.describe(user="The user to unmute")
    @app_commands.checks.has_permissions(moderate_members=True)
    @app_commands.checks.bot_has_permissions(moderate_members=True, manage_roles=True)
    @cooldown(cl=5, tm=15.0, ft=3)
    async def unmute(self, interaction: Interaction, user: discord.Member):
        await interaction.response.defer(ephemeral=False)
        """Unmutes a user."""
        if user == interaction.user:
            return await interaction.followup.send("❌ You cannot unmute yourself!", ephemeral=True)
        if user.id == self.bot.user.id:
            return await interaction.followup.send("❌ You cannot unmute the bot!", ephemeral=True)
        if user.top_role >= interaction.user.top_role:
            return await interaction.followup.send("❌ You cannot unmute a user with a higher or equal role!", ephemeral=True)
        bot_member = interaction.guild.get_member(self.bot.user.id)
        if bot_member is None:
            bot_member = await interaction.guild.fetch_member(self.bot.user.id)
        if user.top_role >= bot_member.top_role:
            return await interaction.followup.send("❌ I cannot unmute a user with a higher or equal role than my own!", ephemeral=True)
        if user.guild_permissions.administrator:
            return await interaction.followup.send("❌ You cannot unmute an administrator!", ephemeral=True)

        mute_role = discord.utils.get(interaction.guild.roles, name="Muted")
        if mute_role not in user.roles:
            return await interaction.followup.send("❌ This user is not muted!", ephemeral=True)

        try:
            await user.remove_roles(mute_role, reason="Unmuted by command")
            # do not remove cases, as they are permanent records.
            await interaction.followup.send(f"✅ **{user.mention} has been unmuted**", ephemeral=False)
        except discord.Forbidden:
            await interaction.followup.send("❌ I do not have permission to unmute this user!", ephemeral=True)
        except Exception as e:
            log.error(f"Error unmuting user: {e}")
            await interaction.followup.send("❌ An error occurred while trying to unmute the user.", ephemeral=True)

    # @safe_command(timeout=15.0)
    @app_commands.command(name="kick", description="Kicks a user from the server.")
    @app_commands.describe(user="The user to kick", reason="The reason for the kick")
    @app_commands.checks.has_permissions(kick_members=True)
    @app_commands.checks.bot_has_permissions(kick_members=True)
    @cooldown(cl=5, tm=15.0, ft=3)
    async def kick(self, interaction: Interaction, user: discord.Member, reason: str = None):
        await interaction.response.defer(ephemeral=False)
        """Kicks a user from the server."""
        if user == interaction.user:
            return await interaction.followup.send("❌ You cannot kick yourself!", ephemeral=True)
        if user.id == self.bot.user.id:
            return await interaction.followup.send("❌ You cannot kick the bot!", ephemeral=True)
        if user.top_role >= interaction.user.top_role:
            return await interaction.followup.send("❌ You cannot kick a user with a higher or equal role!", ephemeral=True)
        bot_member = interaction.guild.get_member(self.bot.user.id)
        if bot_member is None:
            bot_member = await interaction.guild.fetch_member(self.bot.user.id)
        if user.top_role >= bot_member.top_role:
            return await interaction.followup.send("❌ I cannot kick a user with a higher or equal role than my own!", ephemeral=True)
        if user.guild_permissions.administrator:
            return await interaction.followup.send("❌ You cannot kick an administrator!", ephemeral=True)

        if reason is None:
            reason = "No reason provided"

        try:
            await user.kick(reason=reason)
            insert_case(mod_cursor, interaction.guild.id, user.id, user.name, reason, "kick", interaction.user.id, int(time.time()))
            await interaction.followup.send(f"✅ **{user.mention} has been kicked**", ephemeral=False)
        except discord.Forbidden:
            await interaction.followup.send("❌ I do not have permission to kick this user!", ephemeral=True)
        except Exception as e:
            log.error(f"Error kicking user: {e}")
            await interaction.followup.send("❌ An error occurred while trying to kick the user.", ephemeral=True)
    
    # @safe_command(timeout=15.0)
    @app_commands.command(name="ban", description="Bans a user from the server.")
    @app_commands.describe(user="The user to ban", reason="The reason for the ban", duration="Duration of the ban")
    @app_commands.checks.has_permissions(ban_members=True)
    @app_commands.checks.bot_has_permissions(ban_members=True)
    @cooldown(cl=5, tm=15.0, ft=3)
    async def ban(self, interaction: Interaction, user: discord.Member, reason: str = None, duration: str = None):
        """Bans a user from the server."""
        if user == interaction.user:
            return await interaction.followup.send("❌ You cannot ban yourself!", ephemeral=True)
        if user.id == self.bot.user.id:
            return await interaction.followup.send("❌ You cannot ban the bot!", ephemeral=True)
        if user.top_role >= interaction.user.top_role:
            return await interaction.followup.send("❌ You cannot ban a user with a higher or equal role!", ephemeral=True)
        bot_member = interaction.guild.get_member(self.bot.user.id)
        if bot_member is None:
            bot_member = await interaction.guild.fetch_member(self.bot.user.id)
        if user.top_role >= bot_member.top_role:
            return await interaction.followup.send("❌ I cannot ban a user with a higher or equal role than my own!", ephemeral=True)
        if user.guild_permissions.administrator:
            return await interaction.followup.send("❌ You cannot ban an administrator!", ephemeral=True)

        # Default duration is 1 hour
        if duration is None:
            duration = "7d"
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
        expiry_time = int(time.time()) + duration_seconds if duration_seconds > 0 else 0

        try:
            await interaction.guild.ban(user, reason=reason)
            insert_case(
                mod_cursor, interaction.guild.id, user.id, user.name, reason, "ban", interaction.user.id, int(time.time()), expiry=expiry_time
                )
            await interaction.followup.send(
                f"✅ **{user.mention} has been banned for {duration if duration_seconds > 0 else 'permanently'}**", ephemeral=False
                )
        except discord.Forbidden:
                await interaction.followup.send("❌ I do not have permission to ban this user!", ephemeral=True)
        except Exception as e:
                log.error(f"Error banning user: {e}")
                await interaction.followup.send("❌ An error occurred while trying to ban the user.", ephemeral=True)

    # @safe_command(timeout=15.0)
    @app_commands.command(name="unban", description="Unbans a user from the server.")
    @app_commands.describe(user_id="The ID of the user to unban", reason="The reason for the unban")
    @app_commands.checks.has_permissions(ban_members=True)
    @app_commands.checks.bot_has_permissions(ban_members=True)
    @cooldown(cl=5, tm=15.0, ft=3)
    async def unban(self, interaction: Interaction, user_id: int, reason: str = None):
        await interaction.response.defer(ephemeral=False)
        """Unbans a user from the server."""
        if reason is None:
            reason = "No reason provided"

        try:
            user = await interaction.client.fetch_user(user_id)
            await interaction.guild.unban(user, reason=reason)
            insert_case(mod_cursor, interaction.guild.id, user.id, user.name, reason, "unban", interaction.user.id, int(time.time()))
            await interaction.followup.send(f"✅ **{user.mention} has been unbanned**", ephemeral=False)

        except discord.NotFound:
            await interaction.followup.send("❌ User not found or not banned.", ephemeral=True)
        except discord.Forbidden:
            await interaction.followup.send("❌ I do not have permission to unban this user!", ephemeral=True)
        except Exception as e:
            log.error(f"Error unbanning user: {e}")
            await interaction.followup.send("❌ An error occurred while trying to unban the user.", ephemeral=True)

    # @safe_command(timeout=15.0)
    @app_commands.command(name="warn", description="Warns a user.")
    @app_commands.describe(user="The user to warn", reason="The reason for the warning")
    @app_commands.checks.has_permissions(moderate_members=True)
    @app_commands.checks.bot_has_permissions(moderate_members=True)
    @cooldown(cl=5, tm=15.0, ft=3)
    async def warn(self, interaction: Interaction, user: discord.Member, reason: str = None):
        await interaction.response.defer(ephemeral=False)
        """Warns a user."""
        if user == interaction.user:
            return await interaction.followup.send("❌ You cannot warn yourself!", ephemeral=True)
        if user.id == self.bot.user.id:
            return await interaction.followup.send("❌ You cannot warn the bot!", ephemeral=True)
        if user.top_role >= interaction.user.top_role:
            return await interaction.followup.send("❌ You cannot warn a user with a higher or equal role!", ephemeral=True)

        # FIX: get bot as a Member, not ClientUser
        bot_member = interaction.guild.get_member(self.bot.user.id)
        if bot_member is None:
            bot_member = await interaction.guild.fetch_member(self.bot.user.id)
        if user.top_role >= bot_member.top_role:
            return await interaction.followup.send("❌ I cannot warn a user with a higher or equal role than my own!", ephemeral=True)
        if user.guild_permissions.administrator:
            return await interaction.followup.send("❌ You cannot warn an administrator!", ephemeral=True)

        if reason is None:
            reason = "No reason provided"

        try:
            insert_case(mod_cursor, interaction.guild.id, user.id, user.name, reason, "warn", interaction.user.id, int(time.time()))
            await interaction.followup.send(f"✅ **{user.mention} has been warned**\n**Reason:** {reason}", ephemeral=False)
        
        except Exception as e:
            log.error(f"Error warning user: {e}")
            await interaction.followup.send("❌ An error occurred while trying to warn the user.", ephemeral=True)

    # @safe_command(timeout=15.0)
    @app_commands.command(name="cases", description="View all cases for the server with pagination.")
    @app_commands.checks.has_permissions(view_audit_log=True)
    @app_commands.checks.bot_has_permissions(view_audit_log=True)
    @cooldown(cl=10, tm=20.0, ft=3)
    async def cases(self, interaction: Interaction):
        await interaction.response.defer(ephemeral=False)
        """View all cases for the server with pagination."""
        cases = get_cases_for_guild(mod_cursor, interaction.guild.id)
        if not cases:
            return await interaction.followup.send("No cases found for this server.", ephemeral=True)

        CASES_PER_PAGE = 10

        def get_page(page):
            embed = discord.Embed(
                title=f"Cases for {interaction.guild.name} (Page {page+1}/{(len(cases)-1)//CASES_PER_PAGE+1})",
                color=discord.Color.blue()
            )
            start = page * CASES_PER_PAGE
            end = start + CASES_PER_PAGE
            for case in cases[start:end]:
                embed.add_field(
                    name=f"Case #{case[0]}",
                    value=f"User: <@{case[1]}> ({case[2]})\n"
                          f"Type: {case[4]}\n"
                          f"Reason: {case[3]}\n",
                    inline=False
                )
            return embed

        class CasePaginator(discord.ui.View):
            def __init__(self):
                super().__init__(timeout=120)
                self.page = 0
                self.max_page = (len(cases) - 1) // CASES_PER_PAGE

            @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary, disabled=True)
            async def previous(self, interaction_btn: discord.Interaction, button: discord.ui.Button):
                self.page -= 1
                if self.page == 0:
                    self.previous.disabled = True
                self.next.disabled = False
                await interaction_btn.response.edit_message(embed=get_page(self.page), view=self)

            @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary, disabled=(len(cases) <= CASES_PER_PAGE))
            async def next(self, interaction_btn: discord.Interaction, button: discord.ui.Button):
                self.page += 1
                if self.page == self.max_page:
                    self.next.disabled = True
                self.previous.disabled = False
                await interaction_btn.response.edit_message(embed=get_page(self.page), view=self)

            async def on_timeout(self):
                for item in self.children:
                    item.disabled = True
                # Try to edit the message to disable buttons
                try:
                    await self.message.edit(view=self)
                except Exception:
                    pass

        view = CasePaginator()
        try:
            msg = await interaction.followup.send(embed=get_page(0), view=view, ephemeral=False)
        except discord.HTTPException as e:
            log.error(f"Failed to send cases message: {e}")
            return await interaction.followup.send("❌ An error occurred while trying to display cases.", ephemeral=True)
        view.message = msg

    # @safe_command(timeout=15.0)
    @app_commands.command(name="case", description="View details of a specific case.")
    @app_commands.describe(case_id="The ID of the case to view")
    @app_commands.checks.has_permissions(view_audit_log=True)
    @app_commands.checks.bot_has_permissions(view_audit_log=True)
    @cooldown(cl=5, tm=15.0, ft=3)
    async def case(self, interaction: Interaction, case_id: int):
        await interaction.response.defer(ephemeral=False)
        """View details of a specific case."""
        case = get_case(mod_cursor, interaction.guild.id, case_id)
        if not case:
            return await interaction.followup.send(f"No case found with ID {case_id}.", ephemeral=True)

        embed = discord.Embed(title=f"Case #{case[0]} Details", color=discord.Color.blue())
        embed.add_field(
            name=f"Case #{case[0]}",
            value=f"User: <@{case[1]}> ({case[2]})\n"
                f"Type: {case[4]}\n"
                f"Reason: {case[3]}\n"
                f"Moderator: <@{case[6]}>\n"
                f"Timestamp: <t:{case[5]}:F>\n"
                f"Expiry: <t:{case[7]}:R>" if case[7] else "",
            inline=False
        )
        try:
            await interaction.followup.send(embed=embed, ephemeral=False)
        except discord.HTTPException as e:
            log.error(f"Failed to send case details message: {e}")
            return await interaction.followup.send("❌ An error occurred while trying to display case details.", ephemeral=True)

    # @safe_command(timeout=15.0)
    @app_commands.command(name="deletecase", description="Delete a specific case.")
    @app_commands.describe(case_id="The ID of the case to delete, Please be sure before continuing.")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.checks.bot_has_permissions(administrator=True)
    @cooldown(cl=5, tm=15.0, ft=3)
    async def delete_case(self, interaction: Interaction, case_id: int):
        await interaction.response.defer(ephemeral=False)
        """Delete a specific case."""
        case = get_case(mod_cursor, interaction.guild.id, case_id)
        if not case:
            return await interaction.followup.send(f"No case found with ID {case_id}.", ephemeral=True)

        try:
            remove_case(mod_cursor, interaction.guild.id, case_id)
            await interaction.followup.send(f"✅ Case #{case_id} has been deleted.", ephemeral=True)
        except discord.Forbidden:
            await interaction.followup.send("❌ I do not have permission to delete this case!", ephemeral=True)
    
    # @safe_command(timeout=15.0)
    @app_commands.command(name="editcase", description="Edit the reason of a specific case.")
    @app_commands.describe(case_id="The ID of the case to edit", reason="The new reason for the case")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.checks.bot_has_permissions(administrator=True)
    @cooldown(cl=5, tm=15.0, ft=3)
    async def edit_case(self, interaction: Interaction, case_id: int, reason: str):
        """Edit the reason of a specific case."""
        case = get_case(mod_cursor, interaction.guild.id, case_id)
        if not case:
            return await interaction.followup.send(f"No case found with ID {case_id}.", ephemeral=True)
        if not reason:
            return await interaction.followup.send("❌ You must provide a new reason for the case.", ephemeral=True)
        
        try:
            edit_case_reason(mod_cursor, interaction.guild.id, case_id, reason)
            await interaction.followup.send(f"✅ Case #{case_id} has been updated with new reason: {reason}", ephemeral=True)
        except discord.Forbidden:
            await interaction.followup.send("❌ I do not have permission to edit this case!", ephemeral=True)


class ModeratorCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
    
    async def cog_load(self):
        self.bot.tree.add_command(ModeratorCommands(self.bot))

async def setup(bot):
    await bot.add_cog(ModeratorCog(bot))