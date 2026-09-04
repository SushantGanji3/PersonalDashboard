#!/usr/bin/env python3
"""
applications_agent.py — Job Application Tracker Agent

Runs every 3 hours via GitHub Actions. Reads Gmail for job-related emails,
classifies them with Gemini Flash, and writes structured data to a GitHub Gist.

First run: scans past 90 days (backfill). Subsequent runs: picks up from lastChecked.

Required environment variables:
  GMAIL_TOKEN   — base64-encoded token.json from gmail_auth.py
  GEMINI_API_KEY — Gemini API key from aistudio.google.com
  GIST_TOKEN    — GitHub token with gist scope (reused from jobs-sync)
  GIST_ID       — GitHub Gist ID for applications.json
"""

import base64
import hashlib
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from email import message_from_bytes
from urllib.request import Request, urlopen
from urllib.error import URLError

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
BACKFILL_DAYS = 90

# Gmail search query — cast a wide net, Gemini filters false positives
GMAIL_QUERY = (
    'subject:("application received" OR "thank you for applying" OR '
    '"we received your application" OR "your application" OR '
    '"online assessment" OR "hackerrank" OR "codesignal" OR "codility" OR '
    '"interview" OR "offer of employment" OR "congratulations" OR '
    '"unfortunately" OR "not moving forward" OR "other direction")'
)

# Status priority — higher index = higher priority, never downgrade
STATUS_PRIORITY = ["Applied", "OA", "Interview", "Offer", "Rejected"]

GEMINI_MODEL = "gemini-1.5-flash-latest"
GEMINI_API_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"

SYSTEM_PROMPT = """You analyze job application emails. Given an email's subject, sender, and body, return ONLY valid JSON (no markdown, no explanation):

{
  "isJobEmail": true or false,
  "company": "Company Name or null",
  "role": "Job Title or null",
  "type": "Internship" or "NewGrad" or "Unknown",
  "status": "Applied" or "OA" or "Interview" or "Offer" or "Rejected",
  "deadline": "ISO 8601 date string or null (OA/interview deadline if mentioned)",
  "bookingLink": "URL or null (interview scheduling link if present)",
  "applyUrl": "URL or null (original job posting URL if detectable)",
  "notes": "brief important note or empty string"
}

Status rules:
- Applied: confirmation you submitted an application
- OA: online assessment / coding challenge invitation
- Interview: interview scheduled or invited
- Offer: job offer extended
- Rejected: declined, not moving forward, other direction

If the email is NOT about a job application (e.g. newsletter, spam, unrelated), set isJobEmail to false and all other fields to null."""


# ---------------------------------------------------------------------------
# Gmail helpers
# ---------------------------------------------------------------------------

def build_gmail_service():
    """Build Gmail API service from base64-encoded GMAIL_TOKEN env var."""
    gmail_token_b64 = os.environ.get("GMAIL_TOKEN", "").strip()
    if not gmail_token_b64:
        print("ERROR: GMAIL_TOKEN environment variable not set", file=sys.stderr)
        sys.exit(1)

    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build
    except ImportError:
        print("ERROR: google-auth packages not installed. Run: pip install google-auth google-auth-httplib2 google-api-python-client", file=sys.stderr)
        sys.exit(1)

    token_json = base64.b64decode(gmail_token_b64).decode("utf-8")

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        f.write(token_json)
        token_path = f.name

    try:
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
        service = build("gmail", "v1", credentials=creds)
    finally:
        os.unlink(token_path)

    return service


def gmail_search(service, query, after_date_str):
    """Search Gmail and return list of message dicts."""
    full_query = f"{query} after:{after_date_str}"
    print(f"Gmail search query: {full_query}")

    messages = []
    page_token = None

    while True:
        kwargs = {
            "userId": "me",
            "q": full_query,
            "maxResults": 500,
        }
        if page_token:
            kwargs["pageToken"] = page_token

        result = service.users().messages().list(**kwargs).execute()
        batch = result.get("messages", [])
        messages.extend(batch)
        page_token = result.get("nextPageToken")
        if not page_token:
            break

    print(f"Found {len(messages)} candidate emails")
    return messages


def fetch_email_content(service, msg_id):
    """Fetch a single email and extract subject, sender, date, and body snippet."""
    msg = service.users().messages().get(
        userId="me", id=msg_id, format="full"
    ).execute()

    headers = {h["name"].lower(): h["value"] for h in msg.get("payload", {}).get("headers", [])}
    subject = headers.get("subject", "(no subject)")
    sender = headers.get("from", "unknown")
    date_str = headers.get("date", "")

    # Parse date
    try:
        from email.utils import parsedate_to_datetime
        date_parsed = parsedate_to_datetime(date_str)
        date_iso = date_parsed.astimezone(timezone.utc).isoformat()
    except Exception:
        date_iso = datetime.now(timezone.utc).isoformat()

    # Extract body text
    body = _extract_body(msg.get("payload", {}))
    # Truncate to keep Gemini prompt reasonable
    body_snippet = body[:3000] if body else msg.get("snippet", "")

    return {
        "id": msg_id,
        "subject": subject,
        "sender": sender,
        "date": date_iso,
        "body": body_snippet,
    }


