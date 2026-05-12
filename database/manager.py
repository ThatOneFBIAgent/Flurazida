# database/manager.py
# Database management logic for Flurazide
# Ported from src/database.py with bug fixes and modular structure.

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

# Third-Party Imports
import aiosqlite
from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload

# Local Imports
from logging_modules.custom_logger import get_logger
from extraconfig import BACKUP_GDRIVE_FOLDER_ID, BOT_OWNER
from database.items import SHOP_ITEMS, ITEM_EFFECTS

log = get_logger()

# ===================== Constants =====================
DEBT_FLOOR = -1000

# Path handling - DB files live in src/data to keep backward compat with existing data.
# On Railway, CWD is /app. Locally, it's the project root. Both have data.
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # project root
DATA_DIR = os.path.join(BASE_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)

ECONOMY_DB_PATH = os.path.join(DATA_DIR, "economy.db")
MODERATOR_DB_PATH = os.path.join(DATA_DIR, "moderator.db")

# ===================== Decorators =====================
def log_db_call(func):
    @wraps(func)
    async def wrapper(*args, **kwargs):
        log.database(f"ECON DB CALL: {func.__name__} called with args={args}, kwargs={kwargs}")
        return await func(*args, **kwargs)
    return wrapper

def log_mod_call(func):
    @wraps(func)
    async def wrapper(*args, **kwargs):
        log.database(f"MOD DB CALL: {func.__name__} called with args={args}, kwargs={kwargs}")
        return await func(*args, **kwargs)
    return wrapper

# ===================== Google Drive Backup Settings =====================
TOKEN_ENV = "DRIVE_TOKEN_B64"
CREDENTIALS_ENV = "DRIVE_CREDENTIALS_B64"
SCOPES = ['https://www.googleapis.com/auth/drive.file']

def load_creds_local():
    with open("token.json", "r") as f:
        token_info = json.load(f)
    return Credentials.from_authorized_user_info(token_info, SCOPES)

def load_creds_from_env():
    # Try the user's requested 'DRIVE_' prefix first, then fallback to 'GDRIVE_'
    token_b64 = os.environ.get(TOKEN_ENV) or os.environ.get(f"G{TOKEN_ENV}")
    if not token_b64:
        raise RuntimeError(f"Neither {TOKEN_ENV} nor G{TOKEN_ENV} found in environment.")
    token_json = base64.b64decode(token_b64).decode()
    token_info = json.loads(token_json)
    if ("client_id" not in token_info or "client_secret" not in token_info):
        creds_b64 = os.environ.get(CREDENTIALS_ENV) or os.environ.get(f"G{CREDENTIALS_ENV}")
        if creds_b64:
            creds_json = base64.b64decode(creds_b64).decode()
            creds_info = json.loads(creds_json)
            client_block = creds_info.get("installed") or creds_info.get("web") or {}
            token_info.setdefault("client_id", client_block.get("client_id"))
            token_info.setdefault("client_secret", client_block.get("client_secret"))
            token_info.setdefault("token_uri", client_block.get("token_uri") or "https://oauth2.googleapis.com/token")
    creds = Credentials.from_authorized_user_info(token_info, SCOPES)
    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            log.network("Refreshed OAuth access token successfully.")
        except Exception as e:
            log.network(f"Failed to refresh token: {e}. Token may be revoked; you'll need to re-run the local helper.")
    return creds

def build_drive_service():
    if os.getenv("RAILWAY_PROJECT_ID"):
        log.trace("Running on Railway, using env-based credentials.")
        creds = load_creds_from_env()
    else:
        try:
            creds = load_creds_local()
        except Exception:
            log.info("token.json not found, falling back to env-based credentials.")
            creds = load_creds_from_env()
    return build("drive", "v3", credentials=creds, cache_discovery=False)

# ===================== Google Drive Backup / Restore =====================
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
        if 'media' in locals():
            del media
        for i in range(5):
            try:
                if os.path.exists(temp_zip_path):
                    os.remove(temp_zip_path)
                break
            except OSError as e:
                if i == 4:
                    log.warning(f"Could not remove temp backup file after retries: {e}")
                time.sleep(0.5)

async def backup_all_dbs_to_gdrive_env(dbs: list[tuple[str, str]], folder_id: str):
    """Combine multiple .db files into one .zip and upload (overwrite) it to Drive."""
    await asyncio.to_thread(_backup_all_dbs_sync, dbs, folder_id)

def _restore_db_sync(local_path, drive_filename, folder_id):
    log.info(f"Restoring {drive_filename} -> {local_path}")
    service = build_drive_service()
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
                    log.success(f"Restored {member} -> {dest_path}")
                else:
                    log.warning(f"Skipping unknown file in ZIP: {member}")
        log.success("All databases restored successfully.")
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
    """Restore all databases from the fixed ZIP on Google Drive."""
    return await asyncio.to_thread(_restore_all_dbs_sync, folder_id, restore_map)

