# tests/test_prefix_commands.py
# Pytest suite for Flurazide's dynamic prefix command system.

import asyncio
import discord
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

# Add project root to path so imports work
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Mock status module to prevent loading PEM keys in test environment
from unittest.mock import MagicMock
sys.modules["status"] = MagicMock()

from main import (
    FakeInteraction,
    FakeResponse,
    FakeFollowup,
    get_command_usage,
    convert_user,
    bot,
    handle_prefix_command,
)

class TestPrefixSystem:
    def test_get_command_usage_simple(self):
        """Verify command usage generator constructs parameter formatting correctly."""
        cmd = MagicMock()
        cmd.qualified_name = "purge"
        
        param_limit = MagicMock()
        param_limit.name = "limit"
        param_limit.required = True
        param_limit.choices = []
        
        param_type = MagicMock()
        param_type.name = "type"
        param_type.required = False
        param_type.choices = [
            MagicMock(value="user"),
            MagicMock(value="media"),
            MagicMock(value="links")
        ]
        
        cmd.parameters = [param_limit, param_type]
        
        usage = get_command_usage(cmd)
        assert usage == "f!purge (limit) [user|media|links]"

    @pytest.mark.asyncio
    async def test_convert_user_mention(self):
        """Verify convert_user resolves member mentions correctly."""
        guild = MagicMock()
        member = MagicMock()
        guild.get_member.return_value = member
        
        # Mentions like <@123456>
        res = await convert_user("<@123456>", guild)
        assert res == member
        guild.get_member.assert_called_with(123456)

    @pytest.mark.asyncio
    async def test_convert_user_username(self):
        """Verify convert_user falls back to searching members list by name."""
        guild = MagicMock()
        member_a = MagicMock(nick=None)
        member_a.name = "Alice"
        member_b = MagicMock(nick="BobNick")
        member_b.name = "Bob"
        
        guild.members = [member_a, member_b]
        guild.get_member.return_value = None
        
        res = await convert_user("alice", guild)
        assert res == member_a
        
        res = await convert_user("bobnick", guild)
        assert res == member_b

    @pytest.mark.asyncio
    async def test_fake_interaction_response(self):
        """Verify FakeInteraction maps send_message to channel send calls correctly."""
        channel = AsyncMock()
        message = MagicMock()
        message.channel = channel
        message.author = MagicMock()
        message.guild = MagicMock()
        
        client = MagicMock()
        
        interaction = FakeInteraction(message, client)
        
        # Test defer
        resp_msg = MagicMock()
        resp_msg.content = "⏳ *Thinking...*"
        resp_msg.edit = AsyncMock()
        channel.send.return_value = resp_msg

        await interaction.response.defer()
        assert getattr(interaction, "_deferred_thinking", False) is True
        channel.send.assert_not_called()

        # Test subsequent send_message should create the real response now.
        channel.send.return_value = resp_msg
        await interaction.response.send_message("Testing send")
        channel.send.assert_called_with(content="Testing send", embed=None, embeds=None, view=None)
        assert interaction._deferred_thinking is False

    @pytest.mark.asyncio
    async def test_fake_interaction_followup_after_defer(self):
        """Verify followup sends are handled when a prefix interaction was deferred."""
        channel = AsyncMock()
        message = MagicMock()
        message.channel = channel
        message.author = MagicMock()
        message.guild = MagicMock()

        client = MagicMock()
        interaction = FakeInteraction(message, client)

        await interaction.response.defer()
        assert getattr(interaction, "_deferred_thinking", False) is True

        followup_msg = MagicMock()
        channel.send.return_value = followup_msg

        await interaction.followup.send("Followup content")
        channel.send.assert_called_with(content="Followup content", embed=None, embeds=None, view=None)
        assert interaction._deferred_thinking is False


class MockParameter:
    def __init__(self, name, opt_type, required=True, default=None, choices=None):
        self.name = name
        self.display_name = name
        self.type = opt_type
        self.required = required
        self.default = default
        self.choices = choices or []
        self.description = "Test param description"


class MockCommand:
    def __init__(self, name, parameters):
        self.name = name
        self.qualified_name = name
        self.description = "Test description"
        self.parameters = parameters
        self._invoke_with_namespace = AsyncMock()
        self._invoke_error_handlers = AsyncMock(return_value=False)


