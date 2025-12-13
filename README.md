# Flurazida

**Flurazida** is a robust, multi‑purpose Discord bot offering a comprehensive set of features for community management, economy simulation, and media handling.

## Features
- **Image Manipulation** – Meme creation, distortion, and other fun utilities.
- **Economy System** – Work, shop, and currency management with persistent data.
- **Database Backups** – Automated backups to Google Drive for safety.
- **Moderation Tools** – User bans, message clearing, and permission management.
- **Gambling Games** – Virtual coin betting and games (no real money).
- **Item Templates** – Customizable items with clear documentation.
- **Web Server** – JSON API exposing bot statistics for external dashboards.

## Setup Guide (Basic)

### 1. Clone the repository
```bash
git clone https://github.com/ThatOneFBIAgent/Flurazida.git
```

### 2. Install dependencies
```bash
pip install -r requirements.txt
```

### 3. Run the helper
```bash
python startbot.py
```
Follow the prompts to obtain a `token.json`, encode it in Base64, and add it to your `.env` file as `DRIVE_TOKEN_B64`, for later use do:
```bash
python src/main.py
```

## Setup Guide (Advanced)

### 1. Clone the repository
```bash
git clone https://github.com/ThatOneFBIAgent/Flurazida.git
cd Flurazida
```

### 2. Install dependencies
```bash
pip install -r requirements.txt
```

### 3. (Optional) Configure Google Drive backups
```bash
python setuputils/tokenhelper.py
```
Follow the prompts to obtain a `token.json`, encode it in Base64, and add it to your `.env` file as `DRIVE_TOKEN_B64`.

### 4. Environment configuration
Create a `.env/.env` file in the project root containing:
```ini
BOT_TOKEN=your_discord_bot_token
DRIVE_TOKEN_B64=base64_encoded_drive_token   # optional
PORT=5000                                    # web server port (default)
```

### 5. Bot configuration
Edit `src/extraconfig.py` to set:
- `BOT_OWNER` – your Discord user ID.
- `BACKUP_FOLDER_ID` – Google Drive folder ID for backups.
- Optional blacklists (`FORBIDDEN_GUILDS`, `FORBIDDEN_USERS`).

### 6. Run the bot
```bash
python src/main.py
```
The bot will start and the web server (if enabled and configured) will listen on the configured `PORT`. Access `http://localhost:5000/stats` to view live metrics.

## License
GNU Affero General Public License v3.0