# ===================== Database Manager =====================
class DatabaseManager:
    def __init__(self):
        self._economy_conn = None
        self._moderator_conn = None
        self._init_lock = asyncio.Lock()
        self._economy_lock = asyncio.Lock()
        self._moderator_lock = asyncio.Lock()
        self.health_ok = True
        self._bot = None  # Set by bot.py during startup for DM notifications

    def set_bot(self, bot):
        """Called during bot startup so we can DM the owner on DB failure."""
        self._bot = bot

    async def _notify_owner(self, message: str):
        """DM the bot owner about a critical DB issue."""
        if not self._bot:
            return
        try:
            owner = await self._bot.fetch_user(BOT_OWNER)
            await owner.send(f"🚨 **Database Alert**\n{message}")
        except Exception as e:
            log.error(f"Failed to DM owner about DB issue: {e}")

    async def get_economy(self):
        if not self._economy_conn:
            async with self._init_lock:
                if not self._economy_conn:
                    try:
                        self._economy_conn = await aiosqlite.connect(ECONOMY_DB_PATH)
                        await self._economy_conn.execute("PRAGMA foreign_keys = ON")
                    except Exception as e:
                        self.health_ok = False
                        msg = f"CRITICAL: Failed to connect to Economy database at {ECONOMY_DB_PATH}: {e}"
                        log.critical(msg)
                        await self._notify_owner(msg)
                        raise
        return self._economy_conn

    async def get_moderator(self):
        if not self._moderator_conn:
            async with self._init_lock:
                if not self._moderator_conn:
                    try:
                        self._moderator_conn = await aiosqlite.connect(MODERATOR_DB_PATH)
                    except Exception as e:
                        self.health_ok = False
                        msg = f"CRITICAL: Failed to connect to Moderator database at {MODERATOR_DB_PATH}: {e}"
                        log.critical(msg)
                        await self._notify_owner(msg)
                        raise
        return self._moderator_conn

    async def close(self):
        if self._economy_conn:
            await self._economy_conn.close()
        if self._moderator_conn:
            await self._moderator_conn.close()

db = DatabaseManager()

# ===================== Init =====================
async def init_databases():
    """Initialize database tables using aiosqlite"""
    try:
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

        mod_conn = await db.get_moderator()
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
        await mod_conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_cases_guild_id ON cases(guild_id)
        """)
        await mod_conn.commit()
        log.database("Moderator database initialized successfully")
        log.success("Databases initialized successfully")
    except Exception as e:
        log.critical(f"Failed while initializing databases: {e}")
        raise

# ===================== Robbery Modifier =====================
@log_db_call
async def modify_robber_multiplier(user_id, change, duration=None):
    """
    Modifies the user's robbery success/failure rate.

    Args:
        user_id (int): The user's ID
        change (int): The amount to add (or subtract if negative)
        duration (int): Optional duration in seconds for temporary effects
    """
    current_modifier = await get_robbery_modifier(user_id)
    new_modifier = max(min(current_modifier + change, 100), -100)
    conn = await db.get_economy()
    await conn.execute("UPDATE user_items SET effect_modifier = ? WHERE user_id = ?",
                      (new_modifier, user_id))
    await conn.commit()
    log.trace(f"Updated robbery modifier for {user_id}: {new_modifier}%")
    if duration:
        asyncio.create_task(schedule_effect_decay(user_id, current_modifier, duration))

@log_db_call
async def get_robbery_modifier(user_id):
    """Gets the total robbery modifier for a user (from items)."""
    conn = await db.get_economy()
    async with conn.execute("SELECT SUM(effect_modifier) FROM user_items WHERE user_id = ?", (user_id,)) as cursor:
        result = await cursor.fetchone()
        return result[0] if result and result[0] else 0

@log_db_call
async def schedule_effect_decay(user_id, original_value, duration):
    """Waits for the effect duration to expire and then reverts the modifier."""
    await asyncio.sleep(duration)
    conn = await db.get_economy()
    await conn.execute("UPDATE user_items SET effect_modifier = ? WHERE user_id = ?",
                      (original_value, user_id))
    await conn.commit()
    log.trace(f"Restored robbery modifier for {user_id} to {original_value}%")

# ===================== Economy Functions =====================
@log_db_call
async def update_balance(user_id, amount):
    """
    Updates user balance, clamped to DEBT_FLOOR.

    Args:
        user_id (int): The user's ID
        amount (int): The amount to add (or subtract if negative)
    """
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
async def atomic_deduct(user_id, amount):
    """
    Atomically deducts a positive amount from user's balance.
    Fails (returns False) if their balance is below 0 or if removing it would put them in debt (below 0).
    Allows running gambling commands concurrently without race conditions over funds.
    """
    conn = await db.get_economy()
    # Check current balance first to guarantee we only deduct if strictly positive balance >= amount
    async with conn.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,)) as cursor:
        result = await cursor.fetchone()
        if not result or result[0] < amount:
            return False

    # Perform atomic update. 'balance >= amount' ensures no debt caused.
    async with conn.execute(
        "UPDATE users SET balance = balance - ? WHERE user_id = ? AND balance >= ?",
        (amount, user_id, amount)
    ) as cursor:
        await conn.commit()
        return cursor.rowcount > 0

@log_db_call
async def get_balance(user_id):
    """Fetches user balance."""
    log.trace(f"Getting balance for {user_id}")
    conn = await db.get_economy()
    async with conn.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,)) as cursor:
        result = await cursor.fetchone()
        return result[0] if result else 0

@log_db_call
async def add_user(user_id, username):
    """Adds a user to the economy database if they don't exist."""
    log.trace(f"Adding user {user_id} in economy database, {username}")
    conn = await db.get_economy()
    async with conn.execute("SELECT 1 FROM users WHERE user_id = ?", (user_id,)) as cursor:
        exists = await cursor.fetchone()
    if exists:
        return
    await conn.execute(
        "INSERT INTO users (user_id, username, balance) VALUES (?, ?, 0)",
        (user_id, username)
    )
    await conn.commit()