class TestPrefixCommandExecution:
    @pytest.mark.asyncio
    @patch("main.global_blacklist_check", return_value=True)
    @patch("main.convert_user")
    async def test_handle_prefix_command_give(self, mock_convert_user, mock_blacklist):
        """Test parsing of f!give @guy "Ultra Rare Fish" 3"""
        mock_member = MagicMock()
        mock_convert_user.return_value = mock_member

        param_user = MockParameter("user", discord.AppCommandOptionType.user, required=True)
        param_item = MockParameter("item", discord.AppCommandOptionType.string, required=True)
        param_amount = MockParameter("amount", discord.AppCommandOptionType.integer, required=True)
        mock_cmd = MockCommand("give", [param_user, param_item, param_amount])

        with patch.object(bot.tree, "get_commands", return_value=[mock_cmd]):
            message = AsyncMock()
            message.content = 'f!give @guy "Ultra Rare Fish" 3'
            message.guild = MagicMock()
            message.attachments = []

            await handle_prefix_command(message)

            mock_cmd._invoke_with_namespace.assert_called_once()
            called_interaction, called_namespace = mock_cmd._invoke_with_namespace.call_args[0]
            assert called_interaction.command == mock_cmd
            assert called_namespace.user == mock_member
            assert called_namespace.item == "Ultra Rare Fish"
            assert called_namespace.amount == 3

    @pytest.mark.asyncio
    @patch("main.global_blacklist_check", return_value=True)
    async def test_handle_prefix_command_multiple_strings(self, mock_blacklist):
        """Test parsing of f!dosomething "person b" "person c" """
        param_p1 = MockParameter("p1", discord.AppCommandOptionType.string, required=True)
        param_p2 = MockParameter("p2", discord.AppCommandOptionType.string, required=True)
        mock_cmd = MockCommand("dosomething", [param_p1, param_p2])

        with patch.object(bot.tree, "get_commands", return_value=[mock_cmd]):
            message = AsyncMock()
            message.content = 'f!dosomething "person b" "person c"'
            message.guild = MagicMock()
            message.attachments = []

            await handle_prefix_command(message)

            mock_cmd._invoke_with_namespace.assert_called_once()
            called_interaction, called_namespace = mock_cmd._invoke_with_namespace.call_args[0]
            assert called_namespace.p1 == "person b"
            assert called_namespace.p2 == "person c"

    @pytest.mark.asyncio
    @patch("main.global_blacklist_check", return_value=True)
    @patch("main.convert_user")
    async def test_handle_prefix_command_mismatched_quotes(self, mock_convert_user, mock_blacklist):
        """Test fallback split behavior when quotes are mismatched: f!ban @guy being "annoying in vc"""
        mock_member = MagicMock()
        mock_convert_user.return_value = mock_member

        param_user = MockParameter("user", discord.AppCommandOptionType.user, required=True)
        param_reason = MockParameter("reason", discord.AppCommandOptionType.string, required=False)
        mock_cmd = MockCommand("ban", [param_user, param_reason])

        with patch.object(bot.tree, "get_commands", return_value=[mock_cmd]):
            message = AsyncMock()
            message.content = 'f!ban @guy being "annoying in vc'
            message.guild = MagicMock()
            message.attachments = []

            await handle_prefix_command(message)

            mock_cmd._invoke_with_namespace.assert_called_once()
            called_interaction, called_namespace = mock_cmd._invoke_with_namespace.call_args[0]
            assert called_namespace.user == mock_member
            assert called_namespace.reason == 'being "annoying in vc'

    @pytest.mark.asyncio
    @patch("main.global_blacklist_check", return_value=True)
    @patch("main.convert_user")
    async def test_handle_prefix_command_validation_failure(self, mock_convert_user, mock_blacklist):
        """Test failure when argument cannot be converted to expected type (integer validation failure)"""
        mock_member = MagicMock()
        mock_convert_user.return_value = mock_member

        param_user = MockParameter("user", discord.AppCommandOptionType.user, required=True)
        param_item = MockParameter("item", discord.AppCommandOptionType.string, required=True)
        param_amount = MockParameter("amount", discord.AppCommandOptionType.integer, required=True)
        mock_cmd = MockCommand("give", [param_user, param_item, param_amount])

        with patch.object(bot.tree, "get_commands", return_value=[mock_cmd]):
            message = AsyncMock()
            message.content = 'f!give @guy "Fish" abc'
            message.guild = MagicMock()
            message.attachments = []

            await handle_prefix_command(message)

            # Command should not be invoked due to invalid parameter error
            mock_cmd._invoke_with_namespace.assert_not_called()
            # It should send an error message about expecting an integer
            message.channel.send.assert_called_once()
            embed = message.channel.send.call_args[1].get("embed")
            assert embed is not None
            assert "expects an integer" in embed.description

    @pytest.mark.asyncio
    @patch("main.global_blacklist_check", return_value=True)
    async def test_handle_prefix_command_missing_required(self, mock_blacklist):
        """Test validation error when a required parameter is missing"""
        param_p1 = MockParameter("p1", discord.AppCommandOptionType.string, required=True)
        param_p2 = MockParameter("p2", discord.AppCommandOptionType.string, required=True)
        mock_cmd = MockCommand("dosomething", [param_p1, param_p2])

        with patch.object(bot.tree, "get_commands", return_value=[mock_cmd]):
            message = AsyncMock()
            message.content = 'f!dosomething "only_one_arg"'
            message.guild = MagicMock()
            message.attachments = []

            await handle_prefix_command(message)

            mock_cmd._invoke_with_namespace.assert_not_called()
            message.channel.send.assert_called_once()
            embed = message.channel.send.call_args[1].get("embed")
            assert embed is not None
            assert "Missing Required Fields" in embed.title

    @pytest.mark.asyncio
    @patch("main.global_blacklist_check", return_value=True)
    async def test_handle_prefix_command_command_error(self, mock_blacklist):
        """Test error propagation when a command check or invoke error happens"""
        param_p1 = MockParameter("p1", discord.AppCommandOptionType.string, required=True)
        mock_cmd = MockCommand("errorcmd", [param_p1])
        
        # Make _invoke_with_namespace raise AppCommandError
        error = discord.app_commands.AppCommandError("Check failed")
        mock_cmd._invoke_with_namespace.side_effect = error

        from unittest.mock import ANY
        with patch.object(bot.tree, "get_commands", return_value=[mock_cmd]):
            with patch.object(bot.tree, "on_error", new_callable=AsyncMock) as mock_on_error:
                message = AsyncMock()
                message.content = 'f!errorcmd value'
                message.guild = MagicMock()
                message.attachments = []

                await handle_prefix_command(message)

                mock_cmd._invoke_with_namespace.assert_called_once()
                mock_cmd._invoke_error_handlers.assert_called_once_with(ANY, error)
                mock_on_error.assert_called_once_with(ANY, error)


