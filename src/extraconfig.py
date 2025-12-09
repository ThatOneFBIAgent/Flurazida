# extraconfig.py
# Global configuration variables that are used across multiple modules

# File extension blacklist for image commands
EXT_BLACKLIST = (".mp4", ".webm", ".MP4", ".WEBM", ".mp3", ".ogg", ".wav", ".mov", ".zip", ".7z", ".rar", ".db", ".exe", ".msi")

# Bot owner user ID
BOT_OWNER = 853154444850364417  # Replace with your own user ID

# Google Drive folder ID for backups
BACKUP_GDRIVE_FOLDER_ID = "1Frrg3F-RBczRC4yitQT1ehhULZDCUXbN"

# Test server ID
TEST_SERVER = 1240438418388029460

# Forbidden guilds (guild_id: reason)
FORBIDDEN_GUILDS = {
    1368777209375883405: {"reason": "Have fun with cat bot"},
    1375886954889085088: {"reason": "N/a"}
}

# Forbidden users (user_id: reason)
FORBIDDEN_USERS = {
    935179133598711809: {"reason": "N/a"},
}

# Image processing limits
MAX_JPEG_RECURSIONS = 15  # increase at your own risk, performance-wise more recursions equals more cpu and ram usage.
MAX_JPEG_QUALITY = 4096  # max quality setting for jpegify

# Alpha config
ALPHA = False