# Flurazida

Welcome to **Flurazide** — the bot that does *way too much and somehow doesn’t crash*.  
It handles image manipulation, economy systems, moderation, gambling, database backups, and a bunch of other stuff you’ll probably discover by accident while perusing the git.

---

## 🧠 Features
- 🎨 **Image Manipulation:** Meme it, distort it, and regret it later.
- 💸 **Economy System:** Work, buy, flex, lose it all in seconds.
- 💾 **Database Backups:** Google Drive-powered “oh crap” prevention.
- 🧰 **Moderation Commands:** Bonk users, clear messages, enforce order.
- 🎲 **Gambling System:** For your fake in-house coins — don’t sue me, it’s not real money.
- 🧱 **Item Templates:** Customizable, structured, and "questionably" documented.

---

## ⚙️ Setup Guide

### 1. Clone the repo
```bash
git clone https://github.com/ThatOneFBIAgent/Flurazida.git
cd Flurazida
```
### 2. Install required packages
```bash
pip install -r requirements.txt
```

### 2.1 (OPTIONAL) Set google drive:
```bash
python tokenhelper.py
```
This will open your browser (set by default) and open a google authorization page, it will return a token.json, copy this and turn it into a base64 encoded string for .env saving, or move into /src/.

### IMPORTANT
You **MUST** have a client (OAuth2 Web app with 8080 redirect) from google's Cloud Console, replace ln 17's contents with the downloaded json.

### 3. Configure the bot
Open config.py (found under ./src/config.py) and change bot owner id, and backup folder ID. For configuring the bot's token, head to the project root and create a folder named ".env", with a single file titled the same, this'll be where BOT_TOKEN and DRIVE_TOKEN_B64 will be stored at for safety purposes.

### 4. Running the bot
When running on a VPS make sure Procfile points to the correct path, or when done locally simply do:
```bash
python src/main.py
```

When done correctly you will see something like this:
```
[000035ms] [  INFO  ] [opt.venv.lib.python3.12.site-packages.discord.client] logging in using static token
[000344ms] [  INFO  ] [src.database] Economy DB Logging begin
[000344ms] [  INFO  ] [src.database] Moderator DB Logging begin
```

## CONGRATULATIONS!!