#!/usr/bin/env python3
"""
Production sync for the dashboard's "New Grad SWE Jobs" card, run on a
schedule by .github/workflows/jobs-sync.yml (every 30 min, like
rishabhsabnavis/job-alerts' own GitHub Actions cron).

Unlike the Claude Cowork routine this replaces, there is no AI/permission
step in the loop: this is a plain script writing to a plain file, exactly
like poll.py commits seen.json back to job-alerts. State and output both
live in one public GitHub Gist (GIST_ID below) instead of a git commit,
since a gist gives a stable public raw URL the dashboard can fetch from
client-side without exposing the rest of this (private) repo:

  - jobs.json  -- the doc the dashboard's jobs card reads and renders
  - seen.json  -- rolling id list (cap 1500) used to compute "isNew"

Auth: needs a GitHub token with the "gist" scope in the GIST_TOKEN env var
(set as a repo secret for the GitHub Actions run; for local testing you can
export GIST_TOKEN=$(gh auth token) if your gh login has gist scope).

Stdlib only, like jobs_fetch.py.
"""
import datetime
import json
import os
import sys
import urllib.request

from jobs_fetch import fetch_and_filter

GIST_ID = "6b80cfab682273f7a781d035f5178bd9"
GIST_API = f"https://api.github.com/gists/{GIST_ID}"
UA = "Mozilla/5.0 (dashboard-jobs-sync; personal use)"
TIMEOUT = 25
SEEN_CAP = 1500
ITEMS_CAP = 60


def gh_request(method, url, token, body=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "User-Agent": UA,
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
    })
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def read_gist_file(token, filename, default):
    gist = gh_request("GET", GIST_API, token)
    raw_url = gist["files"][filename]["raw_url"]
    req = urllib.request.Request(raw_url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except Exception as e:
        print(f"  could not read {filename} from gist, using default: {e}", file=sys.stderr)
        return default


def write_gist_files(token, files):
    body = {"files": {name: {"content": content} for name, content in files.items()}}
    gh_request("PATCH", GIST_API, token, body)


def main():
    token = os.environ.get("GIST_TOKEN")
    if not token:
        print("GIST_TOKEN env var is required", file=sys.stderr)
        sys.exit(1)

    postings = fetch_and_filter()
    if not postings:
        print("fetch_and_filter() returned nothing -- leaving gist untouched", file=sys.stderr)
        return

    seen = read_gist_file(token, "seen.json", {"ids": [], "updatedAt": None})
    seen_ids = set(seen.get("ids") or [])

    items = []
    run_ids = []
    for p in postings[:ITEMS_CAP]:
        run_ids.append(p["id"])
        items.append({
            "id": p["id"],
            "company": p["company"],
            "title": p["title"],
            "url": p["url"],
            "location": p.get("location", ""),
            "datePosted": p.get("date_posted"),
            "isNew": p["id"] not in seen_ids,
        })

    now = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

    jobs_doc = {
        "generatedAt": now,
        "totalOpen": len(postings),
        "source": "job-alerts (rishabhsabnavis) via SimplifyJobs/vanshb03 aggregator feeds",
        "items": items,
    }

    union_ids = list(seen_ids.union(run_ids))
    if len(union_ids) > SEEN_CAP:
        # rolling window: keep this run's ids plus the most recent of the rest
        keep_old = [i for i in union_ids if i not in run_ids][: SEEN_CAP - len(run_ids)]
        union_ids = run_ids + keep_old
    seen_doc = {"ids": union_ids, "updatedAt": now}

    write_gist_files(token, {
        "jobs.json": json.dumps(jobs_doc, indent=2),
        "seen.json": json.dumps(seen_doc, indent=2),
    })
    print(f"synced {len(items)} items ({sum(1 for i in items if i['isNew'])} new), seen list now {len(union_ids)} ids")


if __name__ == "__main__":
    main()