def _extract_body(payload):
    """Recursively extract plain text body from Gmail payload."""
    mime_type = payload.get("mimeType", "")
    body_data = payload.get("body", {}).get("data", "")

    if mime_type == "text/plain" and body_data:
        return base64.urlsafe_b64decode(body_data + "==").decode("utf-8", errors="replace")

    # Recurse into parts
    for part in payload.get("parts", []):
        result = _extract_body(part)
        if result:
            return result

    # Fallback: try text/html and strip tags
    if mime_type == "text/html" and body_data:
        html = base64.urlsafe_b64decode(body_data + "==").decode("utf-8", errors="replace")
        return re.sub(r"<[^>]+>", " ", html)

    return ""


# ---------------------------------------------------------------------------
# Gemini classification
# ---------------------------------------------------------------------------

def classify_with_gemini(email_data):
    """Send email to Gemini Flash and return structured classification."""
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        print("ERROR: GEMINI_API_KEY environment variable not set", file=sys.stderr)
        sys.exit(1)

    user_prompt = f"""From: {email_data['sender']}
Subject: {email_data['subject']}
Date: {email_data['date']}

Body:
{email_data['body']}"""

    payload = json.dumps({
        "contents": [
            {"role": "user", "parts": [{"text": SYSTEM_PROMPT + "\n\n---\n\n" + user_prompt}]}
        ],
        "generationConfig": {
            "temperature": 0.1,
            "maxOutputTokens": 512,
        }
    }).encode("utf-8")

    url = f"{GEMINI_API_URL}?key={api_key}"
    req = Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")

    try:
        with urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except URLError as e:
        print(f"  Gemini API error: {e}", file=sys.stderr)
        return None

    # Extract text from response
    try:
        text = result["candidates"][0]["content"]["parts"][0]["text"].strip()
        # Strip markdown code fences if present
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE).strip()
        return json.loads(text)
    except (KeyError, IndexError, json.JSONDecodeError) as e:
        print(f"  Failed to parse Gemini response: {e} | Raw: {result}", file=sys.stderr)
        return None


# ---------------------------------------------------------------------------
# Application upsert logic
# ---------------------------------------------------------------------------

def make_app_id(company, role):
    """Stable ID for a company+role combination."""
    key = f"{(company or '').lower().strip()}::{(role or '').lower().strip()}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def status_rank(status):
    """Return numeric priority of a status (higher = better/later in pipeline)."""
    try:
        return STATUS_PRIORITY.index(status)
    except ValueError:
        return -1


def upsert_application(apps_list, classification, email_date, email_subject):
    """Insert or update an application entry. Never downgrades status."""
    company = (classification.get("company") or "").strip()
    role = (classification.get("role") or "").strip()
    new_status = classification.get("status") or "Applied"

    if not company:
        return  # Skip if we couldn't extract a company name

    app_id = make_app_id(company, role)

    # Find existing entry
    existing = next((a for a in apps_list if a["id"] == app_id), None)

    now_iso = datetime.now(timezone.utc).isoformat()

    if existing is None:
        # New application
        entry = {
            "id": app_id,
            "company": company,
            "role": role or "Unknown Role",
            "type": classification.get("type") or "Unknown",
            "status": new_status,
            "dateApplied": email_date,
            "deadline": classification.get("deadline"),
            "bookingLink": classification.get("bookingLink"),
            "applyUrl": classification.get("applyUrl"),
            "notes": classification.get("notes") or "",
            "lastUpdated": now_iso,
            "emailSubject": email_subject,
        }
        apps_list.append(entry)
        print(f"  ✅ NEW: {company} — {role} [{new_status}]")
    else:
        # Existing — only upgrade status, never downgrade
        old_rank = status_rank(existing["status"])
        new_rank = status_rank(new_status)

        updated = False
        if new_rank > old_rank:
            existing["status"] = new_status
            existing["lastUpdated"] = now_iso
            updated = True

        # Update deadline/bookingLink if newly found
        if classification.get("deadline") and not existing.get("deadline"):
            existing["deadline"] = classification["deadline"]
            existing["lastUpdated"] = now_iso
            updated = True

        if classification.get("bookingLink") and not existing.get("bookingLink"):
            existing["bookingLink"] = classification["bookingLink"]
            existing["lastUpdated"] = now_iso
            updated = True

        # Append new notes
        new_notes = (classification.get("notes") or "").strip()
        if new_notes and new_notes not in (existing.get("notes") or ""):
            existing["notes"] = ((existing.get("notes") or "") + " | " + new_notes).strip(" |")
            existing["lastUpdated"] = now_iso
            updated = True

        if updated:
            print(f"  🔄 UPDATE: {company} — {role} [{existing['status']}]")
        else:
            print(f"  ⏭  SKIP (no change): {company} [{existing['status']}]")


