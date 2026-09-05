#!/usr/bin/env python3
"""
calendar_sync.py — Google Calendar digest sync for the self-hosted dashboard.

A standalone agent, separate from agents/job_tracker (which tracks job
application status) and agents/gmail_sync — this one only mirrors the
calendar. Runs every 15 minutes via GitHub Actions. Reads the same three
calendars dashboard.html's live initCalendar() merges in (primary, gym, utd
— see CALENDAR_LIST there) via the Calendar API, and writes them to a Gist
in the shape the live MCP `list_events` payload already has, so
dashboard.html's normalizeGCalEvent()/rebuildCalendarEvents() can render
either source unchanged.

The published claude.ai Artifact never needs this — it gets Calendar live via
the in-Artifact MCP connector. This exists only so the self-hosted static
copy (which has no such connector) can show the same live-merged events.

Required environment variables:
  GMAIL_TOKEN — base64-encoded token.json from
                agents/job_tracker/gmail_auth.py. Must include the
                calendar.readonly scope (added alongside gmail.readonly) —
                re-run gmail_auth.py locally if it doesn't.
  GIST_TOKEN  — GitHub token with gist scope (reused from jobs-sync)
"""

import base64
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from urllib.request import Request, urlopen

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar.readonly",
]

# Mirrors dashboard.html's CALENDAR_LIST — every calendar under
# sushantganji17@gmail.com except "Classes" (which mirrors the static
# COURSES pattern and would otherwise double up every class block).
CALENDAR_LIST = [
    {"key": "primary", "calendarId": "primary"},
    {"key": "gym", "calendarId": "e5061853681324622bb915fc263cbcfdeffc27ef831c210f2f22a6ede7bdf09a@group.calendar.google.com"},
    {"key": "utd", "calendarId": "rlpdnru9cc7045oebe02017r0il7k9b0@import.calendar.google.com"},
]

GIST_ID = "0155252da48c020d0fadb9fdc5e43c2d"
GIST_FILENAME = "calendar_sync.json"


# ---------------------------------------------------------------------------
# Calendar helpers
# ---------------------------------------------------------------------------

def build_calendar_service():
    """Build Calendar API service from base64-encoded GMAIL_TOKEN env var."""
    token_b64 = os.environ.get("GMAIL_TOKEN", "").strip()
    if not token_b64:
        print("ERROR: GMAIL_TOKEN environment variable not set", file=sys.stderr)
        sys.exit(1)

    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request as GoogleRequest
    from googleapiclient.discovery import build

    token_json = base64.b64decode(token_b64).decode("utf-8")

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        f.write(token_json)
        token_path = f.name

    try:
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)
        if creds.expired and creds.refresh_token:
            creds.refresh(GoogleRequest())
        service = build("calendar", "v3", credentials=creds)
    finally:
        os.unlink(token_path)

    return service


def monday_of_week(date):
    monday = date - timedelta(days=date.weekday())
    return monday.replace(hour=0, minute=0, second=0, microsecond=0)


def fetch_calendar_sources(service):
    """Fetch events for each configured calendar over the same 12-day window
    the live initCalendar() uses (this week's Monday, +12 days)."""
    now = datetime.now(timezone.utc)
    start = monday_of_week(now)
    end = start + timedelta(days=12)
    time_min = start.isoformat()
    time_max = end.isoformat()

    sources = {}
    for src in CALENDAR_LIST:
        result = service.events().list(
            calendarId=src["calendarId"],
            timeMin=time_min,
            timeMax=time_max,
            singleEvents=True,
            orderBy="startTime",
            maxResults=100,
            timeZone="America/Chicago",
        ).execute()
        items = result.get("items", [])
        sources[src["key"]] = [
            {
                "id": item.get("id"),
                "summary": item.get("summary"),
                "start": item.get("start"),
                "end": item.get("end"),
                "location": item.get("location", ""),
                "htmlLink": item.get("htmlLink", ""),
                "status": item.get("status"),
            }
            for item in items
        ]
        print(f"  {src['key']}: {len(items)} events")

    return sources


# ---------------------------------------------------------------------------
# Gist helper (same shape as applications_agent.py's write_gist)
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
        "User-Agent": "calendar-sync/1.0",
    }, method="PATCH")

    with urlopen(req, timeout=15) as resp:
        if resp.status not in (200, 201):
            raise RuntimeError(f"Gist write failed: HTTP {resp.status}")
    total = sum(len(v) for v in data.get("sources", {}).values())
    print(f"Wrote {total} events across {len(data.get('sources', {}))} calendars to Gist")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    gist_token = os.environ.get("GIST_TOKEN", "").strip()
    if not gist_token:
        print("ERROR: GIST_TOKEN environment variable not set", file=sys.stderr)
        sys.exit(1)

    service = build_calendar_service()
    sources = fetch_calendar_sources(service)

    doc = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "sources": sources,
    }
    write_gist(GIST_ID, GIST_FILENAME, doc, gist_token)


if __name__ == "__main__":
    main()
