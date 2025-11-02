import discord, asyncio, time, sys, datetime, re
from discord.ext import commands
from discord import app_commands
from discord import Interaction
from database import get_cases_for_guild, get_case, insert_case, remove_case, edit_case_reason, mod_cursor
from config import cooldown
from typing import Optional

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
        await interaction.response.defer(ephemeral=True)

        if user == interaction.user:
            return await interaction.followup.send("❌ You cannot mute yourself!", ephemeral=True)
        if user.id == self.bot.user.id:
            return await interaction.followup.send("❌ You cannot mute the bot!", ephemeral=True)
        if user.top_role >= interaction.user.top_role:
            return await interaction.followup.send("❌ You cannot mute someone with a higher or equal role!", ephemeral=True)
        if user.guild_permissions.administrator:
            return await interaction.followup.send("❌ You cannot mute an administrator!", ephemeral=True)

        # Parse duration
        duration_seconds = 0
        if duration.endswith("d"):
            duration_seconds = int(duration[:-1]) * 86400
        elif duration.endswith("h"):
            duration_seconds = int(duration[:-1]) * 3600
        elif duration.endswith("m"):
            duration_seconds = int(duration[:-1]) * 60
        else:
            return await interaction.followup.send("❌ Invalid duration format! Use `1h`, `30m`, `7d`, etc.", ephemeral=True)

        expiry_time = int(time.time()) + duration_seconds
        until_time = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=duration_seconds)

        if reason is None:
            reason = "No reason provided"

        try:
            until_time = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=duration_seconds)
            await user.timeout(until_time, reason=reason)
            
            insert_case(
                mod_cursor, interaction.guild.id, user.id, user.name,
                reason, "mute", interaction.user.id, int(time.time()), expiry=int(until_time.timestamp())
            )

            await interaction.followup.send(
                f"✅ **{user.mention} has been timed out for {duration}**",
                ephemeral=False
            )

        except discord.Forbidden:
            await interaction.followup.send("❌ I do not have permission to timeout this user!", ephemeral=True)
        except Exception as e:
            log.error(f"Error timing out user: {e}")
            await interaction.followup.send("❌ An error occurred while trying to timeout the user.", ephemeral=True)

    @app_commands.command(name="unmute", description="Removes a mute (timeout) from a user.")
    @app_commands.describe(user="The user to unmute", reason="Reason for unmuting (optional)")
    @app_commands.checks.has_permissions(moderate_members=True)
    @app_commands.checks.bot_has_permissions(moderate_members=True)
    @cooldown(cl=5, tm=15.0, ft=3)
    async def unmute(self, interaction: Interaction, user: discord.Member, reason: str = None):
        await interaction.response.defer(ephemeral=True)

        if not user.is_timed_out():
            return await interaction.followup.send("⚠️ This user is not currently muted.", ephemeral=True)

        try:
            await user.timeout(None, reason=reason or "Manual unmute")
            await interaction.followup.send(f"✅ **{user.mention} has been unmuted.**", ephemeral=False)
        except discord.Forbidden:
            await interaction.followup.send("❌ I don't have permission to unmute that user!", ephemeral=True)
        except Exception as e:
            log.error(f"Error unmuting user: {e}")
            await interaction.followup.send("❌ Something went wrong while unmuting.", ephemeral=True)

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
        await interaction.response.defer(ephemeral=False)

        # --- Permission safety checks ---
        if user == interaction.user:
            return await interaction.followup.send("❌ You can't ban yourself.", ephemeral=True)
        if user.id == self.bot.user.id:
            return await interaction.followup.send("❌ You can't ban the bot.", ephemeral=True)
        if user.guild_permissions.administrator:
            return await interaction.followup.send("❌ You can't ban an administrator.", ephemeral=True)
        if user.top_role >= interaction.user.top_role:
            return await interaction.followup.send("❌ You can't ban someone with equal or higher role.", ephemeral=True)

        bot_member = interaction.guild.me or await interaction.guild.fetch_member(self.bot.user.id)
        if user.top_role >= bot_member.top_role:
            return await interaction.followup.send("❌ That user's role is higher or equal to mine!", ephemeral=True)

        # --- Duration parsing ---
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

        # --- Execute ban ---
        try:
            await interaction.guild.ban(user, reason=reason, delete_message_days=0)
        except discord.Forbidden:
            return await interaction.followup.send("❌ I don't have permission to ban that user.", ephemeral=True)
        except Exception as e:
            log.error(f"[{interaction.guild.name}] Error banning user {user}: {e}", exc_info=True)
            return await interaction.followup.send("❌ An error occurred while trying to ban the user.", ephemeral=True)

        # --- Log case to DB (expiry handled by your main unban task) ---
        insert_case(
            self.mod_cursor,
            interaction.guild.id,
            user.id,
            user.name,
            reason,
            "ban",
            interaction.user.id,
            int(time.time()),
            expiry=expiry
        )

        # --- Confirmation message ---
        duration_text = duration if expiry else "permanently"
        await interaction.followup.send(
            f"✅ **{user.mention} has been banned for {duration_text}.**\nReason: `{reason}`",
            ephemeral=False
        )

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

    # doesnt work for some reason
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

    @app_commands.command(name="purge", description="Deletes messages with optional filters.")
    @app_commands.describe(
        user="Only delete messages from this user (optional)",
        limit="How many messages to scan (max 100)",
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
    @cooldown(cl=3, tm=20.0, ft=2)
    async def purge(self, interaction: Interaction, user: Optional[discord.Member] = None, limit: int = 50, type: str = "all", reason: str = None):
        await interaction.response.defer(ephemeral=True)
        if limit > 100:
            limit = 100

        def check(msg: discord.Message):
            if user and msg.author != user:
                return False
            if type == "links" and not ("http" in msg.content or "www." in msg.content):
                return False
            if type == "media" and not msg.attachments:
                return False
            return True

        deleted = await interaction.channel.purge(limit=limit, check=check, reason=reason or "Purge command")

        await interaction.followup.send(
            f"🧹 Deleted **{len(deleted)}** messages{f' from {user.mention}' if user else ''}.", ephemeral=True
        )

    @app_commands.command(name="whois", description="Get detailed information about a user.")
    @app_commands.describe(user="The user to look up (defaults to yourself).")
    @cooldown(cl=3, tm=15.0, ft=2)
    async def whois(self, interaction: discord.Interaction, user: discord.User | discord.Member = None):
        await interaction.response.defer(thinking=True)

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
                embed.set_footer(text="⚠️ YOUNG ACCOUNT DETECTED — Created less than 60 days ago")
            else:
                embed.set_footer(text=f"Requested by {interaction.user.display_name}")

            await interaction.followup.send(embed=embed)

        except Exception as e:
            log.error(f"WHOIS failed for {member}: {e}")
            await interaction.followup.send("❌ Failed to fetch user info. Check logs for details.", ephemeral=True)

class ModeratorCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
    
    async def cog_load(self):
        self.bot.tree.add_command(ModeratorCommands(self.bot))

async def setup(bot):
    await bot.add_cog(ModeratorCog(bot))