class MockAppCommand(discord.app_commands.Command):
    def __init__(self, name, parameters, qualified_name=None):
        super().__init__(name=name, description="mock", callback=lambda x: None)
        self._mock_parameters = parameters
        self._mock_qualified_name = qualified_name or name
        self._invoke_with_namespace = AsyncMock()
        self._invoke_error_handlers = AsyncMock(return_value=False)

    @property
    def parameters(self):
        return self._mock_parameters

    @property
    def qualified_name(self):
        return self._mock_qualified_name


class MockAppGroup(discord.app_commands.Group):
    def __init__(self, name, commands):
        super().__init__(name=name)
        self._mock_commands = commands

    @property
    def commands(self):
        return self._mock_commands


class TestShopPrefixExclusions:
    @pytest.mark.asyncio
    @patch("main.global_blacklist_check", return_value=True)
    async def test_shop_view_no_subcommand(self, mock_blacklist):
        """f!shop should run shop view command"""
        mock_view_cmd = MockAppCommand("view", [], qualified_name="shop view")
        mock_buy_cmd = MockAppCommand("buy", [MockParameter("item", discord.AppCommandOptionType.string)], qualified_name="shop buy")
        mock_shop_group = MockAppGroup("shop", [mock_view_cmd, mock_buy_cmd])

        with patch.object(bot.tree, "get_commands", return_value=[mock_shop_group]):
            message = AsyncMock()
            message.content = "f!shop"
            message.guild = MagicMock()
            message.attachments = []

            await handle_prefix_command(message)

            mock_view_cmd._invoke_with_namespace.assert_called_once()
            mock_buy_cmd._invoke_with_namespace.assert_not_called()

    @pytest.mark.asyncio
    @patch("main.global_blacklist_check", return_value=True)
    async def test_shop_buy_subcommand(self, mock_blacklist):
        """f!shop buy 'padlocked wallet' should run shop buy command"""
        mock_view_cmd = MockAppCommand("view", [], qualified_name="shop view")
        mock_buy_cmd = MockAppCommand("buy", [
            MockParameter("item_name", discord.AppCommandOptionType.string, required=True),
            MockParameter("quantity", discord.AppCommandOptionType.integer, required=False)
        ], qualified_name="shop buy")
        mock_shop_group = MockAppGroup("shop", [mock_view_cmd, mock_buy_cmd])

        with patch.object(bot.tree, "get_commands", return_value=[mock_shop_group]):
            message = AsyncMock()
            message.content = 'f!shop buy "padlocked wallet" 10'
            message.guild = MagicMock()
            message.attachments = []

            await handle_prefix_command(message)

            mock_buy_cmd._invoke_with_namespace.assert_called_once()
            called_interaction, called_namespace = mock_buy_cmd._invoke_with_namespace.call_args[0]
            assert called_namespace.item_name == "padlocked wallet"
            assert called_namespace.quantity == 10
            mock_view_cmd._invoke_with_namespace.assert_not_called()

    @pytest.mark.asyncio
    @patch("main.global_blacklist_check", return_value=True)
    async def test_shop_subcommands_fallback_exclusions(self, mock_blacklist):
        """f!buy and f!view should be invalid (not fall back to shop subcommands)"""
        mock_view_cmd = MockAppCommand("view", [], qualified_name="shop view")
        mock_buy_cmd = MockAppCommand("buy", [MockParameter("item", discord.AppCommandOptionType.string)], qualified_name="shop buy")
        mock_shop_group = MockAppGroup("shop", [mock_view_cmd, mock_buy_cmd])

        with patch.object(bot.tree, "get_commands", return_value=[mock_shop_group]):
            # Test f!buy
            message_buy = AsyncMock()
            message_buy.content = "f!buy wallet"
            message_buy.guild = MagicMock()
            message_buy.attachments = []

            await handle_prefix_command(message_buy)
            mock_buy_cmd._invoke_with_namespace.assert_not_called()

            # Test f!view
            message_view = AsyncMock()
            message_view.content = "f!view"
            message_view.guild = MagicMock()
            message_view.attachments = []

            await handle_prefix_command(message_view)
            mock_view_cmd._invoke_with_namespace.assert_not_called()

