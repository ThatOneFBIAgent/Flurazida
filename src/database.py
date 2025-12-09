# database.py
# only mess with this file if you want a headache, trust me it's not fun.

# Standard Library Imports
import asyncio
import base64
import io
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
import zipfile
from functools import wraps
from logging import exception

# Third-Party Imports
import aiosqlite
from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload

# Local Imports
from extraconfig import BACKUP_GDRIVE_FOLDER_ID
from logger import get_logger

DEBT_FLOOR = -1000

log = get_logger()

def log_db_call(func):
    from functools import wraps
    @wraps(func)
    async def wrapper(*args, **kwargs):
        log.database(f"ECON DB CALL: {func.__name__} called with args={args}, kwargs={kwargs}")
        return await func(*args, **kwargs)
    return wrapper

def log_mod_call(func):
    from functools import wraps
    @wraps(func)
    async def wrapper(*args, **kwargs):
        log.database(f"MOD DB CALL: {func.__name__} called with args={args}, kwargs={kwargs}")
        return await func(*args, **kwargs)
    return wrapper

SCOPES = ["https://www.googleapis.com/auth/drive.file"]
TOKEN_ENV = "DRIVE_TOKEN_B64"
CREDENTIALS_ENV = "DRIVE_CREDENTIALS_B64"

def load_creds_local():
    with open("token.json", "r") as f:  # same folder as your db.py
        token_info = json.load(f)
    return Credentials.from_authorized_user_info(token_info, SCOPES)

def load_creds_from_env():
    # token (base64 -> json)
    token_b64 = os.environ.get(TOKEN_ENV)
    if not token_b64:
        raise RuntimeError(f"{TOKEN_ENV} not set in environment")

    token_json = base64.b64decode(token_b64).decode()
    token_info = json.loads(token_json)

    # If token_info lacks client_id/client_secret, try to get them from credentials env var
    if ("client_id" not in token_info or "client_secret" not in token_info) and (CREDENTIALS_ENV in os.environ):
        creds_b64 = os.environ.get(CREDENTIALS_ENV)
        creds_json = base64.b64decode(creds_b64).decode()
        creds_info = json.loads(creds_json)
        # creds_info structure has "installed" or "web" keys
        client_block = creds_info.get("installed") or creds_info.get("web") or {}
        token_info.setdefault("client_id", client_block.get("client_id"))
        token_info.setdefault("client_secret", client_block.get("client_secret"))
        token_info.setdefault("token_uri", client_block.get("token_uri") or "https://oauth2.googleapis.com/token")

    # Build a Credentials object from the info
    creds = Credentials.from_authorized_user_info(token_info, SCOPES)

    # Refresh if expired (uses refresh_token). This does not write back to env — but that's fine.
    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            log.info("Refreshed OAuth access token successfully.")
        except Exception as e:
            log.warning(f"Failed to refresh token: {e}. Token may be revoked; you'll need to re-run the local helper.")
    return creds

def build_drive_service():
    # Try local token first, then env-based token loader as fallback
    if os.getenv(RAILWAY_PROJECT_ID):
        log.trace("Running on Railway, using env-based credentials.")
        creds = load_creds_from_env()
    else:
        try:
            creds = load_creds_local()
        except Exception:
            log.info("token.json not found, falling back to env-based credentials.")
            creds = load_creds_from_env()
    return build("drive", "v3", credentials=creds, cache_discovery=False)

# to google:
#   why do you make this so hard

def _backup_db_to_gdrive_sync(local_path, drive_filename, folder_id):
    log.info(f"Backing up {local_path} -> {drive_filename}")
    service = build_drive_service()

    query = f"'{folder_id}' in parents and name='{drive_filename}' and trashed=false"
    res = service.files().list(q=query, fields="files(id, name)").execute()
    files = res.get("files", [])

    media = MediaFileUpload(local_path, mimetype="application/x-sqlite3", resumable=True)

    if files:
        file_id = files[0]["id"]
        service.files().update(fileId=file_id, media_body=media).execute()
        log.success("Updated existing backup.")
    else:
        meta = {"name": drive_filename, "parents": [folder_id]}
        service.files().create(body=meta, media_body=media, fields="id").execute()
        log.success("Created new backup.")

