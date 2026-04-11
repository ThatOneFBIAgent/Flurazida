# tests/test_database.py
# Pytest suite for Flurazide's database functions.
# Verifies economy operations, item effects, and the use_item fix.

import asyncio
import os
import sys
import pytest
import aiosqlite

# Add project root to path so imports work
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# We need to set up a test-safe environment before importing the module
# Override the DB paths to use temp files
import tempfile
_test_dir = tempfile.mkdtemp(prefix="flurazide_test_")

# Patch the paths BEFORE importing the module
import database.manager as db_mod
db_mod.ECONOMY_DB_PATH = os.path.join(_test_dir, "test_economy.db")
db_mod.MODERATOR_DB_PATH = os.path.join(_test_dir, "test_moderator.db")

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
            os.remove(path)

    await db_mod.init_databases()
    yield
    await db_mod.db.close()


# ===================== Economy Tests =====================

class TestEconomy:
    @pytest.mark.asyncio
    async def test_add_user_and_get_balance(self):
        """New users should start with 0 balance."""
        await db_mod.add_user(12345, "TestUser")
        bal = await db_mod.get_balance(12345)
        assert bal == 0

    @pytest.mark.asyncio
    async def test_update_balance_positive(self):
        """Positive balance updates should work."""
        await db_mod.add_user(100, "Earner")
        await db_mod.update_balance(100, 500)
        bal = await db_mod.get_balance(100)
        assert bal == 500

    @pytest.mark.asyncio
    async def test_update_balance_negative(self):
        """Negative balance updates should work, clamped at DEBT_FLOOR."""
        await db_mod.add_user(200, "Spender")
        await db_mod.update_balance(200, -500)
        bal = await db_mod.get_balance(200)
        assert bal == -500

    @pytest.mark.asyncio
    async def test_debt_floor_clamp(self):
        """Balance should never go below DEBT_FLOOR (-1000)."""
        await db_mod.add_user(300, "DebtKing")
        await db_mod.update_balance(300, -5000)
        bal = await db_mod.get_balance(300)
        assert bal == db_mod.DEBT_FLOOR

    @pytest.mark.asyncio
    async def test_add_user_idempotent(self):
        """Adding the same user twice should not reset balance."""
        await db_mod.add_user(400, "Idempotent")
        await db_mod.update_balance(400, 999)
        await db_mod.add_user(400, "Idempotent")
        bal = await db_mod.get_balance(400)
        assert bal == 999


# ===================== Shop & Item Tests =====================

class TestShopItems:
    @pytest.mark.asyncio
    async def test_shop_items_ids_unique(self):
        """All SHOP_ITEMS should have unique IDs."""
        ids = [item["id"] for item in db_mod.SHOP_ITEMS]
        assert len(ids) == len(set(ids)), "Duplicate item IDs found!"

    @pytest.mark.asyncio
    async def test_item_effects_match_shop(self):
        """Every ITEM_EFFECTS key should correspond to a valid SHOP_ITEMS id."""
        shop_ids = {item["id"] for item in db_mod.SHOP_ITEMS}
        for effect_id in db_mod.ITEM_EFFECTS:
            assert effect_id in shop_ids, f"ITEM_EFFECTS has ID {effect_id} not in SHOP_ITEMS"

    @pytest.mark.asyncio
    async def test_buy_item_success(self):
        """Buying an item with sufficient balance should work."""
        await db_mod.add_user(500, "Buyer")
        await db_mod.update_balance(500, 10000)
        result = await db_mod.buy_item(500, 1, "Bragging Rights", 10000)
        assert result is True
        bal = await db_mod.get_balance(500)
        assert bal == 0

    @pytest.mark.asyncio
    async def test_buy_item_insufficient_funds(self):
        """Buying an item without enough balance should fail."""
        await db_mod.add_user(501, "Broke")
        result = await db_mod.buy_item(501, 1, "Bragging Rights", 10000)
        assert result is False

    @pytest.mark.asyncio
    async def test_buy_item_nonexistent_user(self):
        """Buying as a non-existent user should fail."""
        result = await db_mod.buy_item(99999, 1, "Bragging Rights", 10000)
        assert result is False


