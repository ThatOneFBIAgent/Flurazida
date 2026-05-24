# tests/test_bank_economy.py
# Pytest suite for Flurazide's new Bank economy, deposit/withdraw, Vault Expansion, and TTL caching.

import asyncio
import os
import sys
import pytest
import time

# Add project root to path so imports work
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tempfile
_test_dir = tempfile.mkdtemp(prefix="flurazide_test_bank_")

import database.manager as db_mod
db_mod.ECONOMY_DB_PATH = os.path.join(_test_dir, "test_economy.db")
db_mod.MODERATOR_DB_PATH = os.path.join(_test_dir, "test_moderator.db")

# Re-create the DatabaseManager with fresh connections
db_mod.db = db_mod.DatabaseManager()


@pytest.fixture(autouse=True)
async def setup_db():
    """Initialize clean test databases before each test."""
    await db_mod.db.close()
    db_mod.db = db_mod.DatabaseManager()

    # Remove old test files
    for path in [db_mod.ECONOMY_DB_PATH, db_mod.MODERATOR_DB_PATH]:
        if os.path.exists(path):
            os.remove(path)

    # Invalidate TTL cache to be safe
    db_mod._cached_total_economy = None
    db_mod._last_economy_sum_time = 0.0

    await db_mod.init_databases()
    yield
    await db_mod.db.close()


# ===================== Bank Economy Tests =====================

class TestBankEconomy:
    @pytest.mark.asyncio
    async def test_bank_default_values(self):
        """New users should start with 0 bank balance and 10,000 bank_max."""
        await db_mod.add_user(1001, "BankUser")
        bank = await db_mod.get_bank(1001)
        bank_max = await db_mod.get_bank_max(1001)
        assert bank == 0
        assert bank_max == 10000

    @pytest.mark.asyncio
    async def test_deposit_bank_success(self):
        """Depositing within capacity should deduct wallet and add to bank."""
        await db_mod.add_user(1002, "Depositor")
        await db_mod.update_balance(1002, 5000)
        
        success = await db_mod.deposit_bank(1002, 3000)
        assert success is True
        
        wallet = await db_mod.get_balance(1002)
        bank = await db_mod.get_bank(1002)
        assert wallet == 2000
        assert bank == 3000

    @pytest.mark.asyncio
    async def test_deposit_bank_insufficient_funds(self):
        """Depositing more than owned wallet balance should fail."""
        await db_mod.add_user(1003, "LowCash")
        await db_mod.update_balance(1003, 100)
        
        success = await db_mod.deposit_bank(1003, 500)
        assert success is False
        
        wallet = await db_mod.get_balance(1003)
        bank = await db_mod.get_bank(1003)
        assert wallet == 100
        assert bank == 0

    @pytest.mark.asyncio
    async def test_deposit_bank_over_max_limit(self):
        """Depositing more than bank_max capacity should fail."""
        await db_mod.add_user(1004, "MaxCap")
        await db_mod.update_balance(1004, 15000)
        
        # Try depositing 12,000 (limit is 10,000)
        success = await db_mod.deposit_bank(1004, 12000)
        assert success is False
        
        wallet = await db_mod.get_balance(1004)
        bank = await db_mod.get_bank(1004)
        assert wallet == 15000
        assert bank == 0

    @pytest.mark.asyncio
    async def test_withdraw_bank_success(self):
        """Withdrawing within balance should deduct bank and add to wallet."""
        await db_mod.add_user(1005, "Withdrawer")
        await db_mod.update_balance(1005, 5000)
        await db_mod.deposit_bank(1005, 4000)
        
        success = await db_mod.withdraw_bank(1005, 1500)
        assert success is True
        
        wallet = await db_mod.get_balance(1005)
        bank = await db_mod.get_bank(1005)
        assert wallet == 2500
        assert bank == 2500

    @pytest.mark.asyncio
    async def test_withdraw_bank_excessive(self):
        """Withdrawing more than stored bank balance should fail."""
        await db_mod.add_user(1006, "NoBank")
        await db_mod.update_balance(1006, 1000)
        await db_mod.deposit_bank(1006, 500)
        
        success = await db_mod.withdraw_bank(1006, 600)
        assert success is False
        
        wallet = await db_mod.get_balance(1006)
        bank = await db_mod.get_bank(1006)
        assert wallet == 500
        assert bank == 500

    @pytest.mark.asyncio
    async def test_increase_bank_max(self):
        """Permanently increasing bank maximum capacity should work."""
        await db_mod.add_user(1007, "Expander")
        new_max = await db_mod.increase_bank_max(1007, 25000)
        assert new_max == 35000
        
        bank_max = await db_mod.get_bank_max(1007)
        assert bank_max == 35000

    @pytest.mark.asyncio
    async def test_decrease_bank(self):
        """Decreasing bank balance should deduct correctly but clamp to 0."""
        await db_mod.add_user(1008, "Heisted")
        await db_mod.update_balance(1008, 1000)
        await db_mod.deposit_bank(1008, 1000)
        
        await db_mod.decrease_bank(1008, 400)
        bank = await db_mod.get_bank(1008)
        assert bank == 600
        
        # Test clamping to 0
        await db_mod.decrease_bank(1008, 1000)
        bank = await db_mod.get_bank(1008)
        assert bank == 0