async def backup_db_to_gdrive_env(local_path, drive_filename, folder_id):
    await asyncio.to_thread(_backup_db_to_gdrive_sync, local_path, drive_filename, folder_id)

def _backup_all_dbs_sync(dbs, folder_id):
    zip_filename = "Databases_Flurazide.zip"
    temp_zip_path = os.path.join(tempfile.gettempdir(), zip_filename)

    log.info(f"Creating combined backup: {temp_zip_path}")
    with zipfile.ZipFile(temp_zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
        for local_path, drive_filename in dbs:
            if not os.path.exists(local_path):
                log.warning(f"File not found: {local_path}, skipping.")
                continue
            zipf.write(local_path, arcname=drive_filename)
    log.info("All databases zipped successfully.")

    # Upload to Google Drive (overwrite if exists)
    service = build_drive_service()
    query = f"'{folder_id}' in parents and name='{zip_filename}' and trashed=false"
    res = service.files().list(q=query, fields="files(id, name)").execute()
    files = res.get("files", [])

    media = MediaFileUpload(temp_zip_path, mimetype="application/zip", resumable=True)

    try:
        if files:
            file_id = files[0]["id"]
            service.files().update(fileId=file_id, media_body=media).execute()
            log.success(f"Updated existing backup '{zip_filename}'.")
        else:
            meta = {"name": zip_filename, "parents": [folder_id]}
            service.files().create(body=meta, media_body=media, fields="id").execute()
            log.success(f"Created new backup '{zip_filename}'.")
    finally:
        # Force close the stream if possible (MediaFileUpload doesn't strictly require it but it helps)
        if 'media' in locals():
            del media 
        
        # Windows-safe deletion retry loop
        for i in range(5):
            try:
                if os.path.exists(temp_zip_path):
                    os.remove(temp_zip_path)
                break
            except OSError as e:
                if i == 4: # Last attempt
                    log.warning(f"Could not remove temp backup file after retries: {e}")
                time.sleep(0.5) # Wait for Windows to release the lock

async def backup_all_dbs_to_gdrive_env(dbs: list[tuple[str, str]], folder_id: str):
    """
    Combine multiple .db files into one .zip and upload (overwrite) it to Drive.
    dbs = [(local_path, drive_filename), ...]
    """
    await asyncio.to_thread(_backup_all_dbs_sync, dbs, folder_id)

def _restore_db_sync(local_path, drive_filename, folder_id):
    log.info(f"Restoring {drive_filename} -> {local_path}")
    service = build_drive_service()

    # Build query: if folder_id provided, search in it; otherwise search across Drive
    if folder_id:
        q = f"'{folder_id}' in parents and name='{drive_filename}' and trashed=false"
    else:
        q = f"name='{drive_filename}' and trashed=false"

    res = service.files().list(q=q, fields="files(id, name)").execute()
    files = res.get("files", [])
    if not files:
        log.warning(f"No backup found on Google Drive with name: {drive_filename}")
        return False

    file_id = files[0]["id"]
    request = service.files().get_media(fileId=file_id)
    # Use context manager so file handle is closed promptly (avoids Windows file-lock issues)
    try:
        with io.FileIO(local_path, 'wb') as fh:
            downloader = MediaIoBaseDownload(fh, request)
            done = False
            while not done:
                status, done = downloader.next_chunk()
        log.info(f"Restored database from Google Drive: {drive_filename}")
        return True
    except HttpError as e:
        log.exception(f"Failed to download {drive_filename}: {e}")
        return False
    except Exception as e:
        log.exception(f"Unexpected error while restoring {drive_filename}: {e}")
        return False

async def restore_db_from_gdrive_env(local_path, drive_filename, folder_id=None):
    """
    Download a file named drive_filename from Drive (optionally inside folder_id)
    and save it to local_path. Uses googleapiclient (same auth path as backup).
    """
    return await asyncio.to_thread(_restore_db_sync, local_path, drive_filename, folder_id)


def _restore_all_dbs_sync(folder_id, restore_map):
    zip_filename = "Databases_Flurazide.zip"
    service = build_drive_service()
    log.info(f"Searching for {zip_filename} in Google Drive folder {folder_id}...")

    query = f"'{folder_id}' in parents and name='{zip_filename}' and trashed=false"
    res = service.files().list(q=query, fields="files(id, name)").execute()
    files = res.get("files", [])

    if not files:
        log.warning(f"No backup found with name {zip_filename} on Google Drive.")
        return False

    file_id = files[0]["id"]
    temp_zip = os.path.join(tempfile.gettempdir(), zip_filename)
    request = service.files().get_media(fileId=file_id)
    # Use context manager so file handle is closed promptly (avoids Windows file-lock issues)
    try:
        with io.FileIO(temp_zip, 'wb') as fh:
            downloader = MediaIoBaseDownload(fh, request)

            done = False
            while not done:
                status, done = downloader.next_chunk()
                if status:
                    log.info(f"Download progress: {int(status.progress() * 100)}%")

        log.info(f"Downloaded {zip_filename}, extracting...")
        with zipfile.ZipFile(temp_zip, "r") as zipf:
            for member in zipf.namelist():
                if member in restore_map:
                    dest_path = restore_map[member]
                    zipf.extract(member, path=os.path.dirname(dest_path))
                    os.replace(os.path.join(os.path.dirname(dest_path), member), dest_path)
                    log.success(f"Restored {member} → {dest_path}")
                else:
                    log.warning(f"Skipping unknown file in ZIP: {member}")

        log.success("All databases restored successfully.")
        # Now safe to remove the temp file since file handle is closed
        try:
            os.remove(temp_zip)
        except OSError as e:
            log.warning(f"Could not remove temp backup file: {e}")
        return True

    except HttpError as e:
        log.exception(f"Failed to restore from Drive: {e}")
        return False
    except Exception as e:
        log.exception(f"Unexpected error during restore: {e}")
        return False

async def restore_all_dbs_from_gdrive_env(folder_id, restore_map: dict[str, str]):
    """
    Restore all databases from the fixed ZIP on Google Drive.

    restore_map = {
        "economy.db": ECONOMY_DB_PATH,
        "moderator.db": MODERATOR_DB_PATH
    }
    """
    return await asyncio.to_thread(_restore_all_dbs_sync, folder_id, restore_map)


# the next lines of code are nasty hacks becuase windows is shit
# Get the absolute path to the directory where this file is located
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Ensure 'data' directory exists relative to this file
DATA_DIR = os.path.join(BASE_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)

# Paths for databases (relative to script location)
ECONOMY_DB_PATH = os.path.join(DATA_DIR, "economy.db")
MODERATOR_DB_PATH = os.path.join(DATA_DIR, "moderator.db")

SHOP_ITEMS = [
    {"id": 1, "name": "Bragging Rights", "price": 10000, "effect": "Nothing. Just flex.", "uses_left": 1},
    {"id": 2, "name": "Financial Drain", "price": 5000, "effect": "Drains one percent of your balance per hour, I wonder where that money goes..", "uses_left": 1},
    {"id": 3, "name": "Bolt Cutters", "price": 3000, "effect": "Improves robbery success", "uses_left": 4},
    {"id": 4, "name": "Padlocked Wallet", "price": 2000, "effect": "Protects against robbery", "uses_left": 10},
    {"id": 5, "name": "Taser", "price": 3500, "effect": "Stuns robbers", "uses_left": 2},
    {"id": 6, "name": "Lucky Coin", "price": 1500, "effect": "Boosts gambling odds.. or just a really expensive paperweight", "uses_left": 4},
    {"id": 7, "name": "VIP Pass", "price": 50000, "effect": "Grants VIP access", "uses_left": 1},
    {"id": 8, "name": "Hackatron 9900", "price": 7000, "effect": "Increases heist efficiency", "uses_left": 5},
    {"id": 9, "name": "Resintantoinem Sample", "price": 4000, "effect": "Probaably a bad idea, increases heist efficiency but once effect wears off you'll be more susceptible", "uses_left": 1},
    {"id": 10, "name": "Loaded Gun", "price": 9000, "effect": "You remembered your 2nd amendment rights, self defense agaist robbers", "uses_left": 19},
    {"id": 11, "name": "Watermelon", "price": 500, "effect": "Doctors approve! Does nothing", "uses_left": 500},
]


class DatabaseManager:
    def __init__(self):
        self._economy_conn = None
        self._moderator_conn = None

    async def get_economy(self):
        if not self._economy_conn:
            self._economy_conn = await aiosqlite.connect(ECONOMY_DB_PATH)
            await self._economy_conn.execute("PRAGMA foreign_keys = ON")
        return self._economy_conn

    async def get_moderator(self):
        if not self._moderator_conn:
            self._moderator_conn = await aiosqlite.connect(MODERATOR_DB_PATH)
        return self._moderator_conn
    
    async def close(self):
        if self._economy_conn:
            await self._economy_conn.close()
        if self._moderator_conn:
            await self._moderator_conn.close()

db = DatabaseManager()

# Initialize database tables on startup
async def init_databases():
    """Initialize database tables using aiosqlite"""
    try:
        # Initialize economy database
        conn = await db.get_economy()
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT NOT NULL,
            balance INTEGER NOT NULL DEFAULT 0
        )
        """)
        
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS user_items (
            user_id INTEGER,
            item_id TEXT,
            item_name TEXT NOT NULL,
            uses_left INTEGER DEFAULT 0,
            effect_modifier INTEGER DEFAULT 0,
            PRIMARY KEY (user_id, item_id)
        )
        """)
        await conn.commit()
        log.database("Economy database initialized successfully")
    
        # Initialize moderator database with single cases table
        mod_conn = await db.get_moderator()
        # Use an internal autoincrement id (case_id) and a per-guild case_number.
        # case_number is generated per-guild at insert time so each server has its own counting.
        await mod_conn.execute("""
        CREATE TABLE IF NOT EXISTS cases (
            case_id INTEGER PRIMARY KEY AUTOINCREMENT,
            case_number INTEGER NOT NULL,
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            username TEXT,
            reason TEXT NOT NULL,
            action_type TEXT NOT NULL,
            timestamp INTEGER NOT NULL,
            moderator_id INTEGER NOT NULL,
            expiry INTEGER DEFAULT 0,
            UNIQUE (guild_id, case_number)
        )
        """)
        # Create index on guild_id for faster queries
        await mod_conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_cases_guild_id ON cases(guild_id)
        """)
        await mod_conn.commit()
        log.database("Moderator database initialized successfully")
    
        log.success("Databases initialized successfully")
    except Exception as e:
        log.critical(f"Failed while initializing databases: {e}")
        # Re-raise so caller (main.py) can still handle/fail, but we've logged full traceback
        raise

@log_db_call
async def modify_robber_multiplier(user_id, change, duration=None):
    """Modifies the user's robbery success/failure rate"""
    current_modifier = await get_robbery_modifier(user_id)  # Get current modifier
    new_modifier = max(min(current_modifier + change, 100), -100)  # Cap between -100% and +100%

    # Update the database
    conn = await db.get_economy()
    await conn.execute("UPDATE user_items SET effect_modifier = ? WHERE user_id = ?",
                      (new_modifier, user_id))
    await conn.commit()

    log.trace(f"Updated robbery modifier for {user_id}: {new_modifier}%")

    # If it's temporary (like Resin Sample), schedule decay
    if duration:
        asyncio.create_task(schedule_effect_decay(user_id, current_modifier, duration))