# ===================== use_item Bug Fix Tests =====================

class TestUseItem:
    @pytest.mark.asyncio
    async def test_use_item_decrements_uses(self):
        """FIX: use_item should decrement uses_left."""
        await db_mod.add_user(600, "ItemUser")
        await db_mod.add_user_item(600, 11, "Watermelon", uses_left=3)

        msg = await db_mod.use_item(600, 11)
        assert msg is not None, "use_item should return a message"
        assert "2 uses remaining" in msg

        items = await db_mod.get_user_items(600)
        watermelon = next((i for i in items if i["item_id"] == "11" or i["item_id"] == 11), None)
        assert watermelon is not None
        assert watermelon["uses_left"] == 2

    @pytest.mark.asyncio
    async def test_use_item_removes_on_last_use(self):
        """FIX: use_item should remove item when uses_left reaches 0."""
        await db_mod.add_user(601, "LastUser")
        await db_mod.add_user_item(601, 1, "Bragging Rights", uses_left=1)

        msg = await db_mod.use_item(601, 1)
        assert msg is not None
        assert "last use" in msg.lower()

        items = await db_mod.get_user_items(601)
        bragging = next((i for i in items if i["item_id"] == "1" or i["item_id"] == 1), None)
        assert bragging is None, "Item should be removed after last use"

    @pytest.mark.asyncio
    async def test_use_item_returns_message(self):
        """FIX: use_item should always return a string message."""
        await db_mod.add_user(602, "MsgUser")
        await db_mod.add_user_item(602, 11, "Watermelon", uses_left=5)

        msg = await db_mod.use_item(602, 11)
        assert isinstance(msg, str), f"Expected str, got {type(msg)}"
        assert len(msg) > 0

    @pytest.mark.asyncio
    async def test_use_item_no_item(self):
        """Using an item you don't own should return error message."""
        await db_mod.add_user(603, "NoItems")
        msg = await db_mod.use_item(603, 99)
        assert "don't have" in msg.lower()

    @pytest.mark.asyncio
    async def test_use_item_zero_uses(self):
        """Using an item with 0 uses should return error message."""
        await db_mod.add_user(604, "NoUses")
        # Directly insert with 0 uses
        conn = await db_mod.db.get_economy()
        await conn.execute(
            "INSERT INTO user_items (user_id, item_id, item_name, uses_left, effect_modifier) VALUES (?, ?, ?, ?, ?)",
            (604, 11, "Watermelon", 0, 0)
        )
        await conn.commit()

        msg = await db_mod.use_item(604, 11)
        assert "no uses left" in msg.lower()

    @pytest.mark.asyncio
    async def test_use_item_robbery_modifier(self):
        """Using Bolt Cutters should modify robbery success rate."""
        await db_mod.add_user(605, "Robber")
        await db_mod.add_user_item(605, 3, "Bolt Cutters", uses_left=2, effect_modifier=0)

        msg = await db_mod.use_item(605, 3)
        assert "robbery" in msg.lower() or "50%" in msg

    @pytest.mark.asyncio
    async def test_use_taser_defense(self):
        """Using Taser should apply robbery protection."""
        await db_mod.add_user(606, "Defender")
        await db_mod.add_user_item(606, 5, "Taser", uses_left=2, effect_modifier=0)

        msg = await db_mod.use_item(606, 5)
        assert "protected" in msg.lower()

    @pytest.mark.asyncio
    async def test_use_gun_defense(self):
        """Using Loaded Gun should apply gun defense."""
        await db_mod.add_user(607, "Armed")
        await db_mod.add_user_item(607, 10, "Loaded Gun", uses_left=19, effect_modifier=0)

        msg = await db_mod.use_item(607, 10)
        assert "armed" in msg.lower()

    @pytest.mark.asyncio
    async def test_use_lucky_coin_placebo(self):
        """Using Lucky Coin should return placebo message."""
        await db_mod.add_user(608, "Gambler")
        await db_mod.add_user_item(608, 6, "Lucky Coin", uses_left=4, effect_modifier=0)

        msg = await db_mod.use_item(608, 6)
        assert "placebo" in msg.lower() or "luckier" in msg.lower()

    @pytest.mark.asyncio
    async def test_financial_drain_effect(self):
        """Using Financial Drain should return drain message."""
        await db_mod.add_user(609, "Drainer")
        await db_mod.add_user_item(609, 2, "Financial Drain", uses_left=1, effect_modifier=0)

        msg = await db_mod.use_item(609, 2)
        assert "drain" in msg.lower()

    @pytest.mark.asyncio
    async def test_item_2_is_financial_drain_not_robber_mask(self):
        """FIX: Item ID 2 should be 'Financial Drain', not 'Robber's Mask'."""
        item_2 = next(i for i in db_mod.SHOP_ITEMS if i["id"] == 2)
        assert item_2["name"] == "Financial Drain"
        assert 2 in db_mod.ITEM_EFFECTS
        assert "drain_percent" in db_mod.ITEM_EFFECTS[2], "Item 2 effect should be drain_percent"
        assert "robbery_modifier" not in db_mod.ITEM_EFFECTS[2], "Item 2 should NOT have robbery_modifier"

    @pytest.mark.asyncio
    async def test_item_10_uses_match_shop(self):
        """FIX: Item 10 (Loaded Gun) uses in ITEM_EFFECTS should match SHOP_ITEMS."""
        shop_10 = next(i for i in db_mod.SHOP_ITEMS if i["id"] == 10)
        assert db_mod.ITEM_EFFECTS[10]["uses"] == shop_10["uses_left"]


