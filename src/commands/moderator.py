# Standard Library Imports
import asyncio
import datetime
import re
import sys
import time
from collections import Counter
from typing import Optional

# Third-Party Imports
import discord
from discord.ext import commands
from discord import app_commands, Interaction

# Local Imports
from database import (
    get_cases_for_guild,
    get_cases_for_user,
    get_case,
    insert_case,
    remove_case,
    edit_case_reason,
)
from config import cooldown
from logger import get_logger

log = get_logger()

class ModeratorCommands(app_commands.Group):
    def __init__(self, bot):
        super().__init__(name="moderator", description="Moderation related commands")
        self.bot = bot

    @app_commands.command(name="mute", description="Mutes (timeouts) a user for a set duration.")
    @app_commands.describe(user="The user to mute", duration="Duration (e.g., 1h, 30m, 7d)", reason="Reason for the mute")
    @app_commands.checks.has_permissions(moderate_members=True)
    @app_commands.checks.bot_has_permissions(moderate_members=True)
    @cooldown(cl=5, tm=15.0, ft=3)
    async def mute(self, interaction: Interaction, user: discord.Member, duration: str, reason: str = None):
        log.trace(f"Mute invoked by {interaction.user.id} on {user.id} for {duration}")
        await interaction.response.defer(ephemeral=True)

        if not interaction.guild:
            log.warningtrace(f"Mute used outside guild by {interaction.user.id}")
            return await interaction.followup.send("❌ This command must be used in a guild.", ephemeral=True)

        if user == interaction.user:
            log.warningtrace(f"Mute self-target by {interaction.user.id}")
            return await interaction.followup.send("❌ You cannot mute yourself!", ephemeral=True)
        if user.id == self.bot.user.id:
            log.warningtrace(f"Mute bot-target by {interaction.user.id}")
            return await interaction.followup.send("❌ You cannot mute the bot!", ephemeral=True)
        if not interaction.user.top_role or user.top_role >= interaction.user.top_role:
            log.warningtrace(f"Mute hierarchy check failed for {interaction.user.id} on {user.id}")
            return await interaction.followup.send("❌ You cannot mute someone with a higher or equal role!", ephemeral=True)
        if user.guild_permissions.administrator:
            log.warningtrace(f"Mute admin-target by {interaction.user.id} on {user.id}")
            return await interaction.followup.send("❌ You cannot mute an administrator!", ephemeral=True)

        # Parse duration
        duration_seconds = 0
        try:
            if duration.endswith("d"):
                duration_seconds = int(duration[:-1]) * 86400
            elif duration.endswith("h"):
                duration_seconds = int(duration[:-1]) * 3600
            elif duration.endswith("m"):
                duration_seconds = int(duration[:-1]) * 60
            elif duration.endswith("s"):
                duration_seconds = int(duration[:-1])
            else:
                log.warningtrace(f"Mute invalid duration format by {interaction.user.id}: {duration}")
                return await interaction.followup.send("❌ Invalid duration format! Use `1h`, `30m`, `7d`, `45s`, etc.", ephemeral=True)
            
            if duration_seconds <= 0:
                log.warningtrace(f"Mute invalid duration (<=0) by {interaction.user.id}: {duration}")
                return await interaction.followup.send("❌ Duration must be greater than 0!", ephemeral=True)
            if duration_seconds > 2419200:
                log.warningtrace(f"Mute duration too long by {interaction.user.id}: {duration}")
                return await interaction.followup.send("❌ Duration cannot exceed 28 days (Discord's maximum timeout limit)!", ephemeral=True)
        except ValueError:
            log.warningtrace(f"Mute invalid duration format by {interaction.user.id}: {duration}")
            return await interaction.followup.send("❌ Invalid duration format! Use `1h`, `30m`, `7d`, etc.", ephemeral=True)

        expiry_time = int(time.time()) + duration_seconds
        until_time = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=duration_seconds)

        if reason is None:
            reason = "No reason provided"

        try:
            await user.timeout(until_time, reason=reason)
            
            try:
                await insert_case(
                    interaction.guild.id, user.id, user.name,
                    reason, "mute", interaction.user.id, int(time.time()), expiry=int(until_time.timestamp())
                )
            except Exception as db_error:
                log.error(f"Failed to log mute case to database: {db_error}", exc_info=True)
                # Still send success message since the mute succeeded

            log.successtrace(f"Mute successful: {user.id} for {duration} by {interaction.user.id}")
            await interaction.followup.send(
                f"✅ **{user.mention} has been timed out for {duration}**",
                ephemeral=False
            )

        except discord.Forbidden:
            log.warningtrace(f"Mute permission denied for {interaction.user.id} on {user.id}")
            await interaction.followup.send("❌ I do not have permission to timeout this user!", ephemeral=True)
        except Exception as e:
            log.error(f"Error timing out user {user.id} in guild {interaction.guild.id}: {e}", exc_info=True)
            await interaction.followup.send("❌ An error occurred while trying to timeout the user.", ephemeral=True)

    @app_commands.command(name="unmute", description="Removes a mute (timeout) from a user.")
    @app_commands.describe(user="The user to unmute", reason="Reason for unmuting (optional)")
    @app_commands.checks.has_permissions(moderate_members=True)
    @app_commands.checks.bot_has_permissions(moderate_members=True)
    @cooldown(cl=5, tm=15.0, ft=3)
    async def unmute(self, interaction: Interaction, user: discord.Member, reason: str = None):
        log.trace(f"Unmute invoked by {interaction.user.id} on {user.id}")
        await interaction.response.defer(ephemeral=True)

        if not interaction.guild:
            log.warningtrace(f"Unmute used outside guild by {interaction.user.id}")
            return await interaction.followup.send("❌ This command must be used in a guild.", ephemeral=True)

        if not user.is_timed_out():
            log.warningtrace(f"Unmute failed (not muted) for {user.id} by {interaction.user.id}")
            return await interaction.followup.send("⚠️ This user is not currently muted.", ephemeral=True)

        try:
            await user.timeout(None, reason=reason or "Manual unmute")
            log.successtrace(f"Unmute successful: {user.id} by {interaction.user.id}")
            await interaction.followup.send(f"✅ **{user.mention} has been unmuted.**", ephemeral=False)
        except discord.Forbidden:
            log.warningtrace(f"Unmute permission denied for {interaction.user.id} on {user.id}")
            await interaction.followup.send("❌ I don't have permission to unmute that user!", ephemeral=True)
        except Exception as e:
            log.error(f"Error unmuting user {user.id} in guild {interaction.guild.id}: {e}", exc_info=True)
            await interaction.followup.send("❌ Something went wrong while unmuting.", ephemeral=True)

    @app_commands.command(name="kick", description="Kicks a user from the server.")
    @app_commands.describe(user="The user to kick", reason="The reason for the kick")
    @app_commands.checks.has_permissions(kick_members=True)
    @app_commands.checks.bot_has_permissions(kick_members=True)
    @cooldown(cl=5, tm=15.0, ft=3)
    async def kick(self, interaction: Interaction, user: discord.Member, reason: str = None):
        log.trace(f"Kick invoked by {interaction.user.id} on {user.id}")
        await interaction.response.defer(ephemeral=False)
        
        if not interaction.guild:
            log.warningtrace(f"Kick used outside guild by {interaction.user.id}")
            return await interaction.followup.send("❌ This command must be used in a guild.", ephemeral=True)
        
        if user == interaction.user:
            log.warningtrace(f"Kick self-target by {interaction.user.id}")
            return await interaction.followup.send("❌ You cannot kick yourself!", ephemeral=True)
        if user.id == self.bot.user.id:
            log.warningtrace(f"Kick bot-target by {interaction.user.id}")
            return await interaction.followup.send("❌ You cannot kick the bot!", ephemeral=True)
        if not interaction.user.top_role or user.top_role >= interaction.user.top_role:
            log.warningtrace(f"Kick hierarchy check failed for {interaction.user.id} on {user.id}")
            return await interaction.followup.send("❌ You cannot kick a user with a higher or equal role!", ephemeral=True)
        
        bot_member = interaction.guild.get_member(self.bot.user.id)
        if bot_member is None:
            bot_member = await interaction.guild.fetch_member(self.bot.user.id)
        if not bot_member.top_role or user.top_role >= bot_member.top_role:
            log.warningtrace(f"Kick bot hierarchy check failed for {interaction.user.id} on {user.id}")
            return await interaction.followup.send("❌ I cannot kick a user with a higher or equal role than my own!", ephemeral=True)
        
        if user.guild_permissions.administrator:
            log.warningtrace(f"Kick admin-target by {interaction.user.id} on {user.id}")
            return await interaction.followup.send("❌ You cannot kick an administrator!", ephemeral=True)

        if reason is None:
            reason = "No reason provided"

        try:
            await user.kick(reason=reason)
            try:
                await insert_case(interaction.guild.id, user.id, user.name, reason, "kick", interaction.user.id, int(time.time()))
            except Exception as db_error:
                log.error(f"Failed to log kick case to database: {db_error}", exc_info=True)
            
            log.successtrace(f"Kick successful: {user.id} by {interaction.user.id}")
            await interaction.followup.send(f"✅ **{user.mention} has been kicked**", ephemeral=False)
        except discord.Forbidden:
            log.warningtrace(f"Kick permission denied for {interaction.user.id} on {user.id}")
            await interaction.followup.send("❌ I do not have permission to kick this user!", ephemeral=True)
        except Exception as e:
            log.error(f"Error kicking user {user.id} in guild {interaction.guild.id}: {e}", exc_info=True)
            await interaction.followup.send("❌ An error occurred while trying to kick the user.", ephemeral=True)

    @app_commands.command(name="ban", description="Bans a user from the server (optionally timed).")
    @app_commands.describe(
        user="User to ban",
        duration="Ban duration (e.g. 1d, 12h, 30m). Default is 7 days.",
        reason="Reason for the ban"
    )
    @app_commands.checks.has_permissions(ban_members=True)
    @app_commands.checks.bot_has_permissions(ban_members=True)
    @cooldown(cl=5, tm=15.0, ft=3)
    async def ban(
        self,
        interaction: Interaction,
        user: discord.Member,
        duration: str = "7d",
        reason: str = "No reason provided"
    ):
        log.trace(f"Ban invoked by {interaction.user.id} on {user.id} for {duration}")
        await interaction.response.defer(ephemeral=False)

        if not interaction.guild:
            log.warningtrace(f"Ban used outside guild by {interaction.user.id}")
            return await interaction.followup.send("❌ This command must be used in a guild.", ephemeral=True)

        # Permission safety checks
        if user == interaction.user:
            return await interaction.followup.send("❌ You can't ban yourself.", ephemeral=True)
        if user.id == self.bot.user.id:
            return await interaction.followup.send("❌ You can't ban the bot.", ephemeral=True)
        if user.guild_permissions.administrator:
            return await interaction.followup.send("❌ You can't ban an administrator.", ephemeral=True)
        if not interaction.user.top_role or user.top_role >= interaction.user.top_role:
            return await interaction.followup.send("❌ You can't ban someone with equal or higher role.", ephemeral=True)

        bot_member = interaction.guild.me or await interaction.guild.fetch_member(self.bot.user.id)
        if not bot_member.top_role or user.top_role >= bot_member.top_role:
            return await interaction.followup.send("❌ That user's role is higher or equal to mine!", ephemeral=True)

        # Duration parsing
        total_seconds = 0
        matches = re.findall(r"(\d+)([dhm])", duration)
        if not matches:
            return await interaction.followup.send(
                "❌ Invalid duration format. Try something like `3d`, `12h`, or `30m`.",
                ephemeral=True
            )

        for value, unit in matches:
            value = int(value)
            if unit == "d":
                total_seconds += value * 86400
            elif unit == "h":
                total_seconds += value * 3600
            elif unit == "m":
                total_seconds += value * 60

        expiry = int(time.time()) + total_seconds if total_seconds > 0 else 0

        # Execute ban
        try:
            await interaction.guild.ban(user, reason=reason, delete_message_days=0)
        except discord.Forbidden:
            log.warningtrace(f"Ban permission denied for {interaction.user.id} on {user.id}")
            return await interaction.followup.send("❌ I do not have permission to ban this user!", ephemeral=True)
        except Exception as e:
            log.error(f"[{interaction.guild.name}] Error banning user {user.id}: {e}", exc_info=True)
            return await interaction.followup.send("❌ An error occurred while trying to ban the user.", ephemeral=True)

        # Log case to DB (expiry handled by your main unban task)
        try:
            await insert_case(
                interaction.guild.id,
                user.id,
                user.name,
                reason,
                "ban",
                interaction.user.id,
                int(time.time()),
                expiry=expiry
            )
        except Exception as db_error:
            log.error(f"Failed to log ban case to database: {db_error}", exc_info=True)
            # Still send success message since the ban succeeded

        # Confirmation message
        duration_text = duration if expiry else "permanently"
        await interaction.followup.send(
            f"✅ **{user.mention} has been banned for {duration_text}.**\nReason: `{reason}`",
            ephemeral=False
        )

    @app_commands.command(name="unban", description="Unbans a user from the server.")
    @app_commands.describe(user_id="The ID of the user to unban", reason="Reason for unban")
    @app_commands.checks.has_permissions(ban_members=True)
    @app_commands.checks.bot_has_permissions(ban_members=True)
    @cooldown(cl=5, tm=15.0, ft=3)
    async def unban(self, interaction: Interaction, user_id: str, reason: str = None):
        log.trace(f"Unban invoked by {interaction.user.id} on {user_id}")
        await interaction.response.defer(ephemeral=False)

        if not interaction.guild:
            log.warningtrace(f"Unban used outside guild by {interaction.user.id}")
            return await interaction.followup.send("❌ This command must be used in a guild.", ephemeral=True)

        try:
            user_obj = await self.bot.fetch_user(int(user_id))
            await interaction.guild.unban(user_obj, reason=reason or "Manual unban")
            
            log.successtrace(f"Unban successful: {user_id} by {interaction.user.id}")
            await interaction.followup.send(f"✅ **{user_obj.mention} has been unbanned.**", ephemeral=False)
        except discord.NotFound:
            log.warningtrace(f"Unban user not found: {user_id}")
            return await interaction.followup.send("❌ User not found or not banned.", ephemeral=True)
        except ValueError:
             log.warningtrace(f"Unban invalid user ID: {user_id}")
             return await interaction.followup.send("❌ Invalid User ID.", ephemeral=True)
        except discord.Forbidden:
            log.warningtrace(f"Unban permission denied for {interaction.user.id} on {user_id}")
            return await interaction.followup.send("❌ I do not have permission to unban this user!", ephemeral=True)
        except Exception as e:
            log.error(f"Error unbanning user {user_id} in guild {interaction.guild.id}: {e}", exc_info=True)
            await interaction.followup.send("❌ An error occurred while trying to unban the user.", ephemeral=True)

    @app_commands.command(name="warn", description="Warns a user.")
    @app_commands.describe(user="The user to warn", reason="The reason for the warning")
    @app_commands.checks.has_permissions(moderate_members=True)
    @cooldown(cl=5, tm=15.0, ft=3)
    async def warn(self, interaction: Interaction, user: discord.Member, reason: str):
        log.trace(f"Warn invoked by {interaction.user.id} on {user.id}")
        await interaction.response.defer(ephemeral=False)

        if not interaction.guild:
            log.warningtrace(f"Warn used outside guild by {interaction.user.id}")
            return await interaction.followup.send("❌ This command must be used in a guild.", ephemeral=True)

        if user.id == self.bot.user.id:
            log.warningtrace(f"Warn bot-target by {interaction.user.id}")
            return await interaction.followup.send("❌ You cannot warn the bot!", ephemeral=True)
        if not interaction.user.top_role or user.top_role >= interaction.user.top_role:
            return await interaction.followup.send("❌ You cannot warn a user with a higher or equal role!", ephemeral=True)

        bot_member = interaction.guild.get_member(self.bot.user.id)
        if bot_member is None:
            bot_member = await interaction.guild.fetch_member(self.bot.user.id)
        if not bot_member.top_role or user.top_role >= bot_member.top_role:
            return await interaction.followup.send("❌ I cannot warn a user with a higher or equal role than my own!", ephemeral=True)
        if user.guild_permissions.administrator:
            return await interaction.followup.send("❌ You cannot warn an administrator!", ephemeral=True)

        try:
            case_num = await insert_case(interaction.guild.id, user.id, user.name, reason, "warn", interaction.user.id, int(time.time()))
            log.successtrace(f"Warn successful: {user.id} by {interaction.user.id} (Case #{case_num})")
            await interaction.followup.send(f"✅ **{user.mention} has been warned.** (Case #{case_num})", ephemeral=False)
        except Exception as e:
            log.error(f"Error warning user {user.id}: {e}", exc_info=True)
            await interaction.followup.send("❌ An error occurred while logging the warning.", ephemeral=True)

    @app_commands.command(name="cases", description="View all cases for the server with pagination.")
    @app_commands.checks.has_permissions(view_audit_log=True)
    @app_commands.checks.bot_has_permissions(view_audit_log=True)
    @cooldown(cl=10, tm=20.0, ft=3)
    async def cases(self, interaction: Interaction):
        await interaction.response.defer(ephemeral=False)
        log.trace(f"Cases invoked by {interaction.user.id}")
        if not interaction.guild:
            log.warningtrace(f"Cases used outside guild by {interaction.user.id}")
            return await interaction.followup.send("❌ This command must be used in a guild.", ephemeral=True)
        
        try:
            cases = await get_cases_for_guild(interaction.guild.id)
        except Exception as e:
            log.error(f"Error fetching cases for guild {interaction.guild.id}: {e}", exc_info=True)
            return await interaction.followup.send("❌ An error occurred while fetching cases.", ephemeral=True)
        
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

    @app_commands.command(name="case", description="View details of a specific case.")
    @app_commands.describe(case_id="The ID of the case to view")
    @app_commands.checks.has_permissions(view_audit_log=True)
    @app_commands.checks.bot_has_permissions(view_audit_log=True)
    @cooldown(cl=5, tm=15.0, ft=3)
    async def case(self, interaction: Interaction, case_id: int):
        await interaction.response.defer(ephemeral=False)
        log.trace(f"Case invoked by {interaction.user.id}: #{case_id}")
        if not interaction.guild:
            return await interaction.followup.send("❌ This command must be used in a guild.", ephemeral=True)
        
        try:
            case = await get_case(interaction.guild.id, case_id)
        except Exception as e:
            log.error(f"Error fetching case {case_id} for guild {interaction.guild.id}: {e}", exc_info=True)
            return await interaction.followup.send("❌ An error occurred while fetching the case.", ephemeral=True)
        
        if not case:
            return await interaction.followup.send(f"❌ No case found with ID {case_id} in this server.", ephemeral=True)

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

    @app_commands.command(name="deletecase", description="Delete a specific case.")
    @app_commands.describe(case_id="The ID of the case to delete, Please be sure before continuing.")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.checks.bot_has_permissions(administrator=True)
    @cooldown(cl=5, tm=15.0, ft=3)
    async def delete_case(self, interaction: Interaction, case_id: int):
        await interaction.response.defer(ephemeral=True)
        log.trace(f"DeleteCase invoked by {interaction.user.id}: #{case_id}")
        if not interaction.guild:
            return await interaction.followup.send("❌ This command must be used in a guild.", ephemeral=True)
        
        try:
            case = await get_case(interaction.guild.id, case_id)
            if not case:
                return await interaction.followup.send(f"❌ No case found with ID {case_id} in this server.", ephemeral=True)

            await remove_case(interaction.guild.id, case_id)
            await interaction.followup.send(f"✅ Case #{case_id} has been deleted.", ephemeral=True)
        except Exception as e:
            log.error(f"Error deleting case {case_id} in guild {interaction.guild.id}: {e}", exc_info=True)
            await interaction.followup.send("❌ An error occurred while trying to delete the case.", ephemeral=True)

    @app_commands.command(name="edit_case", description="Edit the reason for a specific case.")
    @app_commands.describe(case_number="The case number to edit", new_reason="The new reason")
    @app_commands.checks.has_permissions(moderate_members=True)
    @cooldown(cl=5, tm=15.0, ft=3)
    async def edit_case(self, interaction: Interaction, case_number: int, new_reason: str):
        log.info(f"EditCase invoked by {interaction.user.id}: #{case_number}")
        await interaction.response.defer(ephemeral=True)

        if not interaction.guild:
            log.warningtrace(f"EditCase used outside guild by {interaction.user.id}")
            return await interaction.followup.send("❌ This command must be used in a guild.", ephemeral=True)

        try:
            await edit_case_reason(interaction.guild.id, case_number, new_reason)
            log.successtrace(f"Case edited: #{case_number} by {interaction.user.id}")
            await interaction.followup.send(f"✅ Case #{case_number} updated.", ephemeral=True)
        except Exception as e:
            log.error(f"Error editing case #{case_number}: {e}", exc_info=True)
            await interaction.followup.send("❌ Failed to edit case.", ephemeral=True)

    @app_commands.command(name="purge", description="Deletes messages with optional filters.")
    @app_commands.describe(
        user="Only delete messages from this user (optional)",
        limit="How many messages to scan (max 100, Discord API limit)",
        type="Filter type: all, links, or media",
        reason="Reason for purging (optional)"
    )
    @app_commands.choices(type=[
        app_commands.Choice(name="All messages", value="all"),
        app_commands.Choice(name="Messages containing links", value="links"),
        app_commands.Choice(name="Messages with media", value="media")
    ])
    @app_commands.checks.has_permissions(manage_messages=True)
    @app_commands.checks.bot_has_permissions(manage_messages=True)
    @cooldown(cl=7, tm=20.0, ft=2) # some fucker made the bot get hit with 429s constantly
    async def purge(self, interaction: Interaction, user: Optional[discord.Member] = None, limit: int = 50, type: str = "all", reason: str = None):
        await interaction.response.defer(ephemeral=True)
        log.trace(f"Purge invoked by {interaction.user.id}: {limit} messages, {type} filter, {user}")
        
        if not interaction.guild or not interaction.channel:
            return await interaction.followup.send("❌ This command must be used in a guild channel.", ephemeral=True)
        
        # Discord API limit, do not increases unless you want a nastly worded email.
        if limit > 100:
            limit = 100
        if limit < 1:
            return await interaction.followup.send("❌ Limit must be at least 1.", ephemeral=True)

        def check(msg: discord.Message):
            if user and msg.author != user:
                return False
            if type == "links" and not ("http" in (msg.content or "") or "www." in (msg.content or "")):
                return False
            if type == "media" and not msg.attachments:
                return False
            return True

        try:
            deleted = await interaction.channel.purge(limit=limit, check=check, reason=reason or "Purge command")
            
            if not deleted:
                return await interaction.followup.send("❌ No messages were deleted (none matched the filters or all were too old).", ephemeral=True)
            
            # Count messages by author
            author_counts = Counter()
            for msg in deleted:
                author_name = msg.author.display_name if hasattr(msg.author, 'display_name') else msg.author.name
                author_counts[author_name] += 1
            
            # Build response message
            total = len(deleted)
            response_lines = [f"🧹 Deleted **{total}** message{'s' if total != 1 else ''}"]
            
            # Add breakdown by user (always show if multiple users, or if filtering by specific user)
            # Show breakdown if: multiple authors OR specific user filter (to show their count)
            if len(author_counts) > 1 or user is not None:
                response_lines.append("")  # Empty line separator
                for author_name, count in author_counts.most_common():
                    # Use "Msg." for plural, "Message" for singular
                    msg_text = "Msg." if count != 1 else "Message"
                    response_lines.append(f"**{author_name}**: {count} {msg_text}")
            
            await interaction.followup.send("\n".join(response_lines), ephemeral=True)
            
        except discord.Forbidden:
            await interaction.followup.send("❌ I don't have permission to delete messages in this channel!", ephemeral=True)
        except Exception as e:
            log.error(f"Error purging messages in channel {interaction.channel.id}: {e}", exc_info=True)
            await interaction.followup.send("❌ An error occurred while trying to purge messages.", ephemeral=True)

    @app_commands.command(name="whois", description="Get detailed information about a user.")
    @app_commands.describe(user="The user to look up (defaults to yourself).")
    @cooldown(cl=3, tm=15.0, ft=2)
    async def whois(self, interaction: discord.Interaction, user: discord.User | discord.Member = None):
        await interaction.response.defer(thinking=True)

        if not interaction.guild:
            return await interaction.followup.send("❌ This command must be used in a guild.", ephemeral=True)

        member = user or interaction.user
        if isinstance(member, discord.User):
            member = interaction.guild.get_member(member.id) or member

        try:
            created_utc = getattr(member, "created_at", None)
            joined_utc = getattr(member, "joined_at", None)
            if created_utc:
                created_utc = created_utc.replace(tzinfo=datetime.timezone.utc)
            if joined_utc:
                joined_utc = joined_utc.replace(tzinfo=datetime.timezone.utc)

            age_days = (datetime.datetime.now(datetime.timezone.utc) - created_utc).days if created_utc else 0
            young_account = age_days < 60

            color = member.color if hasattr(member, "color") and getattr(member.color, "value", 0) != 0 else 0x5865F2
            embed = discord.Embed(
                title=f"🕵️ User Info — {getattr(member, 'display_name', member.name)}",
                color=color
            )

            embed.add_field(name="Display Name", value=getattr(member, "display_name", member.name), inline=True)
            embed.add_field(name="Username", value=member.name, inline=True)
            embed.add_field(name="User ID", value=f"`{member.id}`", inline=True)

            if created_utc:
                embed.add_field(name="Account Created", value=created_utc.strftime("%d/%m/%Y at %I:%M:%S %p UTC"), inline=False)
            if joined_utc:
                embed.add_field(name="Joined Server", value=joined_utc.strftime("%d/%m/%Y at %I:%M:%S %p UTC"), inline=False)

            if getattr(member, "communication_disabled_until", None):
                until_time = member.communication_disabled_until
                embed.add_field(name="⏳ Timed Out Until", value=until_time.strftime("%d/%m/%Y at %I:%M:%S %p UTC"), inline=False)

            if getattr(member, "nick", None):
                embed.add_field(name="Nickname", value=member.nick, inline=False)
            
            if isinstance(member, discord.Member):
                perms = member.guild_permissions
                notable = {
                    "Administrator": perms.administrator,
                    "Kick Members": perms.kick_members,
                    "Ban Members": perms.ban_members,
                    "Manage Channels": perms.manage_channels,
                    "Manage Guild": perms.manage_guild,
                    "Manage Messages": perms.manage_messages,
                    "Manage Roles": perms.manage_roles,
                    "Manage Webhooks": perms.manage_webhooks,
                    "Manage Emojis": perms.manage_emojis,
                    "Manage Nicknames": perms.manage_nicknames,
                    "View Audit Log": perms.view_audit_log,
                    "View Server Insights": perms.view_server_insights,
                }
                perm_list = [name for name, val in notable.items() if val]
                embed.add_field(name="Key Permissions", value=", ".join(perm_list) if perm_list else "None", inline=False)

            avatar_url = member.avatar.url if member.avatar else member.default_avatar.url
            banner_url = None
            try:
                user_obj = await interaction.client.fetch_user(member.id)
                if user_obj.banner:
                    banner_url = user_obj.banner.url
            except Exception as e:
                log.warning(f"Failed to fetch banner for {member.id}: {e}")

            embed.set_thumbnail(url=avatar_url)
            links = f"[Avatar]({avatar_url})" + (f" | [Banner]({banner_url})" if banner_url else "")
            embed.add_field(name="Links", value=links, inline=False)

            marker = "🤖 Bot Account" if member.bot else "🧍 Human Account"
            embed.add_field(name="Account Type", value=marker, inline=False)

            if young_account:
                embed.set_footer(text="⚠️ YOUNG ACCOUNT DETECTED — Registered less than 60 days ago")
            else:
                embed.set_footer(text=f"Requested by {interaction.user.display_name}")

            log.successtrace(f"Whois successful for {interaction.user.id}")
            await interaction.followup.send(embed=embed)

        except Exception as e:
            log.error(f"WHOIS failed for {member}: {e}", exc_info=True)
            await interaction.followup.send("❌ Failed to fetch user info. Check logs for details.", ephemeral=True)

class ModeratorCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
    
    async def cog_load(self):
        self.bot.tree.add_command(ModeratorCommands(self.bot))

async def setup(bot):
    await bot.add_cog(ModeratorCog(bot))