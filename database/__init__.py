# database/__init__.py
# Re-export all database functions for backward-compatible imports.
# Cogs can do: from database.manager import get_balance, update_balance, etc.
# Or: from database import manager as db_module

from database.manager import *
