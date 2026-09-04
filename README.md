# Personal Dashboard — Command Deck

Two copies of the same page:

- **claude.ai Artifact**: <https://claude.ai/code/artifact/b663355b-744d-40fa-b1f8-9516411cfaf7>
- **Self-hosted (Vercel)**: <https://personal-dashboard-blush-sigma.vercel.app/>

This repo holds the source behind both. There is no build step — `dashboard.html` is the entire
page (inline `<style>`/`<script>`, no external JS deps) and gets deployed as-is to either target.

## Why two copies

The claude.ai Artifact enforces a CSP that blocks `fetch()`/XHR to external hosts, so it can't
pull live job data itself. The only in-Artifact way around that (`write_db`, called by an AI
agent) requires an interactive approval prompt every single time, with no way to pre-approve it
for an unattended/scheduled run — so the jobs card on that copy depends on a human clicking
approve. The self-hosted copy has neither problem: it's a plain static file polling a public gist
over `fetch()`, no AI or approval step anywhere in that path, so it updates on its own.

## Files

- `dashboard.html` — the full source of the dashboard (schedule, Gmail, jobs). To update either
  live copy, edit this file, then either ask Claude to republish it to the Artifact URL above, or
  run `npx vercel --prod --yes` to redeploy the self-hosted copy.
- `applications.html` — full job-application tracker view, linked from the dashboard.
- `all-jobs.html` — 30-day archive of every matched job posting, linked from the dashboard.
- `jobs_fetch.py` — standalone stdlib-only script that pulls new-grad/intern SWE postings. Pulls
  its filter config from `rishabhsabnavis/job-alerts`'s `sources.json` each run, then fetches the
  SimplifyJobs/vanshb03 aggregator feeds and filters them.
- `jobs_sync.py` — imports `fetch_and_filter()` from `jobs_fetch.py`, diffs against a rolling
  `seen.json` to flag new postings, and writes `jobs.json`/`seen.json` to a public gist. Run every
  10 minutes by `.github/workflows/jobs-sync.yml` on GitHub Actions.
- `vercel.json` — routes `/` to `dashboard.html` for the self-hosted deploy.
- `agents/job_tracker/` — a separate Gmail-based agent (own Google Cloud OAuth app, not Claude's
  Gmail connector) that scans for application-related emails to feed the applications tracker.

## What's on the dashboard

- **Schedule** — a static Fall 2026 Coursebook snapshot merged with live events pulled from all
  of your Google Calendars when the page is open.
- **Gmail** — live, today-only. The page queries sushantganji17@gmail.com every time you open it
  (via Claude's Gmail connector) and shows only messages from the current day.
- **New Grad SWE Jobs** — live, via two different paths depending on which copy you're viewing:
  - Artifact copy: subscribed to a shared database doc, meant to be refreshed by a scheduled
    Claude task — blocked on the `write_db` approval wall described above until that gate changes.
  - Self-hosted copy: polls the public gist that `jobs_sync.py` keeps fresh every 10 minutes.
- **Job applications** — tracked via `agents/job_tracker/`, viewable in full on `applications.html`.
- **Assignments (Canvas)** and **Outlook** — no dedicated cards; still linked from Quick links.

## Notes

- Rishabh's job-alerts repo (github.com/rishabhsabnavis/job-alerts) isn't copied here —
  `jobs_fetch.py` just points at its raw `sources.json` on GitHub each run.
- See `CLAUDE.md` for the full architecture writeup, including exactly why the two copies diverge
  and how to redeploy each one.
