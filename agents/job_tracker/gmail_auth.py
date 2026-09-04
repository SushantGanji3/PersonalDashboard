#!/usr/bin/env python3
"""
gmail_auth.py — One-time Google OAuth2 authorization helper (Gmail + Calendar).

Run this script ONCE locally to generate token.json.
After running:
  1. base64 -i token.json | tr -d '\\n'   (copy the output)
  2. Update the GitHub secret: GMAIL_TOKEN (gh secret set GMAIL_TOKEN)

If token.json already exists but was authorized for a narrower scope list
(e.g. Gmail only, before Calendar was added), this re-runs the consent flow
rather than silently refreshing a token that Google never actually granted
Calendar access to.

Usage:
  python3 agents/job_tracker/gmail_auth.py
"""

import json
import os
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.resolve()
CLIENT_SECRET_PATH = SCRIPT_DIR / "client_secret.json"
TOKEN_PATH = SCRIPT_DIR / "token.json"

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar.readonly",
]


def main():
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
    except ImportError:
        print("ERROR: Required packages not installed.")
        print("Run these commands first:\n")
        print("  python3 -m venv agents/job_tracker/.venv")
        print("  source agents/job_tracker/.venv/bin/activate")
        print("  pip install google-auth google-auth-oauthlib google-auth-httplib2 google-api-python-client")
        print("  python3 agents/job_tracker/gmail_auth.py\n")
        sys.exit(1)

    if not CLIENT_SECRET_PATH.exists():
        print(f"ERROR: client_secret.json not found at {CLIENT_SECRET_PATH}")
        print("Download it from Google Cloud Console -> APIs & Services -> Credentials")
        sys.exit(1)

    creds = None

    # Check if token.json already exists, is still valid, AND actually
    # covers every scope we need — a stale token authorized for a narrower
    # scope list (e.g. Gmail only) must be re-consented, not just refreshed.
    if TOKEN_PATH.exists():
        granted = set(json.loads(TOKEN_PATH.read_text()).get("scopes", []))
        if set(SCOPES).issubset(granted):
            creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)
        else:
            print("Existing token.json doesn't cover all required scopes — re-authorizing.")

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            print("Refreshing existing token...")
            creds.refresh(Request())
        else:
            print("Opening browser for Google authorization...")
            print("Sign in with sushantganji17@gmail.com and approve read access to Gmail and Calendar.")
            flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET_PATH), SCOPES)
            creds = flow.run_local_server(port=0)

    # Save the token
    TOKEN_PATH.write_text(creds.to_json())
    print(f"\n✅ token.json saved to: {TOKEN_PATH}")
    print("\nNext step — base64-encode it for GitHub:")
    print(f"  base64 -i {TOKEN_PATH} | tr -d '\\n'")
    print("\nThen store the output as a GitHub secret named: GMAIL_TOKEN")
    print("  GitHub repo -> Settings -> Secrets and variables -> Actions -> New repository secret")


if __name__ == "__main__":
    main()
