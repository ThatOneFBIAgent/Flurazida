# this is what you run once to get the oauth token for your drive backups if you want, ephemeral services like railway can have outages and you loose all your data.
# to convert this into the string format the actual bot uses do:
# windows:
#   [Convert]::ToBase64String([IO.File]::ReadAllBytes("token.json"))
# unix/mac
#   cat token.json | base64 -w0
# take the final token and add it as a long ass string.

# get_drive_token_local.py
import json
from google_auth_oauthlib.flow import InstalledAppFlow

# Scopes: drive.file limits to files created/opened by app in Drive.
SCOPES = ["https://www.googleapis.com/auth/drive.file"]

# Path to the client secret you downloaded from Cloud Console (OAuth client ID - Web app with localhost 8080 redirect)
CLIENT_SECRETS_PATH = "client_secret_547994356259-u4q3pjsjucveg0j70t2lc2c2tcjkb906.apps.googleusercontent.com.json" # replace with the one you got

def main():
    flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRETS_PATH, SCOPES)

    # Force offline access & consent to ensure we get a refresh_token
    auth_url, _ = flow.authorization_url(access_type="offline", prompt="consent")
    print("Open this URL in your browser and complete consent (copy/paste code if asked):")
    print(auth_url)
    code = input("If presented with a code, paste it here (otherwise just press Enter after consenting):\n").strip()
    if code:
        flow.fetch_token(code=code)
    else:
        # run_local_server if you prefer automatic open+callback handling
        creds = flow.run_local_server(port=8080)
        print("Credentials obtained and saved to 'token.json'")
        with open("token.json", "w") as f:
            f.write(creds.to_json())
        print("Here's the token JSON (copy it).")
        print(creds.to_json())
        return

    creds = flow.credentials
    token_json = creds.to_json()
    with open("token.json", "w") as f:
        f.write(token_json)
    print("Saved token.json. Copy the contents and base64-encode it for Railway.")
    print(token_json)

if __name__ == "__main__":
    main()
