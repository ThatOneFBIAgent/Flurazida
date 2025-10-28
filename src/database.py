# database.py
# only mess with this file if you want a headache, trust me it's not fun.

import sqlite3, shutil, os, asyncio, sys, time, json, tempfile, io, base64, zipfile
from functools import wraps
from dotenv import load_dotenv
from config import BACKUP_GDRIVE_FOLDER_ID

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload, MediaFileUpload # fuck pydrive2
from logger import get_logger
log = get_logger("database")

def log_db_call(func):
    from functools import wraps
    @wraps(func)
    def wrapper(*args, **kwargs):
        log.info(f"ECON DB CALL: {func.__name__} called with args={args}, kwargs={kwargs}")
        return func(*args, **kwargs)
    return wrapper

def log_mod_call(func):
    from functools import wraps
    @wraps(func)
    def wrapper(*args, **kwargs):
        log.info(f"ECON MOD CALL: {func.__name__} called with args={args}, kwargs={kwargs}")
        return func(*args, **kwargs)
    return wrapper

log.info("Economy DB Logging begin")
log.info("Moderator DB Logging begin")


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
        creds_b64 = os.environ[CREDENTIALS_ENV]
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
            # NOTE: We cannot persist the refreshed token back into Railway env from code.
            # If you want the updated token saved, you can re-export the new creds.to_json() manually.
        except Exception as e:
            log.warning(f"Failed to refresh token: {e}. Token may be revoked; you'll need to re-run the local helper.")
    return creds

def build_drive_service():
    # Try local token first, then env-based token loader as fallback
    try:
        creds = load_creds_local()
    except FileNotFoundError:
        log.info("token.json not found, falling back to env-based credentials.")
        creds = load_creds_from_env()
    return build("drive", "v3", credentials=creds, cache_discovery=False)

# to google:
#   hey, why the FUCK do you make this so hard
#   this is like the 6th time i had to rewrite, the entire FUCKING logic
#   becuase service accounts dont have qouta or some shit. this is python fyi.

def backup_db_to_gdrive_env(local_path, drive_filename, folder_id):
    log.info(f"Backing up {local_path} -> {drive_filename}")
    service = build_drive_service()

    query = f"'{folder_id}' in parents and name='{drive_filename}' and trashed=false"
    res = service.files().list(q=query, fields="files(id, name)").execute()
    files = res.get("files", [])

    media = MediaFileUpload(local_path, mimetype="application/x-sqlite3", resumable=True)

    if files:
        file_id = files[0]["id"]
        service.files().update(fileId=file_id, media_body=media).execute()
        log.info("Updated existing backup.")
    else:
        meta = {"name": drive_filename, "parents": [folder_id]}
        service.files().create(body=meta, media_body=media, fields="id").execute()
        log.info("Created new backup.")