@log_db_call
async def get_total_economy_sum():
    """Calculates the sum of all non-negative user balances in the economy."""
    conn = await db.get_economy()
    async with conn.execute("SELECT SUM(balance) FROM users WHERE balance > 0") as cursor:
        result = await cursor.fetchone()
        return result[0] if result and result[0] else 0

# ===================== Item Handling Functions =====================
@log_db_call
async def add_user_item(user_id, item_id, item_name, uses_left=1, effect_modifier=0):
    """Adds an item to the user's inventory."""
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
    """Fetches all items a user owns."""
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

# ===================== Shop Functions =====================
@log_db_call
async def buy_item(user_id, item_id, item_name, price, uses_left=1, effect_modifier=0):
    """Buys an item from the shop and deducts balance."""
    log.trace(f"User {user_id} is buying {item_name} for {price} coins")
    conn = await db.get_economy()
    async with conn.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,)) as cursor:
        user = await cursor.fetchone()
        if not user:
            return False
    current_balance = user[0]
    if current_balance < price:
        return False
    async with conn.execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (price, user_id)):
        await conn.commit()
    async with conn.execute("""
        INSERT INTO user_items (user_id, item_id, item_name, uses_left, effect_modifier)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(user_id, item_id) DO UPDATE SET uses_left = user_items.uses_left + ?
    """, (user_id, item_id, item_name, uses_left, effect_modifier, uses_left)):
        await conn.commit()
    return True

# ===================== Special Item Effects =====================
@log_db_call
async def use_item(user_id, item_id):
    """
    Handles item use and applies effects dynamically.
    FIX: Now properly decrements uses, removes exhausted items, and returns a message.

    Args:
        user_id (int): The user's ID
        item_id (int): The item's ID

    Returns:
        str: A message describing the result of using the item.
    """
    conn = await db.get_economy()
    async with conn.execute("SELECT uses_left FROM user_items WHERE user_id = ? AND item_id = ?", (user_id, item_id)) as cursor:
        result = await cursor.fetchone()

    if not result:
        return "❌ You don't have this item!"

    uses_left = result[0]
    if uses_left <= 0:
        return "❌ You have no uses left for this item!"

    item_data = next((item for item in SHOP_ITEMS if item["id"] == item_id), None)
    if not item_data:
        return "❌ Failed to load item details."

    # Handle last use case
    last_use_warning = ""
    if uses_left == 1:
        last_use_warning = f"⚠️ **This is the last use of your {item_data['name']}!**\n"

    # FIX: Decrement uses
    new_uses = uses_left - 1
    if new_uses <= 0:
        await remove_item_from_user(user_id, item_id)
    else:
        await update_item_uses(user_id, item_id, new_uses)

    # Apply effect if item has one
    effect_applied = f"Used **{item_data['name']}** ({new_uses} uses remaining)."

    if item_id in ITEM_EFFECTS:
        effect_data = ITEM_EFFECTS[item_id]

        # Apply Robbery Modifiers
        if "robbery_modifier" in effect_data and not effect_data.get("taser") and not effect_data.get("gun_defense"):
            mod_val = effect_data["robbery_modifier"]
            duration = effect_data.get("duration")
            await modify_robber_multiplier(user_id, mod_val, duration=duration)
            effect_applied = f"🔧 **Your robbery success rate changed!**"

        # Apply temporary effects (like Resin Sample) — handled inside modify_robber_multiplier via duration
        if "temporary_effect" in effect_data:
            effect_applied += f"\n⏳ *Effect will decay after {effect_data.get('duration', 0) // 60} minutes.*"

        # Apply defensive effects
        if effect_data.get("taser"):
            await modify_robber_multiplier(user_id, effect_data["robbery_modifier"])
            effect_applied = "⚡ **You are now protected from robbery for one attempt!**"

        if effect_data.get("gun_defense"):
            effect_applied = "🔫 **You are armed. Good luck, robber.**"

        if effect_data.get("drain_percent"):
            effect_applied = "💸 **Financial Drain activated!** Your balance will slowly decay..."

        if effect_data.get("gambling_placebo"):
            effect_applied = "🪙 **Lucky Coin activated!** ...you feel luckier. (Placebo effect is real!)"

    # FIX: Actually return the message
    return f"{last_use_warning}{effect_applied}"