# ===================== Gun Defense Tests =====================

class TestGunDefense:
    @pytest.mark.asyncio
    async def test_check_gun_defense_with_gun(self):
        """If user has a loaded gun, check_gun_defense should return uses > 0."""
        await db_mod.add_user(700, "GunOwner")
        await db_mod.add_user_item(700, 10, "Loaded Gun", uses_left=5)

        result = await db_mod.check_gun_defense(700)
        assert result > 0

    @pytest.mark.asyncio
    async def test_check_gun_defense_without_gun(self):
        """If user has no gun, check_gun_defense should return 0."""
        await db_mod.add_user(701, "Unarmed")
        result = await db_mod.check_gun_defense(701)
        assert result == 0

    @pytest.mark.asyncio
    async def test_decrement_gun_use(self):
        """Decrementing gun use should reduce uses by 1."""
        await db_mod.add_user(702, "Shooter")
        await db_mod.add_user_item(702, 10, "Loaded Gun", uses_left=3)

        await db_mod.decrement_gun_use(702)
        remaining = await db_mod.check_gun_defense(702)
        assert remaining == 2


# ===================== Moderation Tests =====================

class TestModeration:
    @pytest.mark.asyncio
    async def test_insert_and_get_case(self):
        """Inserting a case and retrieving it should return correct data."""
        case_num = await db_mod.insert_case(
            guild_id=1000, user_id=100, username="Offender",
            reason="Spamming", action_type="warn",
            moderator_id=200, timestamp=1000000
        )
        assert case_num == 1

        case = await db_mod.get_case(1000, 1)
        assert case is not None
        assert case[3] == "Spamming"

    @pytest.mark.asyncio
    async def test_case_numbers_increment(self):
        """Case numbers should auto-increment per guild."""
        c1 = await db_mod.insert_case(2000, 100, "User1", "Reason1", "warn", 200)
        c2 = await db_mod.insert_case(2000, 101, "User2", "Reason2", "mute", 200)
        assert c2 == c1 + 1

    @pytest.mark.asyncio
    async def test_remove_case(self):
        """Removing a case should make it unfindable."""
        await db_mod.insert_case(3000, 100, "User1", "Reason", "warn", 200)
        await db_mod.remove_case(3000, 1)
        case = await db_mod.get_case(3000, 1)
        assert case is None

    @pytest.mark.asyncio
    async def test_edit_case_reason(self):
        """Editing a case reason should update the stored reason."""
        await db_mod.insert_case(4000, 100, "User1", "Old Reason", "warn", 200)
        await db_mod.edit_case_reason(4000, 1, "New Reason")
        case = await db_mod.get_case(4000, 1)
        assert case[3] == "New Reason"
