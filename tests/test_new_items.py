# tests/test_new_items.py
# Pytest suite for the 16 new miscellaneous items, status effects, and active effects in Flurazide.

import asyncio
import os
import sys
import time
import pytest
import random
from unittest.mock import patch

# Add project root to path so imports work
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# We need to set up a test-safe environment before importing the module
# Override the DB paths to use temp files
import tempfile
_test_dir = tempfile.mkdtemp(prefix="flurazide_test_new_")

# Patch the paths BEFORE importing the module
import database.manager as db_mod
db_mod.ECONOMY_DB_PATH = os.path.join(_test_dir, "test_economy_new.db")
db_mod.MODERATOR_DB_PATH = os.path.join(_test_dir, "test_moderator_new.db")

# Re-create the DatabaseManager with fresh connections
db_mod.db = db_mod.DatabaseManager()


@pytest.fixture(autouse=True)
async def setup_db():
    """Initialize clean test databases before each test."""
    # Close previous connections if any
    await db_mod.db.close()
    db_mod.db = db_mod.DatabaseManager()

    # Remove old test files
    for path in [db_mod.ECONOMY_DB_PATH, db_mod.MODERATOR_DB_PATH]:
        if os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass

    await db_mod.init_databases()
    yield
    await db_mod.db.close()


class TestStatusEffects:
    @pytest.mark.asyncio
    async def test_add_and_has_effect(self):
        """Verify that adding an active effect works and is detected."""
        user_id = 999111
        await db_mod.add_user(user_id, "EffectTester")
        
        # Initially, user should not have the effect
        assert await db_mod.has_active_effect(user_id, "no_gamble") is False
        
        # Add the effect for 10 seconds
        await db_mod.add_user_effect(user_id, "no_gamble", 10, modifier=42)
        
        # Check active status
        assert await db_mod.has_active_effect(user_id, "no_gamble") is True
        
        # Verify modifier
        modifier = await db_mod.get_active_effect_modifier(user_id, "no_gamble")
        assert modifier == 42
        
        # Verify remaining time format
        remaining = await db_mod.get_effect_remaining_time(user_id, "no_gamble")
        assert "s" in remaining or "m" in remaining

    @pytest.mark.asyncio
    async def test_expired_effect_cleanup(self):
        """Verify that expired effects are cleaned up and return False."""
        user_id = 999222
        await db_mod.add_user(user_id, "ExpireTester")
        
        # Add an already expired effect (-5 seconds duration)
        await db_mod.add_user_effect(user_id, "no_gamble", -5, modifier=0)
        
        # Since it's expired, it should return False and get deleted
        assert await db_mod.has_active_effect(user_id, "no_gamble") is False
        
        # Verify it's no longer in the DB
        modifier = await db_mod.get_active_effect_modifier(user_id, "no_gamble")
        assert modifier == 0