# ---------------------------------------------------------------------------
# Gist helpers
# ---------------------------------------------------------------------------

def load_gist(gist_id, filename, gist_token):
    """Load a file from a GitHub Gist. Returns parsed JSON or empty dict."""
    url = f"https://api.github.com/gists/{gist_id}"
    req = Request(url, headers={
        "Authorization": f"token {gist_token}",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "applications-agent/1.0",
    })
    try:
        with urlopen(req, timeout=15) as resp:
            gist = json.loads(resp.read().decode("utf-8"))
        content = gist.get("files", {}).get(filename, {}).get("content", "{}")
        return json.loads(content) if content.strip() else {}
    except Exception as e:
        print(f"Warning: could not load Gist ({e}), starting fresh", file=sys.stderr)
        return {}


def write_gist(gist_id, filename, data, gist_token):
    """Write data to a GitHub Gist file."""
    payload = json.dumps({
        "files": {
            filename: {"content": json.dumps(data, indent=2, ensure_ascii=False)}
        }
    }).encode("utf-8")

    url = f"https://api.github.com/gists/{gist_id}"
    req = Request(url, data=payload, headers={
        "Authorization": f"token {gist_token}",
        "Accept": "application/vnd.github.v3+json",
        "Content-Type": "application/json",
        "User-Agent": "applications-agent/1.0",
    }, method="PATCH")

    with urlopen(req, timeout=15) as resp:
        if resp.status not in (200, 201):
            raise RuntimeError(f"Gist write failed: HTTP {resp.status}")
    print(f"✅ Wrote {len(data.get('applications', []))} applications to Gist")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    gist_token = os.environ.get("GIST_TOKEN", "").strip()
    gist_id = os.environ.get("GIST_ID", "").strip()

    if not gist_token or not gist_id:
        print("ERROR: GIST_TOKEN and GIST_ID must be set", file=sys.stderr)
        sys.exit(1)

    # 1. Load existing data from Gist
    print("Loading existing applications from Gist...")
    data = load_gist(gist_id, "applications.json", gist_token)
    apps_list = data.get("applications", [])
    last_checked = data.get("lastChecked")

    # First-run detection: backfill 90 days
    if not last_checked:
        cutoff = datetime.now(timezone.utc) - timedelta(days=BACKFILL_DAYS)
        print(f"First run — scanning past {BACKFILL_DAYS} days (backfill from {cutoff.date()})")
    else:
        cutoff = datetime.fromisoformat(last_checked.replace("Z", "+00:00"))
        print(f"Incremental run — scanning since {cutoff.isoformat()}")

    after_date_str = cutoff.strftime("%Y/%m/%d")

    # 2. Build Gmail service and search
    print("Connecting to Gmail...")
    service = build_gmail_service()
    messages = gmail_search(service, GMAIL_QUERY, after_date_str)

    if not messages:
        print("No new emails to process.")
    else:
        # 3. Classify each email with Gemini
        processed = 0
        added_or_updated = 0

        for msg in messages:
            try:
                email_data = fetch_email_content(service, msg["id"])
                print(f"\nProcessing: {email_data['subject'][:80]}")

                classification = classify_with_gemini(email_data)
                if classification is None:
                    continue

                if classification.get("isJobEmail"):
                    before_count = len(apps_list)
                    upsert_application(
                        apps_list,
                        classification,
                        email_data["date"],
                        email_data["subject"],
                    )
                    added_or_updated += 1
                else:
                    print("  ⏭  Not a job email, skipping")

                processed += 1

            except Exception as e:
                print(f"  ERROR processing message {msg['id']}: {e}", file=sys.stderr)
                continue

        print(f"\nProcessed {processed}/{len(messages)} emails, {added_or_updated} job emails found")

    # 4. Sort applications: most recently updated first
    apps_list.sort(key=lambda a: a.get("lastUpdated", ""), reverse=True)

    # 5. Build summary stats
    status_counts = {}
    for app in apps_list:
        s = app.get("status", "Applied")
        status_counts[s] = status_counts.get(s, 0) + 1

    now_iso = datetime.now(timezone.utc).isoformat()
    data = {
        "generatedAt": now_iso,
        "lastChecked": now_iso,
        "totalApplications": len(apps_list),
        "statusCounts": status_counts,
        "applications": apps_list,
    }

    # 6. Write back to Gist
    write_gist(gist_id, "applications.json", data, gist_token)
    print(f"\nDone! Total applications tracked: {len(apps_list)}")
    for status, count in sorted(status_counts.items(), key=lambda x: status_rank(x[0]), reverse=True):
        print(f"  {status}: {count}")


if __name__ == "__main__":
    main()
