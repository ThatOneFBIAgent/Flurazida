# database/manager.py
# Database management logic for Flurazide
# Ported from src/database.py with bug fixes and modular structure.

# Standard Library Imports
import asyncio
import random
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
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS user_claims (
            user_id INTEGER NOT NULL,
            claim_type TEXT NOT NULL,
            last_claim INTEGER NOT NULL,
            streak INTEGER DEFAULT 0,
            PRIMARY KEY (user_id, claim_type)
        )
        """)
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS user_effects (
            user_id INTEGER NOT NULL,
            effect_type TEXT NOT NULL,
            expiry INTEGER NOT NULL,
            modifier INTEGER DEFAULT 0,
            PRIMARY KEY (user_id, effect_type)
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
async def get_robbery_modifier(user_id: int) -> float:
    """
    Returns the robber's total offense modifier as a float offset
    (e.g. 0.50 = +50% success chance). Reads directly from ITEM_EFFECTS
    for items the user currently owns.
    """
    conn = await db.get_economy()
    async with conn.execute(
        "SELECT item_id FROM user_items WHERE user_id = ? AND uses_left > 0", (user_id,)
    ) as cursor:
        rows = await cursor.fetchall()
    total = 0
    for (item_id,) in rows:
        effects = ITEM_EFFECTS.get(int(item_id), {})
        # Skip victim-only items — they don't boost the robber
        if not effects.get("taser") and not effects.get("gun_defense") and not effects.get("coffee_defense") and not effects.get("expired_fish") and effects.get("robbery_modifier", 0) > 0:
            total += effects.get("robbery_modifier", 0)
            
    # Apply status effects on robber
    if await has_active_effect(user_id, "cone_penalty"):
        total -= 30
        
    log.trace(f"Robbery modifier for {user_id}: {total}% ({total / 100:+.2f})")
    return total / 100

@log_db_call
async def get_victim_rob_modifier(victim_id: int) -> float:
    """
    Returns the victim's passive defensive modifier as a positive float
    (e.g. 0.50 = reduces robber's success chance by 50%).
    Automatically decrements Padlocked Wallet (item 4), Coffee Mug (item 13), and Expired fish (item 26) uses on call.
    """
    conn = await db.get_economy()
    async with conn.execute(
        "SELECT item_id, uses_left FROM user_items WHERE user_id = ? AND item_id IN (4, 13, 26) AND uses_left > 0",
        (victim_id,)
    ) as cursor:
        rows = await cursor.fetchall()
        
    total_penalty = 0.0
    for item_id_str, uses_left in rows:
        item_id = int(item_id_str)
        new_uses = uses_left - 1
        if new_uses <= 0:
            await conn.execute(
                "DELETE FROM user_items WHERE user_id = ? AND item_id = ?", (victim_id, item_id_str)
            )
            log.trace(f"Defensive item {item_id} exhausted for victim {victim_id}")
        else:
            await conn.execute(
                "UPDATE user_items SET uses_left = ? WHERE user_id = ? AND item_id = ?",
                (new_uses, victim_id, item_id_str)
            )
            
        penalty = abs(ITEM_EFFECTS.get(item_id, {}).get("robbery_modifier", 0))
        total_penalty += penalty
        
    await conn.commit()
    
    # Check status effects
    # "disoriented" from Piss Bottle reduces defense by 25%
    if await has_active_effect(victim_id, "disoriented"):
        total_penalty -= 25
        
    log.trace(f"Victim {victim_id} net defensive modifier: {total_penalty}% ({total_penalty / 100:+.2f})")
    return max(0.0, total_penalty / 100)

@log_db_call
async def consume_robber_item_uses(user_id: int):
    """
    Decrements 1 use from each passive robbery-modifier item the robber owns
    (e.g. Bolt Cutters, Hackatron 9900). Called once per rob attempt.
    """
    conn = await db.get_economy()
    # Collect item IDs that provide passive offense bonuses
    passive_rob_item_ids = [
        iid for iid, eff in ITEM_EFFECTS.items()
        if "robbery_modifier" in eff and not eff.get("taser") and not eff.get("gun_defense") and not eff.get("coffee_defense") and not eff.get("expired_fish") and eff.get("robbery_modifier", 0) > 0
    ]
    for item_id in passive_rob_item_ids:
        async with conn.execute(
            "SELECT uses_left FROM user_items WHERE user_id = ? AND item_id = ? AND uses_left > 0",
            (user_id, item_id)
        ) as cursor:
            result = await cursor.fetchone()
        if not result:
            continue
        new_uses = result[0] - 1
        if new_uses <= 0:
            await conn.execute(
                "DELETE FROM user_items WHERE user_id = ? AND item_id = ?", (user_id, item_id)
            )
            log.trace(f"Item {item_id} exhausted for robber {user_id}")
        else:
            await conn.execute(
                "UPDATE user_items SET uses_left = ? WHERE user_id = ? AND item_id = ?",
                (new_uses, user_id, item_id)
            )
    await conn.commit()

@log_db_call
async def schedule_effect_decay(user_id, original_value, duration):
    """Waits for a temporary effect to expire and logs its completion."""
    await asyncio.sleep(duration)
    log.trace(f"Temporary effect expired for {user_id} (was {original_value}%)")

# ===================== Status Effects Functions =====================
@log_db_call
async def add_user_effect(user_id: int, effect_type: str, duration_seconds: int, modifier: int = 0):
    """Adds a temporary status effect to a user, with an expiry timestamp."""
    conn = await db.get_economy()
    expiry = int(time.time()) + duration_seconds
    await conn.execute("""
        INSERT INTO user_effects (user_id, effect_type, expiry, modifier)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(user_id, effect_type) DO UPDATE
        SET expiry = ?, modifier = ?""",
        (user_id, effect_type, expiry, modifier, expiry, modifier)
    )
    await conn.commit()

@log_db_call
async def has_active_effect(user_id: int, effect_type: str) -> bool:
    """Checks if a user has an active status effect."""
    conn = await db.get_economy()
    now = int(time.time())
    # Clean up expired effects of this type for this user to be tidy
    await conn.execute("DELETE FROM user_effects WHERE user_id = ? AND effect_type = ? AND expiry <= ?", (user_id, effect_type, now))
    await conn.commit()
    
    async with conn.execute(
        "SELECT 1 FROM user_effects WHERE user_id = ? AND effect_type = ? AND expiry > ?",
        (user_id, effect_type, now)
    ) as cursor:
        result = await cursor.fetchone()
        return result is not None

@log_db_call
async def get_active_effect_modifier(user_id: int, effect_type: str) -> int:
    """Gets the modifier value for an active status effect (returns 0 if not active)."""
    conn = await db.get_economy()
    now = int(time.time())
    async with conn.execute(
        "SELECT modifier FROM user_effects WHERE user_id = ? AND effect_type = ? AND expiry > ?",
        (user_id, effect_type, now)
    ) as cursor:
        result = await cursor.fetchone()
        return result[0] if result else 0

@log_db_call
async def get_effect_remaining_time(user_id: int, effect_type: str) -> str:
    """Returns a user-friendly string of the remaining duration of an effect (e.g. '2h 15m' or '45s')."""
    conn = await db.get_economy()
    now = int(time.time())
    async with conn.execute(
        "SELECT expiry FROM user_effects WHERE user_id = ? AND effect_type = ? AND expiry > ?",
        (user_id, effect_type, now)
    ) as cursor:
        result = await cursor.fetchone()
    if not result:
        return "0s"
    remaining = result[0] - now
    if remaining <= 0:
        return "0s"
    
    hours = remaining // 3600
    minutes = (remaining % 3600) // 60
    seconds = remaining % 60
    
    parts = []
    if hours > 0:
        parts.append(f"{hours}h")
    if minutes > 0:
        parts.append(f"{minutes}m")
    if seconds > 0 or not parts:
        parts.append(f"{seconds}s")
    return " ".join(parts)

@log_db_call
async def check_and_use_gambling_boost(user_id: int) -> str | bool:
    """
    Checks if user has a Sea BassHead, USB Stick, or Wishbone.
    If so, handles their triggers and decrements uses.
    Returns:
      - "arrested": if USB stick got them arrested.
      - "chicken_backfire": if the wishbone cursed them.
      - True: if they have a boost active.
      - False: if no boost active.
    """
    conn = await db.get_economy()
    
    # 1. USB Stick (ID 21)
    async with conn.execute(
        "SELECT uses_left FROM user_items WHERE user_id = ? AND item_id = 21 AND uses_left > 0", (user_id,)
    ) as cursor:
        usb_res = await cursor.fetchone()
    if usb_res:
        new_uses = usb_res[0] - 1
        if new_uses <= 0:
            await conn.execute("DELETE FROM user_items WHERE user_id = ? AND item_id = 21", (user_id,))
        else:
            await conn.execute("UPDATE user_items SET uses_left = ? WHERE user_id = ? AND item_id = 21", (new_uses, user_id))
        await conn.commit()
        
        # 5% chance of arrest
        if random.random() < 0.05:
            await add_user_effect(user_id, "no_gamble", 1800) # 30 mins
            return "arrested"
        
        # Boost triggered with 35% chance
        if random.random() < 0.35:
            return True
        return False
        
    # 2. Sea BassHead (ID 14)
    async with conn.execute(
        "SELECT uses_left FROM user_items WHERE user_id = ? AND item_id = 14 AND uses_left > 0", (user_id,)
    ) as cursor:
        fish_res = await cursor.fetchone()
    if fish_res:
        new_uses = fish_res[0] - 1
        if new_uses <= 0:
            await conn.execute("DELETE FROM user_items WHERE user_id = ? AND item_id = 14", (user_id,))
        else:
            await conn.execute("UPDATE user_items SET uses_left = ? WHERE user_id = ? AND item_id = 14", (new_uses, user_id))
        await conn.commit()
        
        if random.random() < 0.25:
            return True
        return False
        
    # 3. Half eaten rotisserie chicken (ID 23)
    async with conn.execute(
        "SELECT uses_left FROM user_items WHERE user_id = ? AND item_id = 23 AND uses_left > 0", (user_id,)
    ) as cursor:
        chicken_res = await cursor.fetchone()
    if chicken_res:
        new_uses = chicken_res[0] - 1
        if new_uses <= 0:
            await conn.execute("DELETE FROM user_items WHERE user_id = ? AND item_id = 23", (user_id,))
        else:
            await conn.execute("UPDATE user_items SET uses_left = ? WHERE user_id = ? AND item_id = 23", (new_uses, user_id))
        await conn.commit()
        
        if random.random() < 0.50:
            # Positive: 20% chance to save loss
            if random.random() < 0.20:
                return True
        else:
            # Negative: 15% chance to force loss
            if random.random() < 0.15:
                return "chicken_backfire"
                
    return False

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
async def use_item(user_id: int, item_id: int, target_id: int | None = None) -> str:
    """
    Handles item use and applies effects dynamically.

    Args:
        user_id (int): The user using the item.
        item_id (int): The item's ID.
        target_id (int | None): Optional target player for targeted items (e.g. Taser active use).

    Returns:
        str: A message describing the result.
    """
    conn = await db.get_economy()
    async with conn.execute(
        "SELECT uses_left FROM user_items WHERE user_id = ? AND item_id = ?", (user_id, item_id)
    ) as cursor:
        result = await cursor.fetchone()

    if not result:
        return "❌ You don't have this item!"

    uses_left = result[0]
    if uses_left <= 0:
        return "❌ You have no uses left for this item!"

    item_data = next((item for item in SHOP_ITEMS if item["id"] == item_id), None)
    if not item_data:
        return "❌ Failed to load item details."

    # ── Taser: active offensive use (targeting another player) ──────────────
    if item_id == 5 and target_id is not None:
        target_balance = await get_balance(target_id)
        if target_balance < 50:
            return "❌ Your target is too broke to bother tasing! Save the charge."

    # ── Piss Bottle & Milk suds targets ──────────────────────────────────
    if item_id == 22 and target_id is None:
        return "❌ You must specify a target user to throw the Piss Bottle at! Use: `/shop use item_name:Piss Bottle target:@target`"
    if item_id == 27 and target_id is None:
        return "❌ You must specify a target user to splash Milk suds on! Use: `/shop use item_name:Milk suds target:@target`"

    # Decrement uses (shared path for all items)
    last_use_warning = ""
    if uses_left == 1:
        last_use_warning = f"⚠️ **Last use of your {item_data['name']}!**\n"

    new_uses = uses_left - 1
    if new_uses <= 0:
        await remove_item_from_user(user_id, item_id)
    else:
        await update_item_uses(user_id, item_id, new_uses)

    # ── Resolve taser active use after decrement ─────────────────────────────
    if item_id == 5 and target_id is not None:
        target_balance = await get_balance(target_id)
        if random.random() < 0.70:
            stolen = random.randint(50, min(150, target_balance))
            await update_balance(user_id, stolen)
            await update_balance(target_id, -stolen)
            effect_applied = (
                f"⚡ **Zap!** You tased <@{target_id}> and swiped 💰 `{stolen}` coins "
                f"while they were twitching! ({new_uses} taser charges left)"
            )
        else:
            effect_applied = (
                f"⚡ **Misfire!** Your taser sputtered. <@{target_id}> laughed at you. "
                f"({new_uses} charges left)"
            )
        return f"{last_use_warning}{effect_applied}"

    # ── Resolve Piss Bottle throw active use ──────────────────────────
    if item_id == 22 and target_id is not None:
        await add_user_effect(target_id, "disoriented", 3600) # 1 hour
        return (
            f"{last_use_warning}💦 **Splat!** You lobbed a **Piss Bottle** at <@{target_id}>! "
            f"They smell horrible and are disoriented! They will be 25% easier to rob for the next hour. "
            f"({new_uses} bottles left)"
        )

    # ── Resolve Milk suds throw active use ────────────────────────────
    if item_id == 27 and target_id is not None:
        await add_user_effect(target_id, "milk_suds", 7200) # 2 hours
        return (
            f"{last_use_warning}🥛 **Suds'd!** You splashed sour **Milk suds** all over <@{target_id}>! "
            f"They feel gross and will fail work, crime, and slut jobs 20% more often for the next 2 hours. "
            f"({new_uses} uses left)"
        )

    # ── General item effects ─────────────────────────────────────────────────
    effect_applied = f"Used **{item_data['name']}** ({new_uses} uses remaining)."

    if item_id in ITEM_EFFECTS:
        effect_data = ITEM_EFFECTS[item_id]

        if "robbery_modifier" in effect_data and not effect_data.get("taser") and not effect_data.get("gun_defense") and not effect_data.get("coffee_defense") and not effect_data.get("expired_fish"):
            direction = "+" if effect_data["robbery_modifier"] > 0 else ""
            mod_pct = effect_data["robbery_modifier"]
            effect_applied = (
                f"🔧 **{item_data['name']} equipped!** Robbery success rate {direction}{mod_pct}% "
                f"as long as you hold it ({new_uses} uses left)."
            )
            if effect_data.get("temporary_effect"):
                effect_applied += f"\n⏳ *Temporary effect — decays after {effect_data.get('duration', 0) // 60} minutes.*"

        elif effect_data.get("taser"):
            # Passive equip (no target provided) — just confirm it's in inventory
            effect_applied = (
                f"⚡ **Taser equipped passively!** You are protected — it will automatically fire at the "
                f"next person who tries to rob you. ({new_uses} charges left)\n"
                f"*Tip: use `/shop use taser @target` to tase someone offensively.*"
            )

        elif effect_data.get("gun_defense"):
            effect_applied = (
                f"🔫 **Loaded Gun equipped!** You're armed — any robber will get shot. "
                f"({new_uses} rounds left)"
            )

        elif effect_data.get("drain_percent"):
            effect_applied = "💸 **Financial Drain... activated?** Nothing happens. Nothing at all. Probably fine."

        elif effect_data.get("gambling_placebo"):
            effect_applied = "🪙 **Lucky Coin rubbed!** ...you feel luckier. (Placebo effect is real!)"

        elif effect_data.get("coffee_defense"):
            effect_applied = (
                f"☕ **Coffee Mug (Full) equipped passively!** You take a hot sip. You feel wide awake and alert! "
                f"Others will have a -30% penalty when trying to rob you. ({new_uses} uses left)."
            )

        elif effect_data.get("expired_fish"):
            effect_applied = (
                f"🐟 **Expired fish equipped passively!** Phew, it stinks! The stench keeps robbers away. "
                f"Others will have a -15% penalty when trying to rob you. ({new_uses} uses left)."
            )

        elif effect_data.get("gambling_boost"):
            effect_applied = (
                f"🐟 **Sea BassHead murmurs!** The strange fish variant stares blankly and whispers some numbers... "
                f"You feel luckier. ({new_uses} uses remaining)"
            )

        elif effect_data.get("rubbers"):
            effect_applied = (
                f"💥 **Snap!** You snapped the rubber band against your wrist. Ouch! That hurt! "
                f"({new_uses} rubbers left)"
            )

        elif effect_data.get("pocket_sand"):
            effect_applied = (
                f"⏳ **Pocket sand equipped passively!** You keep a fistful of sand in your pocket. "
                f"If anyone tries to rob you, they will get sand thrown in their eyes, failing the robbery and blinding them for 3 hours! "
                f"({new_uses} charges left)"
            )

        elif effect_data.get("usb_stick"):
            effect_applied = (
                f"💻 **USB Stick active!** You plug the USB labeled 'Cheat_Codes_2026.exe' into your computer. "
                f"It loads up some suspicious hacks. You feel like the casino games stand no chance... "
                f"but don't get caught! ({new_uses} uses left)"
            )

        elif effect_data.get("chicken_wishbone"):
            effect_applied = (
                f"🍗 **Wishbone checked!** You hold the half-eaten rotisserie chicken carcass. "
                f"The wishbone is still intact. Will it bring good luck or bad luck? Gamble to find out! "
                f"({new_uses} uses left)"
            )

        elif effect_data.get("parking_cone"):
            effect_applied = (
                f"🚧 **Parking cone equipped passively!** You carry a bright orange traffic cone. "
                f"If someone tries to rob you, they'll get this cone placed over their head, automatically failing and reducing their success rate for an hour! "
                f"({new_uses} uses left)"
            )

        elif effect_data.get("status_symbol"):
            effect_applied = (
                f"🇸🇪 **IKEA status!** How are you carrying an entire Ikea in your inventory?! "
                f"Truly a legendary feat. Everyone is in awe of your flat-pack storage capacity. "
                f"({new_uses} uses left)"
            )

        elif effect_data.get("misc"):
            if item_id == 15:
                effect_applied = f"💨 You blew the pocket lint away. It did nothing. ({new_uses} lint left)"
            elif item_id == 16:
                effect_applied = f"🌀 You spun the fidget spinner. It spun for a bit. Wheee. ({new_uses} uses left)"
            elif item_id == 17:
                effect_applied = f"✨ You polished the Fake Gold Bar. It is still plastic. ({new_uses} uses left)"
            elif item_id == 18:
                effect_applied = f"🍦 You licked the Melted Ice Cream wrapper. Sticky! ({new_uses} uses left)"
            elif item_id == 19:
                effect_applied = f"🪛 You pointed the Screwdriver and made a buzzing drill sound. ({new_uses} uses left)"

    return f"{last_use_warning}{effect_applied}"

@log_db_call
async def check_gun_defense(victim_id: int) -> int:
    """Returns the remaining uses of the victim's Loaded Gun (0 if unarmed)."""
    conn = await db.get_economy()
    async with conn.execute(
        "SELECT uses_left FROM user_items WHERE user_id = ? AND item_id = 10 AND uses_left > 0",
        (victim_id,)
    ) as cursor:
        result = await cursor.fetchone()
    return result[0] if result else 0

@log_db_call
async def decrement_gun_use(victim_id: int):
    """Decrements 1 use from the victim's Loaded Gun; removes it if exhausted."""
    conn = await db.get_economy()
    async with conn.execute(
        "SELECT uses_left FROM user_items WHERE user_id = ? AND item_id = 10", (victim_id,)
    ) as cursor:
        result = await cursor.fetchone()
    if not result:
        return
    new_uses = result[0] - 1
    if new_uses <= 0:
        await conn.execute(
            "DELETE FROM user_items WHERE user_id = ? AND item_id = 10", (victim_id,)
        )
    else:
        await conn.execute(
            "UPDATE user_items SET uses_left = ? WHERE user_id = ? AND item_id = 10",
            (new_uses, victim_id)
        )
    await conn.commit()

@log_db_call
async def check_taser_defense(victim_id: int) -> bool:
    """Returns True if the victim has a Taser with uses remaining."""
    conn = await db.get_economy()
    async with conn.execute(
        "SELECT uses_left FROM user_items WHERE user_id = ? AND item_id = 5 AND uses_left > 0",
        (victim_id,)
    ) as cursor:
        result = await cursor.fetchone()
    return result is not None

@log_db_call
async def decrement_taser_use(victim_id: int):
    """Decrements 1 use from the victim's Taser; removes it if exhausted."""
    conn = await db.get_economy()
    async with conn.execute(
        "SELECT uses_left FROM user_items WHERE user_id = ? AND item_id = 5", (victim_id,)
    ) as cursor:
        result = await cursor.fetchone()
    if not result:
        return
    new_uses = result[0] - 1
    if new_uses <= 0:
        await conn.execute(
            "DELETE FROM user_items WHERE user_id = ? AND item_id = 5", (victim_id,)
        )
        log.trace(f"Taser exhausted for {victim_id}")
    else:
        await conn.execute(
            "UPDATE user_items SET uses_left = ? WHERE user_id = ? AND item_id = 5",
            (new_uses, victim_id)
        )
    await conn.commit()

# ===================== Claim Functions (daily / weekly / monthly) =====================
# should however work for any type (e.g hourly) if called with a new claim_type string and used consistently
@log_db_call
async def get_last_claim(user_id: int, claim_type: str) -> tuple[int, int]:
    """Returns (last_claim_unix, streak) for a user's claim type. (0, 0) if never claimed."""
    conn = await db.get_economy()
    async with conn.execute(
        "SELECT last_claim, streak FROM user_claims WHERE user_id = ? AND claim_type = ?",
        (user_id, claim_type)
    ) as cursor:
        result = await cursor.fetchone()
    return result if result else (0, 0)

@log_db_call
async def set_last_claim(user_id: int, claim_type: str, timestamp: int, streak: int):
    """Upserts the claim timestamp and streak for a user."""
    conn = await db.get_economy()
    await conn.execute("""
        INSERT INTO user_claims (user_id, claim_type, last_claim, streak)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(user_id, claim_type) DO UPDATE SET last_claim = ?, streak = ?
    """, (user_id, claim_type, timestamp, streak, timestamp, streak))
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