def backup_all_dbs_to_gdrive_env(dbs: list[tuple[str, str]], folder_id: str):
    """
    Combine multiple .db files into one .zip and upload it to Drive.
    dbs = [(local_path, drive_filename), ...]
    """
    timestamp = time.strftime("%Y-%m-%d_%H-%M-%S")
    zip_filename = f"backup_{timestamp}.zip"
    temp_zip_path = os.path.join(tempfile.gettempdir(), zip_filename)

    # Create a ZIP file with all DBs
    log.info(f"Creating combined backup: {temp_zip_path}")
    with zipfile.ZipFile(temp_zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
        for local_path, drive_filename in dbs:
            if not os.path.exists(local_path):
                log.warning(f"File not found: {local_path}, skipping.")
                continue
            zipf.write(local_path, arcname=drive_filename)
    log.info("All databases zipped successfully.")

    # Upload to Google Drive
    service = build_drive_service()
    query = f"'{folder_id}' in parents and name='{zip_filename}' and trashed=false"
    res = service.files().list(q=query, fields="files(id, name)").execute()
    files = res.get("files", [])

    media = MediaFileUpload(temp_zip_path, mimetype="application/zip", resumable=True)

    try:
        if files:
            file_id = files[0]["id"]
            service.files().update(fileId=file_id, media_body=media).execute()
            log.info("Updated existing combined backup.")
        else:
            meta = {"name": zip_filename, "parents": [folder_id]}
            service.files().create(body=meta, media_body=media, fields="id").execute()
            log.info("Created new combined backup.")
    finally:
        try:
            os.remove(temp_zip_path)
        except Exception as e:
            log.warning(f"Couldn't remove temp zip: {e}")

def restore_db_from_gdrive_env(local_path, drive_filename, folder_id=None):
    """
    Download a file named drive_filename from Drive (optionally inside folder_id)
    and save it to local_path. Uses googleapiclient (same auth path as backup).
    """
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
    fh = io.FileIO(local_path, 'wb')
    downloader = MediaIoBaseDownload(fh, request)
    done = False
    try:
        while not done:
            status, done = downloader.next_chunk()
        log.info(f"Restored database from Google Drive: {drive_filename}")
        return True
    except HttpError as e:
        log.error(f"Failed to download {drive_filename}: {e}")
        return False


def restore_all_dbs_from_gdrive_env(folder_id, restore_map: dict[str, str], latest_only=True):
    """
    Restore all databases from a combined ZIP on Google Drive.

    restore_map = {
        "economy.db": ECONOMY_DB_PATH,
        "moderator.db": MODERATOR_DB_PATH
    }

    If latest_only=True, it restores from the most recently modified backup in folder_id.
    """

    service = build_drive_service()
    log.info("Searching for latest backup ZIP in Google Drive...")

    q = f"'{folder_id}' in parents and name contains '.zip' and trashed=false"
    res = service.files().list(
        q=q,
        fields="files(id, name, modifiedTime)",
        orderBy="modifiedTime desc"
    ).execute()

    files = res.get("files", [])
    if not files:
        log.warning("No .zip backups found on Google Drive.")
        return False

    # Pick latest by modified time
    target_file = files[0]
    file_id = target_file["id"]
    file_name = target_file["name"]
    log.info(f"Found backup: {file_name}")

    # Download to temporary location
    temp_zip = os.path.join(tempfile.gettempdir(), file_name)
    request = service.files().get_media(fileId=file_id)
    fh = io.FileIO(temp_zip, 'wb')
    downloader = MediaIoBaseDownload(fh, request)

    try:
        done = False
        while not done:
            status, done = downloader.next_chunk()
            if status:
                log.info(f"Download progress: {int(status.progress() * 100)}%")

        log.info(f"Downloaded {file_name}, extracting...")
        with zipfile.ZipFile(temp_zip, "r") as zipf:
            for member in zipf.namelist():
                if member in restore_map:
                    dest_path = restore_map[member]
                    zipf.extract(member, path=os.path.dirname(dest_path))
                    # Move extracted file into proper location
                    os.replace(os.path.join(os.path.dirname(dest_path), member), dest_path)
                    log.info(f"Restored {member} → {dest_path}")
                else:
                    log.warning(f"Skipping unknown file in ZIP: {member}")

        log.info("✅ All databases restored successfully.")
        os.remove(temp_zip)
        return True

    except HttpError as e:
        log.error(f"❌ Failed to restore from Drive: {e}")
        return False
    except Exception as e:
        log.error(f"❌ Unexpected error during restore: {e}")
        return False

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

# Connect to economy.db (for user balances and everything)
econ_conn = sqlite3.connect(ECONOMY_DB_PATH, check_same_thread=False)
econ_cursor = econ_conn.cursor()
mod_conn = sqlite3.connect(MODERATOR_DB_PATH, check_same_thread=False)
mod_cursor = mod_conn.cursor()

# Create tables if they don't exist

econ_cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT NOT NULL,
    balance INTEGER NOT NULL DEFAULT 0
)
""")

# god why the fuck am i making 2 tables, this is shit.
# fuck you that's why.

# User items table (tracks owned items & uses)
econ_cursor.execute("""
CREATE TABLE IF NOT EXISTS user_items (
    user_id INTEGER,
    item_id TEXT,
    item_name TEXT NOT NULL,
    uses_left INTEGER DEFAULT 0,
    effect_modifier INTEGER DEFAULT 0,
    PRIMARY KEY (user_id, item_id)
)
""")

# Commit table creations
econ_conn.commit()

def ensure_guild_case_table(cur, guild_id):
    table_name = f"cases_{guild_id}"
    cur.execute(f"""
    CREATE TABLE IF NOT EXISTS {table_name} (
            case_number INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            username TEXT NOT NULL,
            reason TEXT,
            action_type TEXT NOT NULL,
            timestamp INTEGER NOT NULL,
            moderator_id INTEGER NOT NULL,
            expiry INTEGER DEFAULT 0
    );
