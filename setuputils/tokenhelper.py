"""
Helper script to obtain a Google Drive OAuth token, base64 encode it, and optionally save it to a .env file.
This is a one-time setup script.
"""

import os
import json
import base64
from google_auth_oauthlib.flow import InstalledAppFlow

# Scopes: drive.file limits to files created/opened by app in Drive.
SCOPES = ["https://www.googleapis.com/auth/drive.file"]

def get_client_secret_path():
    """Prompts the user for the client secret file path."""
    print("\n--- Google Drive Token Setup ---")
    print("This script will guide you through obtaining an OAuth token for Google Drive backups.")
    print("You need a 'client_secret.json' file from the Google Cloud Console.")
    
    while True:
        path = input("\nEnter the path to your client_secret.json file (or 'q' to quit): ").strip()
        if path.lower() == 'q':
            return None
        
        # Remove quotes if the user copied as path
        if path.startswith('"') and path.endswith('"'):
            path = path[1:-1]
            
        if os.path.exists(path):
            return path
        else:
            print(f"Error: File not found at '{path}'. Please try again.")

def update_env_file(token_b64):
    """Updates or creates the .env file with the new token."""
    env_path = ".env"
    # Check if we are in setuputils or root, try to find .env in root if we are in setuputils
    if not os.path.exists(env_path) and os.path.exists(os.path.join("..", ".env")):
        env_path = os.path.join("..", ".env")
    
    print(f"\nTarget .env file: {os.path.abspath(env_path)}")
    save = input("Do you want to save/update the 'DRIVE_TOKEN_B64' in this file? (y/n): ").strip().lower()
    
    if save == 'y':
        try:
            # Read existing lines
            lines = []
            if os.path.exists(env_path):
                with open(env_path, "r") as f:
                    lines = f.readlines()
            
            # Check if key exists and update it, or append
            key = "DRIVE_TOKEN_B64"
            new_line = f"{key}={token_b64}\n"
            key_found = False
            
            for i, line in enumerate(lines):
                if line.strip().startswith(f"{key}="):
                    lines[i] = new_line
                    key_found = True
                    break
            
            if not key_found:
                if lines and not lines[-1].endswith("\n"):
                    lines.append("\n")
                lines.append(new_line)
                
            with open(env_path, "w") as f:
                f.writelines(lines)
                
            print(f"Successfully updated {env_path}")
            
        except Exception as e:
            print(f"Failed to update .env file: {e}")
            print("Please manually add the token to your .env file.")
    else:
        print("Skipping .env update.")

def main():
    client_secret_path = get_client_secret_path()
    if not client_secret_path:
        print("Setup cancelled.")
        return

    try:
        flow = InstalledAppFlow.from_client_secrets_file(client_secret_path, SCOPES)
        
        print("\nLaunching browser for authentication...")
        # Force offline access & consent to ensure we get a refresh_token
        # run_local_server automatically handles the callback
        creds = flow.run_local_server(port=8080, prompt="consent", access_type="offline")
        
        print("\nAuthentication successful!")
        
        # Convert credentials to JSON
        token_json = creds.to_json()
        
        # Base64 encode
        token_b64 = base64.b64encode(token_json.encode('utf-8')).decode('utf-8')
        
        print("\n--- Generated Token (Base64) ---")
        print(token_b64)
        print("--------------------------------")
        
        update_env_file(token_b64)
        
        print("\nSetup complete! You can now delete the generated 'token.json' if it was created in the process, as the base64 string is all you need.")
        
    except Exception as e:
        print(f"\nAn error occurred: {e}")

if __name__ == "__main__":
    main()
