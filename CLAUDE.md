# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

Source behind a single published Claude Artifact — a personal dashboard ("Command Deck") at
<https://claude.ai/code/artifact/b663355b-744d-40fa-b1f8-9516411cfaf7> — plus a real GitHub
Actions automation that keeps a *self-hosted* copy of the same page fed with live jobs data. The
self-hosted copy is deployed as a static site on Vercel at
<https://personal-dashboard-blush-sigma.vercel.app/> (this repo stays private; only the built
static files are public). There is no build system or package manager: `dashboard.html` is the
entire artifact (inline `<style>` and `<script>`, no external JS deps), and `jobs_fetch.py` /
`jobs_sync.py` are standalone stdlib-only Python scripts. The live claude.ai Artifact page does
**not** depend on anything in this repo running — see "New Grad SWE Jobs" below for why a
self-hosted copy needs the GitHub Actions workflow but the Artifact copy doesn't.

The two copies exist because the claude.ai Artifact has a hard limitation the self-hosted copy
doesn't: a published Artifact's CSP blocks all `fetch`/`XHR` to external hosts, and the only
in-Artifact way to get live data past that (`write_db`, called by an AI agent) requires an
interactive approval prompt every time with no way to pre-approve it for an unattended/scheduled
run (confirmed via the stuck Cowork trigger below). The self-hosted copy sidesteps both problems
by being a plain static file polling a public gist via `fetch()` — no AI or approval step
anywhere in that path. This is a structural gap in the Artifact runtime, not a bug in this repo;
if a future Artifact runtime version allows pre-approved/unattended `write_db` calls, the
Cowork-task path becomes viable and this two-copy setup could be collapsed.

## Commands

- Run the jobs fetch script locally: `python3 jobs_fetch.py` (stdlib only, no deps to install).
  It prints one JSON object to stdout (`{generatedAt, count, postings}`); non-fatal feed
  failures go to stderr and that feed is skipped. `jobs_sync.py` imports `fetch_and_filter()`
  from this file rather than duplicating the fetch/filter pipeline.
- There is no lint/test/build command — verify `dashboard.html` changes by publishing to the
  Artifact and checking the page in the browser (see "Updating the live page" below).

## Architecture

**`dashboard.html` is the source of truth for the live page.** To change what's on the
dashboard, edit this file, then republish it to the *same* Artifact URL above (do not create a
new artifact) — the live page is what users actually see, this file is only a local copy.

The page has three sections with very different data-freshness models, reflected in the
`chip` badge on each card:

- **Schedule** (`COURSES` array, top of the `<script>` block) — static, hand-entered from
  Coursebook for the current semester. Update this array directly when the semester/schedule
  changes; there's no fetch behind it.
- **Gmail** — live, client-side. `initGmail()` calls `window.claude.use('mcp')` and
  `mcp.watchTool('Gmail','search_threads', ...)` directly from the browser each time the page
  is open (`refetchInterval: 300000`ms). No server-side sync involved. Only works when the page
  is rendered inside the Claude Artifact runtime.
- **New Grad SWE Jobs** — live, via **two independent paths** that `initJobs()` picks between
  based on whether `window.claude` exists:
  - *On the published Artifact*: `window.claude.use('db')`, subscribed to
    `db.doc('dashboard/jobs')`. That doc is meant to be populated out-of-band by a scheduled
    Claude Cowork task (not code in this repo) — as of the last check that task exists
    (`trig_01TkFJUfkH53R79AyUKEFnmY`, "Dashboard: refresh new-grad SWE jobs", every 3h) but was
    stuck: it wrote its output to a local temp file and passed `file_path` to the Artifact
    `write_db` call, which requires an interactive permission approval no unattended run can
    give, so every run silently hung at the last step. Fixing that routine's prompt to write via
    inline `data` instead of `file_path` was not done as part of this change — this bullet
    documents the known issue, not a fix.
  - *Everywhere else (local file, localhost, self-hosted)*: `window.claude` doesn't exist, so
    `initJobsFromGist()` polls a public GitHub Gist every 5 minutes via plain `fetch()` instead.
    This path exists because published Artifacts enforce a CSP that silently blocks `fetch()`/XHR
    to arbitrary external hosts, so the gist path can *only* ever work off-Artifact — it was
    built for the self-hosted copy, not as a fix for the live claude.ai page.
- **Assignments (Canvas)** and **Outlook** cards, and the Coursebook/Academic Calendar quick
  links, were removed from the dashboard — Canvas and Outlook are still linked from "Quick
  links" but have no dedicated cards.

**`jobs_sync.py` is what `.github/workflows/jobs-sync.yml` runs every 30 minutes** (matching the
cadence of the `rishabhsabnavis/job-alerts` repo this filtering logic is modeled on). It calls
`fetch_and_filter()` from `jobs_fetch.py`, diffs against a rolling `seen.json` (cap 1500 ids) to
compute `isNew`, and writes both `jobs.json` (what the dashboard renders) and `seen.json` to a
public gist (id `6b80cfab682273f7a781d035f5178bd9`, owned by SushantGanji3) via the GitHub Gists
API. It needs a `GIST_TOKEN` repo secret scoped to `gist` only — GitHub gives no API for minting
that token, so it has to be created manually at github.com/settings/tokens and set with
`gh secret set GIST_TOKEN`. This mirrors job-alerts' own design closely: a plain script on a
plain cron with no AI/approval step in the loop, persisting state to a place with a stable public
URL — a gist here in place of job-alerts' own committed `seen.json`.

**`jobs_fetch.py` pulls its own config remotely.** Filter rules and the target company
allowlist live in `sources.json` in a separate GitHub repo
(`rishabhsabnavis/job-alerts`, fetched raw at `SOURCES_URL`), not in this repo — the script
re-fetches that config every run. It then hits only the SimplifyJobs/vanshb03 aggregator
`listings.json` feeds (per-company ATS endpoints like Greenhouse/Lever are blocked in the
Claude sandbox network egress `jobs_fetch.py` was originally written for — this doesn't apply to
`jobs_sync.py` running on a GitHub Actions runner, which has unrestricted egress, but the direct
ATS feeds were never wired in there either; both scripts still only use the aggregator feeds).
Filtering pipeline, in order: role/level regex match → optional PhD-only exclusion → optional
excluded-grad-year exclusion → optional US-only location filter → dedupe by (company, title) via
`collapse()` → sort newest-first → cap at 60.

## Updating the live page

Editing `dashboard.html` in this repo has no effect on its own — after making changes, ask
Claude to republish it to the existing Artifact URL (not a new one) so the live page picks up
the edit.

## Updating the self-hosted page

The self-hosted copy is a static deploy on Vercel (project `personal-dashboard`, account
`sushantganji3`), configured by `vercel.json` (a single rewrite so `/` serves `dashboard.html`).
It is **not** wired to auto-deploy on push — the GitHub repo connection failed on first deploy
and wasn't retried, and isn't actually needed: job postings refresh live in the browser via
`initJobsFromGist()`, independent of any redeploy. A redeploy is only needed when
`dashboard.html` (or another served file) itself changes:

```bash
npx vercel --prod --yes
```

run from the repo root, after `npx vercel login` once per machine.