""")

# continue this dumbassery with the mod tables now. am i going insane?
# extremely.

@log_db_call
def modify_robber_multiplier(user_id, change, duration=None):
    """Modifies the user's robbery success/failure rate"""
    current_modifier = get_robbery_modifier(user_id)  # Get current modifier
    new_modifier = max(min(current_modifier + change, 100), -100)  # Cap between -100% and +100%

    # Update the database
    econ_cursor.execute("UPDATE user_items SET effect_modifier = ? WHERE user_id = ?",
                        (new_modifier, user_id))
    econ_conn.commit()

    log.info(f"Updated robbery modifier for {user_id}: {new_modifier}%")

    # If it's temporary (like Resin Sample), schedule decay
    if duration:
        asyncio.create_task(schedule_effect_decay(user_id, current_modifier, duration))

@log_db_call
def get_robbery_modifier(user_id):
    """Gets the total robbery modifier for a user (from items)"""
    econ_cursor.execute("SELECT SUM(effect_modifier) FROM user_items WHERE user_id = ?", (user_id,))
    result = econ_cursor.fetchone()
    return result[0] if result and result[0] else 0  # Default to 0 modifier

@log_db_call
async def schedule_effect_decay(user_id, original_value, duration):
    """Waits for the effect duration to expire and then reverts the modifier"""
    await asyncio.sleep(duration)  # Wait X seconds
    econ_cursor.execute("UPDATE user_items SET effect_modifier = ? WHERE user_id = ?",
                        (original_value, user_id))
    econ_conn.commit()

    log.info(f"Restored robbery modifier for {user_id} to {original_value}%")

# ----------- Economy Functions -----------

@log_db_call
def update_balance(user_id, amount):
    """ Updates user balance and syncs to backup """
    log.info(f"Updating balance for {user_id}: {amount} coins")
    econ_cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amount, user_id))
    econ_conn.commit()

@log_db_call
def get_balance(user_id):
    """ Fetches user balance """
    log.info(f"Getting balance for {user_id}")
    econ_cursor.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
    result = econ_cursor.fetchone()
    return result[0] if result else 0

@log_db_call
def add_user(user_id, username):
    """ Adds a user to the economy database if they don't exist """
    log.info(f"Adding user {user_id} in economy database, {username}")
    econ_cursor.execute("INSERT OR IGNORE INTO users (user_id, username, balance) VALUES (?, ?, 0)", (user_id, username))
    econ_conn.commit()

# ----------- Item Handling Functions -----------

@log_db_call
def add_user_item(user_id, item_id, item_name, uses_left=1, effect_modifier=0):
    """ Adds an item to the user's inventory """
    log.info(f"Adding item {item_name} (ID: {item_id}) to {user_id}'s inventory")
    econ_cursor.execute("""
        INSERT INTO user_items (user_id, item_id, item_name, uses_left, effect_modifier) 
        VALUES (?, ?, ?, ?, ?) 
        ON CONFLICT(user_id, item_id) DO UPDATE 
        SET uses_left = uses_left + ?""",
        (user_id, item_id, item_name, uses_left, effect_modifier, uses_left)
    )
    econ_conn.commit()

@log_db_call
def get_user_items(user_id):
    """ Fetches all items a user owns """
    econ_cursor.execute("SELECT item_id, item_name, uses_left FROM user_items WHERE user_id = ?", (user_id,))
    items = econ_cursor.fetchall()

    return [{"item_id": row[0], "item_name": row[1], "uses_left": row[2]} for row in items] if items else []

@log_db_call
def remove_item_from_user(user_id, item_id):
        """Removes an item completely from the user's inventory."""
        econ_cursor.execute("DELETE FROM user_items WHERE user_id = ? AND item_id = ?", (user_id, item_id))
        econ_conn.commit()

@log_db_call
def update_item_uses(user_id, item_id, uses_left):
        """Updates the number of uses left for a user's item."""
        econ_cursor.execute("UPDATE user_items SET uses_left = ? WHERE user_id = ? AND item_id = ?", (uses_left, user_id, item_id))
        econ_conn.commit()

@log_db_call
def add_item_to_user(user_id, item_id, item_name, uses_left=1, effect_modifier=0):
        """Adds an item to the user's inventory or updates uses if it exists."""
        econ_cursor.execute("""
            INSERT INTO user_items (user_id, item_id, item_name, uses_left, effect_modifier)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id, item_id) DO UPDATE SET uses_left = user_items.uses_left + ?
        """, (user_id, item_id, item_name, uses_left, effect_modifier, uses_left))
        econ_conn.commit()

# ----------- Shop Functions -----------

