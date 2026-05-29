# tests/test_moderator.py
# Pytest suite for moderator.py helpers: parse_duration and check_moderation_target.

import sys
import os
import pytest
from unittest.mock import AsyncMock, MagicMock

# Add project root to path so imports work
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from commands.moderator import parse_duration, check_moderation_target


# ===================== Duration Parser Tests =====================

class TestDurationParser:
    @pytest.mark.parametrize(
        "input_str,expected",
        [
            # Single units without spaces
            ("10s", 10),
            ("30m", 1800),
            ("12h", 43200),
            ("5d", 432000),
            ("2w", 1209600),
            ("3mo", 7889400),
            ("1y", 31557600),
            
            # Unit aliases & plurals
            ("10 seconds", 10),
            ("1 second", 1),
            ("5 mins", 300),
            ("10mn", 600),
            ("2 hours", 7200),
            ("1 day", 86400),
            ("2 weeks", 1209600),
            ("3 months", 7889400),
            ("10 years", 315576000),
            
            # Spaces
            ("10   s", 10),
            ("30   mins", 1800),
            ("2   hours", 7200),
            
            # Mixed units (with and without space)
            ("10 years 5 hours", 10 * 31557600 + 5 * 3600),
            ("10 months 2 weeks", 10 * 2629800 + 2 * 604800),
            ("10mn 7d", 10 * 60 + 7 * 86400),
            ("2w 3d 4h 5m 6s", 2 * 604800 + 3 * 86400 + 4 * 3600 + 5 * 60 + 6),
            
            # Commas and "and" fillers
            ("10 years, 5 hours", 10 * 31557600 + 5 * 3600),
            ("10 months and 2 weeks", 10 * 2629800 + 2 * 604800),
            ("1 year, 2 months, and 3 days", 1 * 31557600 + 2 * 2629800 + 3 * 86400),
        ]
    )
    def test_valid_durations(self, input_str, expected):
        assert parse_duration(input_str) == expected

    @pytest.mark.parametrize(
        "input_str",
        [
            "",
            "   ",
            "abc",
            "10",            # No unit
            "10y extra",     # Trailing invalid content
            "extra 10y",     # Leading invalid content
            "10y 5",         # Mixed invalid trailing content
            "10xyz",         # Invalid unit
            "10 y 5 xyz",    # Invalid unit in mixed
            "10y and 5xyz",  # Invalid unit in mixed with "and"
        ]
    )
    def test_invalid_durations(self, input_str):
        assert parse_duration(input_str) is None


# ===================== Moderation Target Checker Tests =====================