class TestGamblingBoosts:
    @pytest.mark.asyncio
    async def test_check_and_use_boost_no_items(self):
        """Verify check_and_use_gambling_boost returns False if user has no relevant items."""
        user_id = 888111
        await db_mod.add_user(user_id, "NoItemsTester")
        
        res = await db_mod.check_and_use_gambling_boost(user_id)
        assert res is False

    @pytest.mark.asyncio
    async def test_check_and_use_boost_usb_stick_arrest(self):
        """Verify USB Stick (ID 21) triggers arrest with 5% chance."""
        user_id = 888222
        await db_mod.add_user(user_id, "USBArrestTester")
        
        # Add USB Stick (ID 21) with 3 uses
        await db_mod.add_user_item(user_id, 21, "USB Stick", uses_left=3)
        
        # Mock random.random() to return 0.01 (which is < 0.05, triggering arrest)
        with patch("random.random", return_value=0.01):
            res = await db_mod.check_and_use_gambling_boost(user_id)
            assert res == "arrested"
            
        # Verify uses decreased
        items = await db_mod.get_user_items(user_id)
        usb_item = next((i for i in items if int(i["item_id"]) == 21), None)
        assert usb_item is not None
        assert usb_item["uses_left"] == 2
        
        # Verify "no_gamble" effect was applied
        assert await db_mod.has_active_effect(user_id, "no_gamble") is True

    @pytest.mark.asyncio
    async def test_check_and_use_boost_usb_stick_success(self):
        """Verify USB Stick triggers boost with 35% chance when not arrested."""
        user_id = 888333
        await db_mod.add_user(user_id, "USBBoostTester")
        await db_mod.add_user_item(user_id, 21, "USB Stick", uses_left=1)
        
        # Mock random to return 0.1 for first call (arrest: 0.1 >= 0.05, no arrest)
        # and 0.2 for second call (boost: 0.2 < 0.35, triggers boost)
        with patch("random.random", side_effect=[0.1, 0.2]):
            res = await db_mod.check_and_use_gambling_boost(user_id)
            assert res is True
            
        # Since it had 1 use left, it should now be removed from inventory
        items = await db_mod.get_user_items(user_id)
        usb_item = next((i for i in items if int(i["item_id"]) == 21), None)
        assert usb_item is None

    @pytest.mark.asyncio
    async def test_check_and_use_boost_sea_basshead(self):
        """Verify Sea BassHead (ID 14) triggers boost with 25% chance."""
        user_id = 888444
        await db_mod.add_user(user_id, "BassHeadTester")
        await db_mod.add_user_item(user_id, 14, "Sea BassHead", uses_left=2)
        
        # Mock random to trigger boost (< 0.25)
        with patch("random.random", return_value=0.1):
            res = await db_mod.check_and_use_gambling_boost(user_id)
            assert res is True
            
        # Verify uses decreased to 1
        items = await db_mod.get_user_items(user_id)
        fish_item = next((i for i in items if int(i["item_id"]) == 14), None)
        assert fish_item is not None
        assert fish_item["uses_left"] == 1

    @pytest.mark.asyncio
    async def test_check_and_use_boost_chicken_wishbone(self):
        """Verify Half eaten rotisserie chicken (ID 23) positive/negative effects."""
        user_id = 888555
        await db_mod.add_user(user_id, "ChickenTester")
        
        # Case A: Positive trigger (first roll < 0.50), save loss (second roll < 0.20)
        await db_mod.add_user_item(user_id, 23, "Half eaten rotisserie chicken", uses_left=3)
        with patch("random.random", side_effect=[0.3, 0.1]):
            res = await db_mod.check_and_use_gambling_boost(user_id)
            assert res is True
            
        # Case B: Negative trigger (first roll >= 0.50), backfire (second roll < 0.15)
        with patch("random.random", side_effect=[0.7, 0.1]):
            res = await db_mod.check_and_use_gambling_boost(user_id)
            assert res == "chicken_backfire"


class TestRobberyAndVictimModifiers:
    @pytest.mark.asyncio
    async def test_robber_cone_penalty(self):
        """Verify that "cone_penalty" effect reduces robber success by 30%."""
        user_id = 777111
        await db_mod.add_user(user_id, "RobberConeTester")
        
        # Initial modifier should be 0.0
        assert await db_mod.get_robbery_modifier(user_id) == 0.0
        
        # Apply cone penalty
        await db_mod.add_user_effect(user_id, "cone_penalty", 3600)
        
        # Modifier should now be -0.30
        assert await db_mod.get_robbery_modifier(user_id) == -0.30

    @pytest.mark.asyncio
    async def test_victim_defenses(self):
        """Verify victim defense items: Coffee Mug and Expired fish."""
        victim_id = 777222
        await db_mod.add_user(victim_id, "VictimTester")
        
        # Add Coffee Mug (ID 13, -30% rob success chance)
        await db_mod.add_user_item(victim_id, 13, "Coffee Mug (Full)", uses_left=5)
        
        # Add Expired fish (ID 26, -15% rob success chance)
        await db_mod.add_user_item(victim_id, 26, "Expired fish", uses_left=2)
        
        # Modifier should be abs(-30) + abs(-15) = 45% -> 0.45
        net_modifier = await db_mod.get_victim_rob_modifier(victim_id)
        assert net_modifier == 0.45
        
        # Both items should have decremented uses
        items = await db_mod.get_user_items(victim_id)
        coffee = next((i for i in items if int(i["item_id"]) == 13), None)
        fish = next((i for i in items if int(i["item_id"]) == 26), None)
        assert coffee["uses_left"] == 4
        assert fish["uses_left"] == 1

    @pytest.mark.asyncio
    async def test_victim_disoriented(self):
        """Verify "disoriented" effect reduces victim defense by 25%."""
        victim_id = 777333
        await db_mod.add_user(victim_id, "DisorientedVictim")
        
        # Apply disoriented status effect
        await db_mod.add_user_effect(victim_id, "disoriented", 3600)
        
        # Without items, it should not go below 0.0 (clamped at 0.0)
        assert await db_mod.get_victim_rob_modifier(victim_id) == 0.0
        
        # Add Coffee Mug (gives 30% defense)
        await db_mod.add_user_item(victim_id, 13, "Coffee Mug (Full)", uses_left=2)
        
        # 30% mug defense - 25% disorientation penalty = 5% defense -> 0.05
        assert pytest.approx(await db_mod.get_victim_rob_modifier(victim_id)) == 0.05
