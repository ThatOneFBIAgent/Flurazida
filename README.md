"MAIN" is the main branch of this bot, whilst MASTER contains unfinished MySQL code.
The original bot runs on sqlite3, hence why it was reverted to this one and is favorable for NUC or home based server shenanigans.

To run this bot:
1- Run this command in CMD or powershell:
pip install..
discord.py
psutil
aiohttp
python-dotenv
pynacl (optional)

2- Create a folder named ".env" and create a file named ".env"
3- Open this file with your choice of editor and make a variable named "BOT_TOKEN" with your bot's token as the value in qoutation marks ("example")
4- In the terminal navigate to the src folder using cd
5- Run python main.py

Your app should now be running the version of this bot, alongside setting a custom status, the lines "Commands synced!" should also appear in the terminal if the commands synced successfully and ready to use.