@log_db_call
async def get_robbery_modifier(user_id):
    """Gets the total robbery modifier for a user (from items)"""
    conn = await db.get_economy()
    async with conn.execute("SELECT SUM(effect_modifier) FROM user_items WHERE user_id = ?", (user_id,)) as cursor:
        result = await cursor.fetchone()
        return result[0] if result and result[0] else 0  # Default to 0 modifier

@log_db_call
async def schedule_effect_decay(user_id, original_value, duration):
    """Waits for the effect duration to expire and then reverts the modifier"""
    await asyncio.sleep(duration)  # Wait X seconds
    conn = await db.get_economy()
    await conn.execute("UPDATE user_items SET effect_modifier = ? WHERE user_id = ?",
                      (original_value, user_id))
    await conn.commit()

    log.trace(f"Restored robbery modifier for {user_id} to {original_value}%")

# ----------- Economy Functions -----------

@log_db_call
async def update_balance(user_id, amount):
    """Updates user balance, clamped to DEBT_FLOOR and synced to backup."""
    log.trace(f"Updating balance for {user_id}: {amount} coins")
    conn = await db.get_economy()
    await conn.execute("""
        UPDATE users
        SET balance = CASE
            WHEN balance + ? < ?
                THEN ?
            ELSE balance + ?
        END
        WHERE user_id = ?
    """, (amount, DEBT_FLOOR, DEBT_FLOOR, amount, user_id))
    await conn.commit()

