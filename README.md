# Personal Dashboard — Command Deck

Live page: https://claude.ai/code/artifact/b663355b-744d-40fa-b1f8-9516411cfaf7

This folder holds local copies of the source files behind that page. The live page
itself is hosted by Claude (that URL), so nothing here needs to run for the dashboard
to work — these are just backups / reference copies.

## Files

- `dashboard.html` — the full source of the published dashboard page (schedule, Gmail,
  assignments, Outlook, jobs sections). To update the live page, edit this file and
  ask Claude to republish it to the same URL above.
- `jobs_fetch.py` — standalone script that pulls new-grad/intern SWE postings. Pulls
  its config from Rishabh's job-alerts repo (sources.json) each run, then fetches the
  SimplifyJobs/vanshb03 aggregator feeds and filters them. This is the same logic a
  scheduled Claude task runs every 3 hours to refresh the dashboard's jobs card.

## What's live right now

- **Class schedule** — static, pulled from your Coursebook "My Classes" for Fall 2026.
  Won't change during the semester, so it's baked into the page.
- **Gmail** — live. The page queries sushantganji17@gmail.com directly every time you
  open it (via Claude's Gmail connector), no separate syncing needed.
- **New Grad SWE Jobs** — live. Refreshed every 3 hours by a scheduled Claude task
  (name: "Dashboard: refresh new-grad SWE jobs") that re-pulls Rishabh's job-alerts
  filters + the aggregator feeds and flags newly-seen postings.
- **Assignments (Canvas)** — not yet connected. Needs a Canvas personal access token:
  elearning.utdallas.edu → Account → Settings → "New Access Token", then send the
  token to Claude to wire it up.
- **Outlook (sxg220252@utdallas.edu)** — not yet connected. Needs the Microsoft 365
  connector added in claude.ai → Settings → Connectors (university tenants sometimes
  block this, worth trying anyway).

## Notes

- The dashboard's live sections (Gmail, jobs) read from a small shared database
  attached to the published page — not from anything in this folder.
- Rishabh's original job-alerts repo (github.com/rishabhsabnavis/job-alerts) is not
  copied here — `jobs_fetch.py` just points at its raw sources.json on GitHub each run.