@log_db_call
def buy_item(user_id, item_id, item_name, price, uses_left=1, effect_modifier=0):
    """Buys an item from the shop and deducts balance, using sqlite3 for all operations."""
    log.info(f"User {user_id} is buying {item_name} for {price} coins")

    # Check if user exists
    econ_cursor.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
    user = econ_cursor.fetchone()
    if not user:
        return False  # User does not exist

    current_balance = user[0]
    if current_balance < price:
        return False  # Not enough money

    # Deduct balance
    econ_cursor.execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (price, user_id))

    # Add or update item in inventory
    econ_cursor.execute("""
        INSERT INTO user_items (user_id, item_id, item_name, uses_left, effect_modifier)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(user_id, item_id) DO UPDATE SET uses_left = user_items.uses_left + ?
    """, (user_id, item_id, item_name, uses_left, effect_modifier, uses_left))

    econ_conn.commit()
    return True


# ----------- Special Item Effects -----------

@log_db_call
def use_item(user_id, item_id):
    """Handles item use and applies effects dynamically."""
    # Fetch user items
    econ_cursor.execute("SELECT uses_left FROM user_items WHERE user_id = ? AND item_id = ?", (user_id, item_id))
    result = econ_cursor.fetchone()
    
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

    # SHOP_ITEMS
    #    {"id": 1, "name": "Bragging Rights", "price": 10000, "effect": "Nothing. Just flex.", "uses_left": 1},
    #    {"id": 2, "name": "Robber's Mask", "price": 5000, "effect": "Increases robbery success", "uses_left": 3},
    #    {"id": 3, "name": "Bolt Cutters", "price": 3000, "effect": "Improves robbery success", "uses_left": 4},
    #    {"id": 4, "name": "Padlocked Wallet", "price": 2000, "effect": "Protects against robbery", "uses_left": 10},
    #    {"id": 5, "name": "Taser", "price": 3500, "effect": "Stuns robbers", "uses_left": 2},
    #    {"id": 6, "name": "Lucky Coin", "price": 1500, "effect": "Boosts gambling odds.. or just a really expensive paperweight", "uses_left": 4},
    #    {"id": 7, "name": "VIP Pass", "price": 50000, "effect": "Grants VIP access", "uses_left": 1},
    #    {"id": 8, "name": "Hackatron 9900™", "price": 7000, "effect": "Increases heist efficiency", "uses_left": 5},
    #    {"id": 9, "name": "Resintantoinem Sample", "price": 4000, "effect": "Probaably a bad idea, increases heist efficiency but once effect wears off you'll be more susceptible", "uses_left": 1},
    #    {"id": 10, "name": "Loaded Gun", "price": 9000, "effect": "You remembered your 4th amendment rights, self defense agaist robbers", "uses_left": 19},
    #    {"id": 11, "name": "Watermelon", "price": 500, "effect": "Doctors approve! Does nothing", "uses_left": 500},

    # Apply effect if item has one
    effect_applied = ""
    if item_id in item_effects:
        effect_data = item_effects[item_id]

        # Apply Robbery Modifiers
        if "robbery_modifier" in effect_data:
            modify_robber_multiplier(user_id, effect_data["robbery_modifier"])
            effect_applied = f"🔧 **Your robbery success rate changed by {effect_data['robbery_modifier']}%!**"

        # Apply temporary effects (like Resin Sample)
        if "temporary_effect" in effect_data:
            schedule_effect_decay(user_id, effect_data["temporary_effect"], effect_data["duration"])

        # Apply defensive effects
        if "taser" in effect_data:
            modify_robber_multiplier(user_id, effect_data["robbery_modifier"])
            effect_applied = "⚡ **You are now protected from robbery for one attempt!**"
        
        if "gun_defense" in effect_data:
            effect_applied = "🔫 **You are armed. Good luck, robber.**"

    # Reduce item uses
    econ_cursor.execute("UPDATE user_items SET uses_left = uses_left - 1 WHERE user_id = ? AND item_id = ?", (user_id, item_id))
    econ_conn.commit()

    return f"{last_use_warning}✅ **You used {item_data['name']}!** {effect_applied}"

@log_db_call
def check_gun_defense(victim_id):
    econ_cursor.execute("SELECT uses_left FROM user_items WHERE user_id = ? AND item_id = 10", (victim_id,))
    result = econ_cursor.fetchone()
    return result[0] if result and result[0] > 0 else 0

@log_db_call
def decrement_gun_use(victim_id):
    econ_cursor.execute("UPDATE user_items SET uses_left = uses_left - 1 WHERE user_id = ? AND item_id = 10 AND uses_left > 0", (victim_id,))
    econ_conn.commit()

# ----------- Moderator logging functions -----------