@log_db_call
async def get_balance(user_id):
    """ Fetches user balance """
    log.trace(f"Getting balance for {user_id}")
    conn = await db.get_economy()
    async with conn.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,)) as cursor:
        result = await cursor.fetchone()
        return result[0] if result else 0

@log_db_call
async def add_user(user_id, username):
    """ Adds a user to the economy database if they don't exist """
    log.trace(f"Adding user {user_id} in economy database, {username}")
    conn = await db.get_economy()
    await conn.execute("INSERT OR IGNORE INTO users (user_id, username, balance) VALUES (?, ?, 0)", (user_id, username))
    await conn.commit()

# ----------- Item Handling Functions -----------

@log_db_call
async def add_user_item(user_id, item_id, item_name, uses_left=1, effect_modifier=0):
    """ Adds an item to the user's inventory """
    log.trace(f"Adding item {item_name} (ID: {item_id}) to {user_id}'s inventory")
    conn = await db.get_economy()
    await conn.execute("""
        INSERT INTO user_items (user_id, item_id, item_name, uses_left, effect_modifier) 
        VALUES (?, ?, ?, ?, ?) 
        ON CONFLICT(user_id, item_id) DO UPDATE 
        SET uses_left = uses_left + ?""",
        (user_id, item_id, item_name, uses_left, effect_modifier, uses_left)
    )
    await conn.commit()