class TestModerationTargetChecker:
    @pytest.mark.asyncio
    async def test_outside_guild(self):
        """Should fail if interaction is outside a guild."""
        interaction = MagicMock()
        interaction.guild = None
        interaction.user.id = 123
        
        target = MagicMock()
        bot = MagicMock()
        
        res = await check_moderation_target(interaction, target, bot, "mute")
        assert res is not None
        assert "must be used in a guild" in res

    @pytest.mark.asyncio
    async def test_self_target(self):
        """Should fail if attempting to target self."""
        interaction = MagicMock()
        interaction.guild = MagicMock()
        interaction.user.id = 123
        
        target = MagicMock()
        target.id = 123
        
        bot = MagicMock()
        bot.user.id = 999
        
        res = await check_moderation_target(interaction, target, bot, "mute")
        assert res is not None
        assert "cannot mute yourself" in res

    @pytest.mark.asyncio
    async def test_bot_target(self):
        """Should fail if attempting to target the bot."""
        interaction = MagicMock()
        interaction.guild = MagicMock()
        interaction.user.id = 123
        
        target = MagicMock()
        target.id = 999
        
        bot = MagicMock()
        bot.user.id = 999
        
        res = await check_moderation_target(interaction, target, bot, "mute")
        assert res is not None
        assert "cannot mute the bot" in res

    @pytest.mark.asyncio
    async def test_user_hierarchy_fail(self):
        """Should fail if the target has equal or higher role than executor."""
        interaction = MagicMock()
        interaction.guild = MagicMock()
        interaction.user.id = 123
        
        # Executor top role is lower
        exec_role = MagicMock()
        exec_role.__ge__.return_type = False
        exec_role.__lt__.return_type = True
        interaction.user.top_role = exec_role
        
        target = MagicMock()
        target.id = 456
        target_role = MagicMock()
        target.top_role = target_role
        
        # Mock target_role >= exec_role to be True
        target_role.__ge__ = MagicMock(return_value=True)
        
        bot = MagicMock()
        bot.user.id = 999
        
        res = await check_moderation_target(interaction, target, bot, "mute")
        assert res is not None
        assert "someone with a higher or equal role" in res

    @pytest.mark.asyncio
    async def test_bot_hierarchy_fail(self):
        """Should fail if the target has equal or higher role than the bot."""
        interaction = MagicMock()
        interaction.guild = MagicMock()
        interaction.user.id = 123
        
        # Executor top role is higher than target top role
        exec_role = MagicMock()
        interaction.user.top_role = exec_role
        
        target = MagicMock()
        target.id = 456
        target_role = MagicMock()
        target.top_role = target_role
        
        exec_role.__ge__ = MagicMock(return_value=False)
        target_role.__ge__ = MagicMock(return_value=False)
        # We need exec_role > target_role so target_role >= exec_role is False
        # But target_role >= bot_role is True
        
        bot_member = MagicMock()
        bot_role = MagicMock()
        bot_member.top_role = bot_role
        
        interaction.guild.me = bot_member
        
        # target_role >= bot_role is True
        target_role.__ge__ = MagicMock(side_effect=lambda other: other == bot_role)
        
        bot = MagicMock()
        bot.user.id = 999
        
        res = await check_moderation_target(interaction, target, bot, "mute")
        assert res is not None
        assert "higher or equal role than my own" in res

    @pytest.mark.asyncio
    async def test_admin_target_fail(self):
        """Should fail if the target is an administrator."""
        interaction = MagicMock()
        interaction.guild = MagicMock()
        interaction.user.id = 123
        
        exec_role = MagicMock()
        interaction.user.top_role = exec_role
        
        target = MagicMock()
        target.id = 456
        target_role = MagicMock()
        target.top_role = target_role
        
        # target_role is lower than exec_role
        target_role.__ge__ = MagicMock(return_value=False)
        
        # target is administrator
        target.guild_permissions.administrator = True
        
        bot_member = MagicMock()
        bot_role = MagicMock()
        bot_member.top_role = bot_role
        interaction.guild.me = bot_member
        
        bot = MagicMock()
        bot.user.id = 999
        
        res = await check_moderation_target(interaction, target, bot, "mute")
        assert res is not None
        assert "cannot mute an administrator" in res

    @pytest.mark.asyncio
    async def test_success_case(self):
        """Should return None if all moderation checks pass."""
        interaction = MagicMock()
        interaction.guild = MagicMock()
        interaction.user.id = 123
        
        exec_role = MagicMock()
        interaction.user.top_role = exec_role
        
        target = MagicMock()
        target.id = 456
        target_role = MagicMock()
        target.top_role = target_role
        
        # target_role is lower than executor
        target_role.__ge__ = MagicMock(return_value=False)
        # target is NOT administrator
        target.guild_permissions.administrator = False
        
        bot_member = MagicMock()
        bot_role = MagicMock()
        bot_member.top_role = bot_role
        interaction.guild.me = bot_member
        
        # bot_role is higher than target_role
        bot_role.__ge__ = MagicMock(return_value=True)
        
        bot = MagicMock()
        bot.user.id = 999
        
        res = await check_moderation_target(interaction, target, bot, "mute")
        assert res is None
