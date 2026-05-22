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

# ===================== Player Interaction Item Tests =====================

class TestPlayerInteractions:
    @pytest.mark.asyncio
    async def test_robbery_modifier_no_items(self):
        """User with no items should have 0.0 robbery modifier."""
        await db_mod.add_user(800, "NoItems")
        mod = await db_mod.get_robbery_modifier(800)
        assert mod == 0.0

    @pytest.mark.asyncio
    async def test_robbery_modifier_from_bolt_cutters(self):
        """Bolt Cutters (ID 3, +50%) should give +0.50 modifier."""
        await db_mod.add_user(801, "BoltCutter")
        await db_mod.add_user_item(801, 3, "Bolt Cutters", uses_left=4)
        mod = await db_mod.get_robbery_modifier(801)
        assert mod == pytest.approx(0.50)

    @pytest.mark.asyncio
    async def test_robbery_modifier_stacks(self):
        """Bolt Cutters (+50%) + Hackatron (+20%) should give +0.70 combined."""
        await db_mod.add_user(802, "StackRobber")
        await db_mod.add_user_item(802, 3, "Bolt Cutters",   uses_left=4)
        await db_mod.add_user_item(802, 8, "Hackatron 9900", uses_left=5)
        mod = await db_mod.get_robbery_modifier(802)
        assert mod == pytest.approx(0.70)

    @pytest.mark.asyncio
    async def test_taser_excluded_from_robber_modifier(self):
        """Taser (victim-only) should not contribute to the robber's modifier."""
        await db_mod.add_user(803, "TaserOwner")
        await db_mod.add_user_item(803, 5, "Taser", uses_left=2)
        mod = await db_mod.get_robbery_modifier(803)
        assert mod == 0.0

    @pytest.mark.asyncio
    async def test_victim_padlock_returns_modifier(self):
        """Victim with Padlocked Wallet should return +0.50 defensive modifier."""
        await db_mod.add_user(810, "Padlocked")
        await db_mod.add_user_item(810, 4, "Padlocked Wallet", uses_left=5)
        mod = await db_mod.get_victim_rob_modifier(810)
        assert mod == pytest.approx(0.50)

    @pytest.mark.asyncio
    async def test_victim_padlock_decrements_on_call(self):
        """get_victim_rob_modifier should consume 1 Padlocked Wallet use."""
        await db_mod.add_user(811, "PadlockDecrement")
        await db_mod.add_user_item(811, 4, "Padlocked Wallet", uses_left=3)
        await db_mod.get_victim_rob_modifier(811)
        items = await db_mod.get_user_items(811)
        padlock = next((i for i in items if str(i["item_id"]) == "4"), None)
        assert padlock is not None
        assert padlock["uses_left"] == 2

    @pytest.mark.asyncio
    async def test_victim_padlock_removes_on_last_use(self):
        """Padlocked Wallet with 1 use left should vanish after get_victim_rob_modifier."""
        await db_mod.add_user(812, "PadlockLast")
        await db_mod.add_user_item(812, 4, "Padlocked Wallet", uses_left=1)
        await db_mod.get_victim_rob_modifier(812)
        items = await db_mod.get_user_items(812)
        assert not any(str(i["item_id"]) == "4" for i in items), "Wallet should be removed"

    @pytest.mark.asyncio
    async def test_victim_no_padlock_returns_zero(self):
        """Victim with no Padlocked Wallet should return 0.0."""
        await db_mod.add_user(813, "NoPadlock")
        mod = await db_mod.get_victim_rob_modifier(813)
        assert mod == 0.0

    @pytest.mark.asyncio
    async def test_check_taser_defense_with_taser(self):
        """Victim with Taser should return True."""
        await db_mod.add_user(820, "TaserVictim")
        await db_mod.add_user_item(820, 5, "Taser", uses_left=2)
        assert await db_mod.check_taser_defense(820) is True

    @pytest.mark.asyncio
    async def test_check_taser_defense_without_taser(self):
        """Victim without a Taser should return False."""
        await db_mod.add_user(821, "NoTaser")
        assert await db_mod.check_taser_defense(821) is False

    @pytest.mark.asyncio
    async def test_decrement_taser_reduces_uses(self):
        """decrement_taser_use should reduce Taser uses by 1."""
        await db_mod.add_user(822, "TaserDecrement")
        await db_mod.add_user_item(822, 5, "Taser", uses_left=2)
        await db_mod.decrement_taser_use(822)
        items = await db_mod.get_user_items(822)
        taser = next((i for i in items if str(i["item_id"]) == "5"), None)
        assert taser is not None and taser["uses_left"] == 1

    @pytest.mark.asyncio
    async def test_decrement_taser_removes_on_last_use(self):
        """Taser with 1 use should be removed after decrement_taser_use."""
        await db_mod.add_user(823, "TaserLast")
        await db_mod.add_user_item(823, 5, "Taser", uses_left=1)
        await db_mod.decrement_taser_use(823)
        assert await db_mod.check_taser_defense(823) is False
        items = await db_mod.get_user_items(823)
        assert not any(str(i["item_id"]) == "5" for i in items)

    @pytest.mark.asyncio
    async def test_taser_active_use_broke_target_does_not_consume(self):
        """Taser active use on a target with <50 coins should not consume a charge."""
        await db_mod.add_user(830, "TaserUser")
        await db_mod.add_user(831, "BrokeTarget")
        await db_mod.add_user_item(830, 5, "Taser", uses_left=2)
        # BrokeTarget has 0 coins
        msg = await db_mod.use_item(830, 5, target_id=831)
        assert "too broke" in msg.lower()
        items = await db_mod.get_user_items(830)
        taser = next((i for i in items if str(i["item_id"]) == "5"), None)
        assert taser is not None and taser["uses_left"] == 2

    @pytest.mark.asyncio
    async def test_consume_robber_item_uses_decrements_bolt_cutters(self):
        """consume_robber_item_uses should decrement Bolt Cutters by 1."""
        await db_mod.add_user(840, "RobberWithCutters")
        await db_mod.add_user_item(840, 3, "Bolt Cutters", uses_left=4)
        await db_mod.consume_robber_item_uses(840)
        items = await db_mod.get_user_items(840)
        cutter = next((i for i in items if str(i["item_id"]) == "3"), None)
        assert cutter is not None and cutter["uses_left"] == 3

    @pytest.mark.asyncio
    async def test_consume_robber_item_uses_removes_on_last(self):
        """Bolt Cutters with 1 use should be removed after consume_robber_item_uses."""
        await db_mod.add_user(841, "LastCutter")
        await db_mod.add_user_item(841, 3, "Bolt Cutters", uses_left=1)
        await db_mod.consume_robber_item_uses(841)
        items = await db_mod.get_user_items(841)
        assert not any(str(i["item_id"]) == "3" for i in items)


