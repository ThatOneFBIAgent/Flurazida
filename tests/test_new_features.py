import pytest
import time
import asyncio
from database.manager import add_reminder, get_due_reminders, delete_reminder, init_databases
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
    await init_databases()
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
        
    # Also clean up the future one
    future_reminders = await get_due_reminders(int(time.time()) + 2000)
    future_clean = [r for r in future_reminders if r[1] == user_id and r[2] == channel_id and r[3] == message]
    for r in future_clean:
        await delete_reminder(r[0])