# unfortunately the automatic creation of tables per server is not an sql function, so we'll simulate with python code.
@log_mod_call
def insert_case(mod_cursor, guild_id, user_id, username, reason, action_type, moderator_id, timestamp=None, expiry=0):
    table_name = f"cases_{guild_id}"
    ensure_guild_case_table(mod_cursor, guild_id)
    if timestamp is None:
        timestamp = int(time.time())
    mod_cursor.execute(
        f"""
        INSERT INTO {table_name} (user_id, username, reason, action_type, timestamp, moderator_id, expiry)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (user_id, username, reason, action_type, timestamp, moderator_id, expiry)
    )
    mod_conn.commit()
    # Optionally, get the last inserted case_number:
    mod_cursor.execute(f"SELECT last_insert_rowid()")
    return mod_cursor.fetchone()[0]

@log_mod_call
def get_cases_for_guild(mod_cursor, guild_id, limit=50, offset=0):
    table_name = f"cases_{guild_id}"
    ensure_guild_case_table(mod_cursor, guild_id)
    mod_cursor.execute(
        f"""
        SELECT case_number, user_id, username, reason, action_type, timestamp, moderator_id
        FROM {table_name}
        ORDER BY case_number DESC
        LIMIT ? OFFSET ?
        """,
        (limit, offset)
    )
    return mod_cursor.fetchall()

@log_mod_call
def get_cases_for_user(mod_cursor, guild_id, user_id):
    table_name = f"cases_{guild_id}"
    ensure_guild_case_table(mod_cursor, guild_id)
    mod_cursor.execute(
        f"""
        SELECT case_number, reason, action_type, timestamp, moderator_id
        FROM {table_name} WHERE user_id = ?
        ORDER BY case_number DESC
        """,
        (user_id,)
    )
    return mod_cursor.fetchall()

@log_mod_call
def get_case(mod_cursor, guild_id, case_number):
    table_name = f"cases_{guild_id}"
    ensure_guild_case_table(mod_cursor, guild_id)
    mod_cursor.execute(
        f"""
        SELECT case_number, user_id, username, reason, action_type, timestamp, moderator_id
        FROM {table_name} WHERE case_number = ?
        """,
        (case_number,)
    )
    return mod_cursor.fetchone()

@log_mod_call
def remove_case(mod_cursor, guild_id, case_number):
    table_name = f"cases_{guild_id}"
    ensure_guild_case_table(mod_cursor, guild_id)
    mod_cursor.execute(
        f"DELETE FROM {table_name} WHERE case_number = ?",
        (case_number,)
    )
    mod_conn.commit()

@log_mod_call
def edit_case_reason(mod_cursor, guild_id, case_number, new_reason):
    table_name = f"cases_{guild_id}"
    ensure_guild_case_table(mod_cursor, guild_id)
    mod_cursor.execute(
        f"UPDATE {table_name} SET reason = ? WHERE case_number = ?",
        (new_reason, case_number)
    )
    mod_conn.commit()

# Do not log becuase it spams terminal like hell
def get_expired_cases(mod_cursor, guild_id, action_type, now=None):
    table_name = f"cases_{guild_id}"
    ensure_guild_case_table(mod_cursor, guild_id)
    if now is None:
        now = int(time.time())
    mod_cursor.execute(
        f"""
        SELECT case_number, user_id FROM {table_name}
        WHERE action_type = ? AND expiry > 0 AND expiry <= ?
        """,
        (action_type, now)
    )
    return mod_cursor.fetchall()

BACKUP_FOLDER_ID = BACKUP_GDRIVE_FOLDER_ID


async def periodic_backup(interval_hours=1):
    log.info("Started periodic_backup task")
    while True:
        try:
            log.info("Running backup...")
            econ_conn.commit()
            mod_conn.commit()
            log.info("Databases committed.")

            try:
                backup_db_to_gdrive_env(ECONOMY_DB_PATH, "economy.db", BACKUP_FOLDER_ID)
            except HttpError as e:
                log.warning(f"Backup failed for economy.db: {e}")
            except Exception as e:
                log.warning(f"Unexpected error backing up economy.db: {e}")
            
            try:
                backup_db_to_gdrive_env(MODERATOR_DB_PATH, "moderator.db", BACKUP_FOLDER_ID)
            except HttpError as e:
                log.warning(f"Backup failed for moderator.db: {e}")
            except Exception as e:
                log.warning(f"Unexpected error backing up moderator.db: {e}")
            
            log.info("Backup task completed.")
        except Exception as e:
            log.error(f"Periodic backup loop encountered an unexpected error: {e}")

        await asyncio.sleep(interval_hours * 3600)