@log_db_call
async def get_user_items(user_id):
    """ Fetches all items a user owns """
    conn = await db.get_economy()
    async with conn.execute("SELECT item_id, item_name, uses_left FROM user_items WHERE user_id = ?", (user_id,)) as cursor:
        items = await cursor.fetchall()
        return [{"item_id": row[0], "item_name": row[1], "uses_left": row[2]} for row in items] if items else []

@log_db_call
async def remove_item_from_user(user_id, item_id):
    """Removes an item completely from the user's inventory."""
    conn = await db.get_economy()
    await conn.execute("DELETE FROM user_items WHERE user_id = ? AND item_id = ?", (user_id, item_id))
    await conn.commit()

@log_db_call
async def update_item_uses(user_id, item_id, uses_left):
    """Updates the number of uses left for a user's item."""
    conn = await db.get_economy()
    await conn.execute("UPDATE user_items SET uses_left = ? WHERE user_id = ? AND item_id = ?", (uses_left, user_id, item_id))
    await conn.commit()

@log_db_call
async def add_item_to_user(user_id, item_id, item_name, uses_left=1, effect_modifier=0):
    """Adds an item to the user's inventory or updates uses if it exists."""
    conn = await db.get_economy()
    await conn.execute("""
        INSERT INTO user_items (user_id, item_id, item_name, uses_left, effect_modifier)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(user_id, item_id) DO UPDATE SET uses_left = user_items.uses_left + ?
    """, (user_id, item_id, item_name, uses_left, effect_modifier, uses_left))
    await conn.commit()

# ----------- Shop Functions -----------

@log_db_call
async def buy_item(user_id, item_id, item_name, price, uses_left=1, effect_modifier=0):
    """Buys an item from the shop and deducts balance, using aiosqlite for all operations."""
    log.trace(f"User {user_id} is buying {item_name} for {price} coins")

    conn = await db.get_economy()
    # Check if user exists
    async with conn.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,)) as cursor:
        user = await cursor.fetchone()
        if not user:
            return False  # User does not exist

    current_balance = user[0]
    if current_balance < price:
        return False  # Not enough money

    # Deduct balance
    await conn.execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (price, user_id))

    # Add or update item in inventory
    await conn.execute("""
        INSERT INTO user_items (user_id, item_id, item_name, uses_left, effect_modifier)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(user_id, item_id) DO UPDATE SET uses_left = user_items.uses_left + ?
    """, (user_id, item_id, item_name, uses_left, effect_modifier, uses_left))

    await conn.commit()
    return True


# ----------- Special Item Effects -----------

