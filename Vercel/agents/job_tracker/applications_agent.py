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
import time
from datetime import datetime, timedelta, timezone
from email import message_from_bytes
from urllib.request import Request, urlopen
from urllib.error import URLError
import urllib.parse

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
BACKFILL_DAYS = 90

# Gmail search query — intentionally very broad; Gemini filters false positives.
# Searches full email text (body + subject). The OR terms cover the most common
# phrases found in job application confirmation, OA, interview, and rejection emails.
GMAIL_QUERY = (
    '"application" OR "internship" OR "interview" OR '
    '"hackerrank" OR "codesignal" OR "assessment" OR '
    '"offer" OR "rejected" OR "not selected"'
)

# Status priority — higher index = higher priority, never downgrade
STATUS_PRIORITY = ["Applied", "OA", "Interview", "Offer", "Rejected"]

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

    # RFC Message-ID and direct Gmail link
    raw_rfc_id = headers.get("message-id", "").strip()
    clean_rfc_id = raw_rfc_id.strip("<> ").strip()
    thread_id = msg.get("threadId") or msg_id

    if clean_rfc_id:
        email_url = f"https://mail.google.com/mail/u/0/#search/rfc822msgid%3A{urllib.parse.quote(clean_rfc_id)}"
    else:
        email_url = f"https://mail.google.com/mail/u/0/#all/{thread_id}"

    return {
        "id": msg_id,
        "threadId": thread_id,
        "rfcMessageId": clean_rfc_id,
        "emailUrl": email_url,
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
# Gemini classification (supports modern google-genai and legacy fallback)
# ---------------------------------------------------------------------------

GENAI_CLIENT = None
WORKING_MODEL = None
VERIFIED_MODELS = []


def init_gemini():
    """Initialize Gemini client and verify a working model."""
    global GENAI_CLIENT, WORKING_MODEL, VERIFIED_MODELS
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        print("ERROR: GEMINI_API_KEY environment variable not set", file=sys.stderr)
        sys.exit(1)

    masked = api_key[:6] + "..." + api_key[-4:] if len(api_key) > 10 else "***"
    print(f"Connecting to Gemini (API key: {masked})...")

    # 1. Try modern google.genai SDK
    try:
        from google import genai
        GENAI_CLIENT = genai.Client(api_key=api_key)
        print("Using google.genai SDK.")
    except ImportError:
        print("Note: google.genai SDK not found, using google.generativeai", file=sys.stderr)

    # Discover models via list() if possible
    dynamic_candidates = []
    if GENAI_CLIENT:
        try:
            for item in GENAI_CLIENT.models.list():
                m_name = getattr(item, "name", "")
                if "flash" in m_name.lower() or "pro" in m_name.lower():
                    clean_name = m_name.replace("models/", "")
                    dynamic_candidates.append(clean_name)
            if dynamic_candidates:
                print(f"Discovered models from API: {dynamic_candidates}")
        except Exception as e:
            print(f"Note: models.list() probe skipped: {e}")

    # Prioritize production models with generous Free Tier quotas (1500 RPD)
    # over preview/experimental models like gemini-3.6-flash which have a strict 20 RPD cap.
    preferred = [
        "gemini-2.5-flash",
        "gemini-2.5-flash-lite",
        "gemini-flash-latest",
        "gemini-2.5-pro",
        "gemini-3.6-flash",
        "gemini-3.8-flash",
    ]
    candidates = []
    for m in preferred:
        if m in dynamic_candidates and m not in candidates:
            candidates.append(m)
    for m in preferred:
        if m not in candidates:
            candidates.append(m)
    for m in dynamic_candidates:
        if m not in candidates:
            candidates.append(m)

    VERIFIED_MODELS = []
    for m in candidates:
        try:
            if GENAI_CLIENT:
                resp = GENAI_CLIENT.models.generate_content(
                    model=m,
                    contents="Say OK",
                )
                if resp and resp.text:
                    VERIFIED_MODELS.append(m)
                    print(f"  Verified model candidate: {m}")
                    if len(VERIFIED_MODELS) >= 3:
                        break
            else:
                import google.generativeai as legacy_genai
                legacy_genai.configure(api_key=api_key)
                model = legacy_genai.GenerativeModel(m)
                resp = model.generate_content("Say OK")
                if resp and resp.text:
                    VERIFIED_MODELS.append(m)
                    print(f"  Verified model candidate (legacy SDK): {m}")
                    if len(VERIFIED_MODELS) >= 3:
                        break
        except Exception as e:
            print(f"  Probe {m} failed ({type(e).__name__}): {e}", file=sys.stderr)

    if VERIFIED_MODELS:
        WORKING_MODEL = VERIFIED_MODELS[0]
        print(f"✅ Gemini model selected: {WORKING_MODEL} (available fallbacks: {VERIFIED_MODELS[1:]})")
        return True

    print("❌ ERROR: All Gemini model probes failed. Verify your GEMINI_API_KEY in GitHub Secrets.", file=sys.stderr)
    return False


def classify_with_gemini(email_data):
    """Send email to Gemini and return structured classification with retry and model failover."""
    global GENAI_CLIENT, WORKING_MODEL, VERIFIED_MODELS

    user_prompt = f"""From: {email_data['sender']}
Subject: {email_data['subject']}
Date: {email_data['date']}

Body:
{email_data['body']}"""

    max_retries = 3
    for attempt in range(max_retries):
        model_name = WORKING_MODEL or "gemini-2.5-flash"
        try:
            if GENAI_CLIENT:
                from google.genai import types
                response = GENAI_CLIENT.models.generate_content(
                    model=model_name,
                    contents=user_prompt,
                    config=types.GenerateContentConfig(
                        system_instruction=SYSTEM_PROMPT,
                        temperature=0.1,
                        response_mime_type="application/json",
                    )
                )
                text = (response.text or "").strip()
                text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE).strip()
                return json.loads(text)
            else:
                import google.generativeai as legacy_genai
                full_prompt = SYSTEM_PROMPT + "\n\n---\n\n" + user_prompt
                model = legacy_genai.GenerativeModel(
                    model_name,
                    generation_config={"temperature": 0.1, "max_output_tokens": 512}
                )
                response = model.generate_content(full_prompt)
                text = (response.text or "").strip()
                text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE).strip()
                return json.loads(text)
        except json.JSONDecodeError as e:
            print(f"  Failed to parse Gemini JSON ({model_name}): {e}", file=sys.stderr)
            return None
        except Exception as e:
            err_str = str(e)
            print(f"  Gemini classification attempt {attempt+1} error ({model_name}, {type(e).__name__}): {e}", file=sys.stderr)

            # Check if 429 quota / resource exhaustion
            if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                if "GenerateRequestsPerDay" in err_str:
                    print(f"  ⚠️ Daily quota reached for {model_name}!", file=sys.stderr)
                    if VERIFIED_MODELS:
                        idx = VERIFIED_MODELS.index(model_name) if model_name in VERIFIED_MODELS else -1
                        if idx + 1 < len(VERIFIED_MODELS):
                            WORKING_MODEL = VERIFIED_MODELS[idx + 1]
                            print(f"  🔄 Switching to fallback model: {WORKING_MODEL}", file=sys.stderr)
                            time.sleep(2)
                            continue
                    return None

                # RPM rate limit: wait retryDelay or default 15s
                retry_delay = 15
                match = re.search(r"retry in (\d+(?:\.\d+)?)s", err_str, re.IGNORECASE)
                if match:
                    retry_delay = max(5, int(float(match.group(1))) + 2)
                print(f"  ⏳ Hit RPM rate limit. Waiting {retry_delay}s before retry...", file=sys.stderr)
                time.sleep(retry_delay)
                continue

            elif "503" in err_str or "UNAVAILABLE" in err_str:
                print(f"  ⏳ Service unavailable (503). Waiting 10s before retry...", file=sys.stderr)
                time.sleep(10)
                continue

            return None

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