# ===================== Claim (Daily / Monthly) Tests =====================

class TestClaims:
    @pytest.mark.asyncio
    async def test_get_last_claim_default(self):
        """First-time caller should return (0, 0)."""
        await db_mod.add_user(900, "Claimer")
        last, streak = await db_mod.get_last_claim(900, "daily")
        assert last == 0 and streak == 0

    @pytest.mark.asyncio
    async def test_set_and_get_claim(self):
        """set_last_claim should persist and be retrievable."""
        await db_mod.add_user(901, "ClaimSetter")
        await db_mod.set_last_claim(901, "daily", 1_000_000, 5)
        last, streak = await db_mod.get_last_claim(901, "daily")
        assert last == 1_000_000 and streak == 5

    @pytest.mark.asyncio
    async def test_claim_upsert_overwrites(self):
        """Calling set_last_claim twice should overwrite previous values."""
        await db_mod.add_user(902, "ClaimOverwrite")
        await db_mod.set_last_claim(902, "daily", 1_000, 1)
        await db_mod.set_last_claim(902, "daily", 2_000, 3)
        last, streak = await db_mod.get_last_claim(902, "daily")
        assert last == 2_000 and streak == 3

    @pytest.mark.asyncio
    async def test_daily_and_monthly_are_independent(self):
        """'daily' and 'monthly' should be stored independently per user."""
        await db_mod.add_user(903, "MultiClaimer")
        await db_mod.set_last_claim(903, "daily",   1_000, 7)
        await db_mod.set_last_claim(903, "monthly", 2_000, 0)
        d_last, d_streak = await db_mod.get_last_claim(903, "daily")
        m_last, m_streak = await db_mod.get_last_claim(903, "monthly")
        assert d_last == 1_000 and d_streak == 7
        assert m_last == 2_000 and m_streak == 0