@log_db_call
async def use_item(user_id, item_id):
    """Handles item use and applies effects dynamically."""
    conn = await db.get_economy()
    # Fetch user items
    async with conn.execute("SELECT uses_left FROM user_items WHERE user_id = ? AND item_id = ?", (user_id, item_id)) as cursor:
        result = await cursor.fetchone()
        
        if not result:
            return f"❌ You don't have this item!"

        uses_left = result[0]
        if uses_left <= 0:
            return f"❌ You have no uses left for this item!"

        # Fetch item details from hardcoded shop list
        item_data = next((item for item in SHOP_ITEMS if item["id"] == item_id), None)
        if not item_data:
            return f"❌ Item does not exist!"

        # Handle last use case
        last_use_warning = ""
        if uses_left == 1:
            last_use_warning = f"⚠️ **This is the last use of your {item_data['name']}!**\n"

        # Define item effects dynamically
        item_effects = {
            1: {"robbery_modifier": 0, "uses": 1},        # Bragging Rights: no effect, 1 use
            2: {"robbery_modifier": 20, "uses": 3},      # Robber's Mask: +20% robbery, 3 uses
            3: {"robbery_modifier": 50, "uses": 4},      # Bolt Cutters: +50% robbery, 4 uses
            4: {"robbery_modifier": -40, "uses": 10},    # Padlocked Wallet: -40% robbery, 10 uses
            5: {"robbery_modifier": -90, "taser": True, "uses": 2},               # Taser: blocks robbery, 2 uses
            6: {"Gambling_odds_mul": 0, "uses": 4},      # Lucky coin: placebo effect goes insane, 4 uses
            8: {"robbery_modifier": 75, "uses": 5},      # Hackatron 9900™: +75% robbery, 5 uses
            9: {  # Resin Sample: +100% robbery, then -40% after effect wears off
                "robbery_modifier": 100,
                "temporary_effect": -40,
                "duration": 3600,  # 1 hour in seconds
                "uses": 1
            },
            10: {"gun_defense": True, "uses": 8},        # Loaded Gun: blocks robbery, 8 uses
            11: {"uses": 500}                            # Watermelon: no effect, 500 uses
        }

        # Apply effect if item has one
        effect_applied = ""
        if item_id in item_effects:
            effect_data = item_effects[item_id]

            # Apply Robbery Modifiers
            if "robbery_modifier" in effect_data:
                await modify_robber_multiplier(user_id, effect_data["robbery_modifier"])
                effect_applied = f"🔧 **Your robbery success rate changed by {effect_data['robbery_modifier']}%!**"

            # Apply temporary effects (like Resin Sample)
            if "temporary_effect" in effect_data:
                await schedule_effect_decay(user_id, effect_data["temporary_effect"], effect_data["duration"])

            # Apply defensive effects
            if "taser" in effect_data:
                await modify_robber_multiplier(user_id, effect_data["robbery_modifier"])
                effect_applied = "⚡ **You are now protected from robbery for one attempt!**"
            
            if "gun_defense" in effect_data:
                effect_applied = "🔫 **You are armed. Good luck, robber.**"


@log_db_call
async def check_gun_defense(victim_id):
    conn = await db.get_economy()
    async with conn.execute("SELECT uses_left FROM user_items WHERE user_id = ? AND item_id = 10", (victim_id,)) as cursor:
        result = await cursor.fetchone()
        return result[0] if result and result[0] > 0 else 0

@log_db_call
async def decrement_gun_use(victim_id):
    conn = await db.get_economy()
    await conn.execute("UPDATE user_items SET uses_left = uses_left - 1 WHERE user_id = ? AND item_id = 10 AND uses_left > 0", (victim_id,))
    await conn.commit()

# ----------- Moderator logging functions -----------

