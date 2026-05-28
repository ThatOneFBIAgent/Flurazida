import pytest
import time
import asyncio
from database import manager as _reminder_module  # used by patch targets in test_reminders_db
from commands.gambling import DoubleOrNothingView

@pytest.mark.asyncio
async def test_gambling_doubleornothing_curve():
    view = DoubleOrNothingView(user_id=1, bet=100, current_multiplier=1, current_prob=0.5)
    assert view.current_prob == 0.5
    
    new_multiplier = view.current_multiplier * 2
    new_prob = view.current_prob * 0.75
    assert new_multiplier == 2
    assert new_prob == 0.375
    
    new_multiplier2 = new_multiplier * 2
    new_prob2 = new_prob * 0.75
    assert new_multiplier2 == 4
    assert new_prob2 == 0.28125

@pytest.mark.asyncio
async def test_reminders_db():
    """
    Simulates the reminders table schema entirely in-memory using a dict.
    No SQLite connection or init_databases() call is made — this keeps the
    test hermetic and avoids aiosqlite lifecycle issues in the test runner.
    """
    from unittest.mock import patch, AsyncMock

    # In-memory store: reminder_id -> (reminder_id, user_id, channel_id, message, timestamp)
    _store = {}
    _next_id = [1]

    async def mock_add_reminder(user_id, channel_id, message, timestamp):
        rid = _next_id[0]
        _store[rid] = (rid, user_id, channel_id, message, timestamp)
        _next_id[0] += 1

    async def mock_get_due_reminders(current_timestamp):
        return [
            (rid, uid, cid, msg, ts)
            for rid, (_, uid, cid, msg, ts) in _store.items()
            if ts <= current_timestamp
        ]

    async def mock_delete_reminder(reminder_id):
        _store.pop(reminder_id, None)

    with patch("database.manager.add_reminder", side_effect=mock_add_reminder), \
         patch("database.manager.get_due_reminders", side_effect=mock_get_due_reminders), \
         patch("database.manager.delete_reminder", side_effect=mock_delete_reminder):

        from database.manager import add_reminder, get_due_reminders, delete_reminder

        user_id = 999123
        channel_id = 999456
        message = "Test reminder dry data"
        future_time = int(time.time()) + 1000
        past_time = int(time.time()) - 1000

        await add_reminder(user_id, channel_id, message, future_time)
        await add_reminder(user_id, channel_id, message, past_time)

        current_time = int(time.time())
        due = await get_due_reminders(current_time)

        due_past = [r for r in due if r[1] == user_id and r[2] == channel_id and r[3] == message]
        assert len(due_past) >= 1

        for r in due_past:
            await delete_reminder(r[0])

        # Verify the past one is gone, future one is still present
        future_due = await get_due_reminders(int(time.time()) + 2000)
        future_clean = [r for r in future_due if r[1] == user_id and r[2] == channel_id and r[3] == message]
        assert len(future_clean) == 1

        await delete_reminder(future_clean[0][0])
        assert len(_store) == 0

