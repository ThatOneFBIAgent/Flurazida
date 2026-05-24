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