def upsert_application(apps_list, classification, email_date, email_subject, email_url=None, email_id=None, thread_id=None, rfc_message_id=None):
    """Insert or update an application entry. Never downgrades status. Always links to latest email."""
    company = (classification.get("company") or "").strip()
    role = (classification.get("role") or "").strip()
    new_status = classification.get("status") or "Applied"

    if not company:
        return  # Skip if we couldn't extract a company name

    # Skip non-job applications (e.g. apartment leasing / rental applications)
    company_lower = company.lower()
    if any(term in company_lower for term in ["northside", "apartment", "rental", "lease", "guarantor"]):
        return

    app_id = make_app_id(company, role)

    # Find existing entry
    existing = next((a for a in apps_list if a["id"] == app_id), None)
    # If not matched by exact role, check if company matches with an unknown or empty role
    if existing is None and role and role != "Unknown Role":
        existing = next((a for a in apps_list if a.get("company", "").lower() == company.lower() and a.get("role") in ("Unknown Role", "", None)), None)
        if existing:
            existing["role"] = role
            existing["id"] = app_id

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
            "emailUrl": email_url,
            "emailId": email_id,
            "threadId": thread_id,
            "rfcMessageId": rfc_message_id,
        }
        apps_list.append(entry)
        print(f"  ✅ NEW: {company} — {role} [{new_status}]")
    else:
        # Always update email link & subject to point to the latest email regarding this application
        if email_url:
            existing["emailUrl"] = email_url
            existing["emailId"] = email_id
            existing["threadId"] = thread_id
            existing["rfcMessageId"] = rfc_message_id
            existing["emailSubject"] = email_subject

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

    # 1. Initialize and probe Gemini API first
    if not init_gemini():
        print("Aborting run because no working Gemini model could be verified. Gist is untouched.", file=sys.stderr)
        sys.exit(1)

    # 2. Load existing data from Gist
    print("Loading existing applications from Gist...")
    data = load_gist(gist_id, "applications.json", gist_token)
    apps_list = data.get("applications", [])
    last_checked = data.get("lastChecked")

    force_backfill = (
        os.environ.get("FORCE_BACKFILL", "").strip().lower() in ("true", "1", "yes")
        or "--backfill" in sys.argv
    )

    # Backfill detection: if force_backfill is set, or lastChecked is missing,
    # or if very few applications exist (< 10, indicating an interrupted initial run)
    if force_backfill or not last_checked or len(apps_list) < 10:
        cutoff = datetime.now(timezone.utc) - timedelta(days=BACKFILL_DAYS)
        reason = "force backfill" if force_backfill else f"initial/recovery run ({len(apps_list)} existing applications)"
        print(f"Backfill mode ({reason}) — scanning past {BACKFILL_DAYS} days (from {cutoff.date()})")
    else:
        cutoff = datetime.fromisoformat(last_checked.replace("Z", "+00:00"))
        print(f"Incremental run — scanning since {cutoff.isoformat()}")

    after_date_str = cutoff.strftime("%Y/%m/%d")

    # 3. Build Gmail service and search
    print("Connecting to Gmail...")
    service = build_gmail_service()
    messages = gmail_search(service, GMAIL_QUERY, after_date_str)

    # Pre-filtering rules to reject non-job marketing/notifications before calling Gemini:
    # 1. Senders to always skip (promos, financial, job board digests, GitHub notifications)
    SENDER_BLACKLIST = [
        "notifications@github.com",
        "jobalerts-noreply@linkedin.com",
        "messages-noreply@linkedin.com",
        "invitations@linkedin.com",
        "americanexpress.com",
        "welcome.aexp.com",
        "orders.apple.com",
        "apple.com",
        "chase.com",
        "capitalone.com",
        "discover.com",
        "citi.com",
        "wellsfargo.com",
        "rentflex.com",
    ]

    # 2. Subjects to always skip (promos, rental/lease, salary digests, GitHub notifications)
    SUBJECT_BLACKLIST = [
        "amex", "apple card", "credit limit", "bonus miles", "points offer",
        "special offer", "exclusive offers", "initiation", "spa weekend",
        "split rent", "rental application", "guarantor application",
        "lease application", "northside", "[sushantganji3/", "run failed:",
        "run succeeded:", "up to $", "$/hr", "is hiring for a", "is hiring a",
        "applied science", "applied scientist", "applied materials",
        "applied research", "applied power", "application engineer",
        "application security", "forage /", "vacay", "surface lineup",
    ]

    # 3. Known Applicant Tracking Systems (ATS) — always inspect if sender is from here
    ATS_DOMAINS = [
        "greenhouse.io", "lever.co", "myworkday.com", "myworkdayjobs.com",
        "ashbyhq.com", "smartrecruiters.com", "jobvite.com", "icims.com",
        "workablemail.com", "hirevue.com", "hackerrank.net", "codesignal.com",
        "codility.com",
    ]

    # 4. Patterns indicating genuine job application, OA, interview, or decision emails
    JOB_APPLICATION_PATTERNS = [
        "thank you for applying", "thank you for your application", "thanks for applying",
        "your application", "application received", "application submitted",
        "application successfully", "received online application", "online application submitted",
        "applied to", "interview", "assessment", "hackerrank", "codesignal",
        "codility", "hirevue", "karat", "job offer", "offer letter",
        "offer of employment", "update on your", "update regarding your",
        "referred to a job", "your candidacy", "not moving forward",
        "not selected", "unfortunately",
    ]

    def email_looks_like_job(subject, sender):
        s = (subject or "").lower()
        snd = (sender or "").lower()

        if any(bad in snd for bad in SENDER_BLACKLIST):
            return False

        if any(bad in s for bad in SUBJECT_BLACKLIST):
            return False

        if any(ats in snd for ats in ATS_DOMAINS):
            return True

        return any(pat in s for pat in JOB_APPLICATION_PATTERNS)

    if not messages:
        print("No emails matched the search query.")
        job_candidates = []
        processed = 0
    else:
        print(f"\nStep 1: Fetching subjects for {len(messages)} candidate emails...")

        # 3a. Fetch just metadata (subject + from) for all emails — cheap API call
        #     Then pre-filter before fetching full body
        job_candidates = []
        skipped = 0
        for i, msg in enumerate(messages):
            try:
                m = service.users().messages().get(
                    userId="me", id=msg["id"], format="metadata",
                    metadataHeaders=["Subject", "From", "Date"]
                ).execute()
                hdrs = {h["name"].lower(): h["value"]
                        for h in m.get("payload", {}).get("headers", [])}
                subject = hdrs.get("subject", "")
                sender = hdrs.get("from", "")

                if email_looks_like_job(subject, sender):
                    job_candidates.append({
                        "id": msg["id"],
                        "subject": subject,
                        "sender": sender,
                    })
                else:
                    skipped += 1

                # Rate limit: pause every 10 fetches to avoid quota errors
                if (i + 1) % 10 == 0:
                    time.sleep(0.5)
                else:
                    time.sleep(0.1)

            except Exception as e:
                print(f"  Warning: could not fetch metadata for {msg['id']}: {e}", file=sys.stderr)
                time.sleep(1)  # back off on error
                continue

        print(f"Pre-filter: {len(job_candidates)} relevant job emails identified ({skipped} irrelevant skipped)")

        # 3b. For each job candidate, fetch full body and classify with Gemini
        processed = 0
        added_or_updated = 0

        for i, candidate in enumerate(job_candidates):
            try:
                print(f"\n[{i+1}/{len(job_candidates)}] {candidate['subject'][:80]}")

                email_data = fetch_email_content(service, candidate["id"])
                time.sleep(0.3)  # rate limit Gmail full-body fetches

                classification = classify_with_gemini(email_data)
                time.sleep(3.5)  # pace Gemini calls (~17 RPM, well within Free Tier limits)

                if classification is None:
                    continue

                if classification.get("isJobEmail"):
                    upsert_application(
                        apps_list,
                        classification,
                        email_data["date"],
                        email_data["subject"],
                        email_url=email_data.get("emailUrl"),
                        email_id=email_data.get("id"),
                        thread_id=email_data.get("threadId"),
                        rfc_message_id=email_data.get("rfcMessageId"),
                    )
                    added_or_updated += 1
                else:
                    print("  ⏭  Not a job email, skipping")

                processed += 1

            except Exception as e:
                print(f"  ERROR: {e}", file=sys.stderr)
                time.sleep(2)  # back off on error
                continue

        print(f"\nProcessed {processed}/{len(job_candidates)} job candidates, {added_or_updated} applications added/updated")

    # 4. Sort applications: most recently updated first
    apps_list.sort(key=lambda a: a.get("lastUpdated", ""), reverse=True)

    # 5. Build summary stats
    status_counts = {}
    for app in apps_list:
        s = app.get("status", "Applied")
        status_counts[s] = status_counts.get(s, 0) + 1

    now_iso = datetime.now(timezone.utc).isoformat()
    # Avoid advancing lastChecked if significant errors prevented processing candidates
    fail_threshold = len(job_candidates) * 0.75
    if job_candidates and processed < fail_threshold:
        print(f"\n⚠️ WARNING: Only {processed}/{len(job_candidates)} candidates processed successfully. Preserving lastChecked.")
        new_last_checked = last_checked or cutoff.isoformat()
    else:
        new_last_checked = now_iso

    data = {
        "generatedAt": now_iso,
        "lastChecked": new_last_checked,
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
