# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

This is *not* a runnable web app. `dashboard.html` is the source for a page published
as a Claude Artifact — the live page (schedule, Gmail, jobs) at
https://claude.ai/code/artifact/b663355b-744d-40fa-b1f8-9516411cfaf7 is what the user
actually sees. This repo is a local backup/reference copy of that source, plus the
standalone script that feeds one of its live sections.

There is no build, lint, or test tooling in this repo — it's two static files.

## Updating the live dashboard

The published Artifact and `dashboard.html` are separate copies. To ship a change:

1. Edit `dashboard.html` directly (plain HTML/CSS/vanilla JS, no framework, no bundler).
2. Republish it to the *same* Artifact URL above so the live page picks it up —
   publishing without reusing that URL creates a disconnected duplicate.
3. Commit the change to this repo so the local copy stays in sync with what's live.

## Architecture of `dashboard.html`

Single HTML file with three data sources, each wired up independently in the bottom
`<script>` block:

- **Class schedule** — fully static. Hardcoded in the `COURSES` array (Fall 2026 data
  pulled from Coursebook). `renderSchedule()` / `renderUpNext()` / `computeNextClass()`
  derive the week grid and the "up next" card from it on a 30s `tick()` interval — no
  network calls, so a semester schedule change means editing `COURSES` by hand.
- **Gmail card** — live via the Claude Artifact `mcp` capability. `initGmail()` calls
  `window.claude.use('mcp')` and does `mcp.watchTool('Gmail', 'search_threads', ...)`,
  re-polling every 5 minutes (`refetchInterval: 300000`). Rendering and error states
  (`needs_reauth`, `not_granted`, `selection_required`, etc.) are handled in
  `renderGmailThreads()` / `initGmail()`'s callback — this only works when viewed as
  the published Artifact with a Gmail connector attached; there's no live data in a
  plain browser open of the file.
- **New Grad SWE Jobs card** — live via the Claude Artifact `db` capability, *not* a
  direct fetch from this page. `initJobs()` subscribes to the shared doc
  `dashboard/jobs` (`db.doc('dashboard/jobs').onSnapshot(...)`) and renders whatever
  is there. That doc is populated externally by a scheduled Claude task that runs the
  same filtering logic as `jobs_fetch.py` every 3 hours — editing `jobs_fetch.py` does
  not change the dashboard until that scheduled task (or a manual republish of its
  logic) picks up the change.

All three sections fail gracefully to an `.empty-state` message when the runtime
capability isn't available (e.g. viewing the raw file, or a connector not granted).

The CSS uses a light/dark palette defined as custom properties on `:root`, overridden
under `prefers-color-scheme: dark` and `[data-theme="dark"]` — keep both in sync when
changing colors, since the Artifact host can force either theme.

## `jobs_fetch.py`

Standalone, stdlib-only script (no dependencies to install). Run directly:

```
python3 jobs_fetch.py
```

It pulls filter config and the target company/alias list from
`rishabhsabnavis/job-alerts`'s `sources.json` on GitHub on every run (so tuning done
in that repo applies automatically here, no local config to update), then fetches the
SimplifyJobs/vanshb03 aggregator `listings.json` feeds and filters them by:

- role/level keyword match against the target seniority (new grad / intern), rejecting
  senior/staff/lead titles and non-engineering roles (`matches()`)
- optional PhD-only and excluded-grad-year filtering
- optional US-only location filtering (`is_us()`, using state-code and keyword heuristics)

Matching postings are deduped by `(company, title)` via `collapse()` and printed as one
JSON object to stdout: `{"generatedAt", "count", "postings": [...]}`.

Note: per-company ATS endpoints (Greenhouse/Lever/Ashby/etc.) are blocked by this
environment's network egress — only the aggregator feeds are reachable, which the
script's docstring notes still covers ~850+ matching open roles across the target
company list.
