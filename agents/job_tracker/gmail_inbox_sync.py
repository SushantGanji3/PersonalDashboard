#!/usr/bin/env python3
"""
gmail_inbox_sync.py — Inbox digest sync for the self-hosted dashboard.

Runs every 15 minutes via GitHub Actions. Reads the last two days of inbox
mail via the Gmail API and writes it to a Gist in the same shape the live
Artifact's `window.claude.use('mcp')` -> Gmail `search_threads` payload
already has, so dashboard.html's renderGmailThreads() can render either
source unchanged.

The published claude.ai Artifact never needs this — it gets Gmail live via
the in-Artifact MCP connector. This exists only so the self-hosted static
copy (which has no such connector) can show something too.

Required environment variables:
  GMAIL_TOKEN — base64-encoded token.json from gmail_auth.py (same token
                already used by applications_agent.py)
  GIST_TOKEN  — GitHub token with gist scope (reused from jobs-sync)
"""

import base64
import json
import os
import sys
import tempfile
from email.utils import parsedate_to_datetime
from datetime import datetime, timedelta, timezone
from urllib.request import Request, urlopen

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

# Same query the live MCP path uses at dashboard.html's initGmail().
GMAIL_QUERY = "in:inbox newer_than:2d"
MAX_RESULTS = 50

# The Applications gist already holds Gmail-derived data for this dashboard;
# this script just adds a second file to it rather than provisioning a new
# gist + secret.
GIST_ID = "0155252da48c020d0fadb9fdc5e43c2d"
GIST_FILENAME = "gmail_inbox.json"


# ---------------------------------------------------------------------------
# Gmail helpers
# ---------------------------------------------------------------------------

def build_gmail_service():
    """Build Gmail API service from base64-encoded GMAIL_TOKEN env var."""
    gmail_token_b64 = os.environ.get("GMAIL_TOKEN", "").strip()
    if not gmail_token_b64:
        print("ERROR: GMAIL_TOKEN environment variable not set", file=sys.stderr)
        sys.exit(1)

    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request as GoogleRequest
    from googleapiclient.discovery import build

    token_json = base64.b64decode(gmail_token_b64).decode("utf-8")

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        f.write(token_json)
        token_path = f.name

    try:
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)
        if creds.expired and creds.refresh_token:
            creds.refresh(GoogleRequest())
        service = build("gmail", "v1", credentials=creds)
    finally:
        os.unlink(token_path)

    return service


def fetch_inbox_threads(service):
    """Fetch recent inbox messages, deduped to the newest message per thread."""
    result = service.users().messages().list(
        userId="me", q=GMAIL_QUERY, maxResults=MAX_RESULTS
    ).execute()
    stubs = result.get("messages", [])

    threads = {}
    for stub in stubs:
        msg = service.users().messages().get(
            userId="me", id=stub["id"], format="metadata",
            metadataHeaders=["From", "Subject", "Date"],
        ).execute()

        headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
        try:
            date_iso = parsedate_to_datetime(headers.get("Date", "")).astimezone(timezone.utc).isoformat()
        except Exception:
            date_iso = None

        entry = {
            "sender": headers.get("From", "Unknown sender"),
            "subject": headers.get("Subject", "(no subject)"),
            "date": date_iso,
            "labelIds": msg.get("labelIds", []),
        }

        thread_id = msg.get("threadId")
        existing = threads.get(thread_id)
        if existing is None or (date_iso and (existing["date"] or "") < date_iso):
            threads[thread_id] = entry

    return [
        {"id": thread_id, "messages": [entry]}
        for thread_id, entry in threads.items()
        if entry["date"]
    ]


# ---------------------------------------------------------------------------
# Gist helpers (same shape as applications_agent.py's load_gist/write_gist)
# ---------------------------------------------------------------------------

def write_gist(gist_id, filename, data, gist_token):
    payload = json.dumps({
        "files": {filename: {"content": json.dumps(data, indent=2, ensure_ascii=False)}}
    }).encode("utf-8")

    url = f"https://api.github.com/gists/{gist_id}"
    req = Request(url, data=payload, headers={
        "Authorization": f"token {gist_token}",
        "Accept": "application/vnd.github.v3+json",
        "Content-Type": "application/json",
        "User-Agent": "gmail-inbox-sync/1.0",
    }, method="PATCH")

    with urlopen(req, timeout=15) as resp:
        if resp.status not in (200, 201):
            raise RuntimeError(f"Gist write failed: HTTP {resp.status}")
    print(f"Wrote {len(data.get('threads', []))} threads to Gist")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    gist_token = os.environ.get("GIST_TOKEN", "").strip()
    if not gist_token:
        print("ERROR: GIST_TOKEN environment variable not set", file=sys.stderr)
        sys.exit(1)

    service = build_gmail_service()
    threads = fetch_inbox_threads(service)
    print(f"Fetched {len(threads)} inbox threads")

    doc = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "threads": threads,
    }
    write_gist(GIST_ID, GIST_FILENAME, doc, gist_token)


if __name__ == "__main__":
    main()