# ===================== Shop Vault Expansion & Bulk Tests =====================

class TestVaultExpansionAndBulk:
    @pytest.mark.asyncio
    async def test_vault_expansion_use(self):
        """Using a Vault Expansion item should permanently increase bank_max."""
        await db_mod.add_user(2001, "RichGuy")
        await db_mod.add_user_item(2001, 28, "Vault Expansion", uses_left=1)
        
        msg = await db_mod.use_item(2001, 28, quantity=1)
        assert "Vault Expansion activated" in msg
        assert "35,000" in msg
        
        bank_max = await db_mod.get_bank_max(2001)
        assert bank_max == 35000
        
        items = await db_mod.get_user_items(2001)
        assert len(items) == 0

    @pytest.mark.asyncio
    async def test_vault_expansion_bulk_use(self):
        """Bulk using multiple Vault Expansions should stack the boost correctly."""
        await db_mod.add_user(2002, "MegaRich")
        await db_mod.add_user_item(2002, 28, "Vault Expansion", uses_left=3)
        
        msg = await db_mod.use_item(2002, 28, quantity=3)
        assert "Vault Expansion activated" in msg
        assert "85,000" in msg # 10k base + (25k * 3) = 85k
        
        bank_max = await db_mod.get_bank_max(2002)
        assert bank_max == 85000
        
        items = await db_mod.get_user_items(2002)
        assert len(items) == 0

    @pytest.mark.asyncio
    async def test_bulk_buy_item_success(self):
        """Bulk buying items should deduct total price and grant correct uses."""
        await db_mod.add_user(2003, "WholesaleBuyer")
        await db_mod.update_balance(2003, 100000)
        
        # Buy 2x Vault Expansion (35k each = 70k)
        success = await db_mod.buy_item(2003, 28, "Vault Expansion", 35000, uses_left=1, quantity=2)
        assert success is True
        
        wallet = await db_mod.get_balance(2003)
        assert wallet == 30000
        
        items = await db_mod.get_user_items(2003)
        expansion = next(i for i in items if i["item_id"] == "28" or i["item_id"] == 28)
        assert expansion["uses_left"] == 2


# ===================== Telemetry & Caching Tests =====================

class TestTelemetryCaching:
    @pytest.mark.asyncio
    async def test_get_total_economy_sum_combines_wallet_and_bank(self):
        """get_total_economy_sum should sum both wallet and bank balances."""
        await db_mod.add_user(3001, "UserA")
        await db_mod.add_user(3002, "UserB")
        
        await db_mod.update_balance(3001, 1000) # Wallet A = 1000
        await db_mod.update_balance(3002, 5000) 
        await db_mod.deposit_bank(3002, 4000)   # Wallet B = 1000, Bank B = 4000
        
        # total non-negative economy = 1000 + 1000 + 4000 = 6000
        total = await db_mod.get_total_economy_sum()
        assert total == 6000

    @pytest.mark.asyncio
    async def test_ttl_cache_returns_cached_value(self):
        """get_total_economy_sum should return cached value within TTL duration."""
        await db_mod.add_user(3003, "CacheUser")
        await db_mod.update_balance(3003, 5000)
        
        total1 = await db_mod.get_total_economy_sum()
        assert total1 == 5000
        
        # Directly modify DB balance without calling wrapper (bypassing invalidation)
        conn = await db_mod.db.get_economy()
        await conn.execute("UPDATE users SET balance = balance + 1000 WHERE user_id = 3003")
        await conn.commit()
        
        # Calling get_total_economy_sum again should still return 5000 (cached!)
        total2 = await db_mod.get_total_economy_sum()
        assert total2 == 5000
        
        # Invalidate manually by advancing time / setting last sum time to 0
        db_mod._last_economy_sum_time = 0.0
        total3 = await db_mod.get_total_economy_sum()
        assert total3 == 6000