# Now using a single cases table with guild_id column instead of per-guild tables
@log_mod_call
async def insert_case(guild_id, user_id, username, reason, action_type, moderator_id, timestamp=None, expiry=0):
    """Insert a case into the unified cases table with a per-guild case_number."""
    if timestamp is None:
        timestamp = int(time.time())

    conn = await db.get_moderator()
    # Ensure atomicity so two concurrent inserts for the same guild can't get the same case_number
    await conn.execute("BEGIN IMMEDIATE")
    # Get current max case_number for the guild
    async with conn.execute("SELECT MAX(case_number) FROM cases WHERE guild_id = ?", (guild_id,)) as cursor:
        row = await cursor.fetchone()
        next_case_number = (row[0] or 0) + 1

    # Insert including computed per-guild case_number
    await conn.execute("""
        INSERT INTO cases (case_number, guild_id, user_id, username, reason, action_type, timestamp, moderator_id, expiry)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (next_case_number, guild_id, user_id, username, reason, action_type, timestamp, moderator_id, expiry))

    await conn.commit()

    # Return the per-guild case number so callers can reference it
    return next_case_number

@log_mod_call
async def get_cases_for_guild(guild_id, limit=50, offset=0):
    """Get cases for a specific guild"""
    conn = await db.get_moderator()
    async with conn.execute("""
        SELECT case_number, user_id, username, reason, action_type, timestamp, moderator_id, expiry
        FROM cases
        WHERE guild_id = ?
        ORDER BY case_number DESC
        LIMIT ? OFFSET ?
    """, (guild_id, limit, offset)) as cursor:
        return await cursor.fetchall()

@log_mod_call
async def get_cases_for_user(guild_id, user_id):
    """Get cases for a specific user in a guild"""
    conn = await db.get_moderator()
    async with conn.execute("""
        SELECT case_number, reason, action_type, timestamp, moderator_id, expiry
        FROM cases
        WHERE guild_id = ? AND user_id = ?
        ORDER BY case_number DESC
    """, (guild_id, user_id)) as cursor:
        return await cursor.fetchall()

@log_mod_call
async def get_case(guild_id, case_number):
    """Get a specific case by case_number and guild_id"""
    conn = await db.get_moderator()
    async with conn.execute("""
        SELECT case_number, user_id, username, reason, action_type, timestamp, moderator_id, expiry
        FROM cases
        WHERE guild_id = ? AND case_number = ?
    """, (guild_id, case_number)) as cursor:
        return await cursor.fetchone()

@log_mod_call
async def remove_case(guild_id, case_number):
    """Remove a case by case_number and guild_id"""
    conn = await db.get_moderator()
    await conn.execute("DELETE FROM cases WHERE guild_id = ? AND case_number = ?", (guild_id, case_number))
    await conn.commit()

@log_mod_call
async def edit_case_reason(guild_id, case_number, new_reason):
    """Edit the reason of a specific case"""
    conn = await db.get_moderator()
    await conn.execute("UPDATE cases SET reason = ? WHERE guild_id = ? AND case_number = ?",
                      (new_reason, guild_id, case_number))
    await conn.commit()

# Do not log because it spams terminal like hell
async def get_expired_cases(guild_id, action_type, now=None):
    """Get expired cases for a guild and action type. If guild_id is None, returns all expired cases."""
    if now is None:
        now = int(time.time())
    conn = await db.get_moderator()
    if guild_id is None:
        # Get all expired cases across all guilds - return (guild_id, user_id)
        async with conn.execute("""
            SELECT guild_id, user_id FROM cases
            WHERE action_type = ? AND expiry > 0 AND expiry <= ?
        """, (action_type, now)) as cursor:
            return await cursor.fetchall()
    else:
        # Return (case_number, user_id) for specific guild
        async with conn.execute("""
            SELECT case_number, user_id FROM cases
            WHERE guild_id = ? AND action_type = ? AND expiry > 0 AND expiry <= ?
        """, (guild_id, action_type, now)) as cursor:
            return await cursor.fetchall()

BACKUP_FOLDER_ID = BACKUP_GDRIVE_FOLDER_ID


async def periodic_backup(interval_hours=1):
    log.info("Started periodic_backup task")
    while True:
        try:
            await backup_all_dbs_to_gdrive_env([
                (ECONOMY_DB_PATH, "economy.db"),
                (MODERATOR_DB_PATH, "moderator.db")
            ], BACKUP_FOLDER_ID)
        except Exception as e:
            log.warning(f"Periodic backup fail: {e}")
            
        log.success("Backup task completed.")
        await asyncio.sleep(interval_hours * 3600)

# Register a global uncaught-exception hook to make sure fatal crashes are logged with traceback
# dont ask why this is in database.py and not main.py i'm too lazy to move it fuck you

def _log_unhandled_exception(exc_type, exc_value, exc_tb):
    # Let KeyboardInterrupt behave normally
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_tb)
        return
    log.critical("Uncaught exception", exc_info=(exc_type, exc_value, exc_tb))

sys.excepthook = _log_unhandled_exception