@log_db_call
async def check_gun_defense(victim_id):
    """Checks if a user has a gun defense item."""
    conn = await db.get_economy()
    async with conn.execute("SELECT uses_left FROM user_items WHERE user_id = ? AND item_id = 10", (victim_id,)) as cursor:
        result = await cursor.fetchone()
        return result[0] if result and result[0] > 0 else 0

@log_db_call
async def decrement_gun_use(victim_id):
    """Decrements the uses left for a user's gun defense item."""
    conn = await db.get_economy()
    await conn.execute("UPDATE user_items SET uses_left = uses_left - 1 WHERE user_id = ? AND item_id = 10 AND uses_left > 0", (victim_id,))
    await conn.commit()

# ===================== Moderator Logging Functions =====================
@log_mod_call
async def insert_case(guild_id, user_id, username, reason, action_type, moderator_id, timestamp=None, expiry=0):
    """
    Insert a moderation case into the database.
    Returns the new case number.
    """
    if timestamp is None:
        timestamp = int(time.time())
    
    # Ensure reason is not None as per schema
    if reason is None:
        reason = "No reason provided"

    async with db._moderator_lock:
        conn = await db.get_moderator()
        await conn.execute("BEGIN IMMEDIATE")
        try:
            async with conn.execute("SELECT MAX(case_number) FROM cases WHERE guild_id = ?", (guild_id,)) as cursor:
                row = await cursor.fetchone()
            next_case_number = (row[0] or 0) + 1
            await conn.execute("""
                INSERT INTO cases (case_number, guild_id, user_id, username, reason, action_type, timestamp, moderator_id, expiry)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (next_case_number, guild_id, user_id, username, reason, action_type, timestamp, moderator_id, expiry))
            await conn.commit()
            return next_case_number
        except Exception as e:
            await conn.rollback()
            log.error(f"Error in insert_case: {e}")
            raise

@log_mod_call
async def get_cases_for_guild(guild_id, limit=50, offset=0):
    """Get cases for a specific guild."""
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
    """Get cases for a specific user in a guild."""
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
    """Get a specific case by case_number and guild_id."""
    conn = await db.get_moderator()
    async with conn.execute("""
        SELECT case_number, user_id, username, reason, action_type, timestamp, moderator_id, expiry
        FROM cases
        WHERE guild_id = ? AND case_number = ?
    """, (guild_id, case_number)) as cursor:
        return await cursor.fetchone()

@log_mod_call
async def remove_case(guild_id, case_number):
    """Remove a case by case_number and guild_id."""
    conn = await db.get_moderator()
    await conn.execute("DELETE FROM cases WHERE guild_id = ? AND case_number = ?", (guild_id, case_number))
    await conn.commit()

@log_mod_call
async def edit_case_reason(guild_id, case_number, new_reason):
    """Edit the reason of a specific case."""
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
        async with conn.execute("""
            SELECT guild_id, user_id FROM cases
            WHERE action_type = ? AND expiry > 0 AND expiry <= ?
        """, (action_type, now)) as cursor:
            return await cursor.fetchall()
    else:
        async with conn.execute("""
            SELECT case_number, user_id FROM cases
            WHERE guild_id = ? AND action_type = ? AND expiry > 0 AND expiry <= ?
        """, (guild_id, action_type, now)) as cursor:
            return await cursor.fetchall()

# ===================== Periodic Backup =====================
BACKUP_FOLDER_ID = BACKUP_GDRIVE_FOLDER_ID

async def periodic_backup(interval_hours=1):
    """Periodically back up the economy and moderator databases to Google Drive."""
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

# ===================== Global Exception Hook =====================
def _log_unhandled_exception(exc_type, exc_value, exc_tb):
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_tb)
        return
    log.critical("Uncaught exception", exc_info=(exc_type, exc_value, exc_tb))

sys.excepthook = _log_unhandled_exception
