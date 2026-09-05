# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

Source behind two separated targets:
1. **Claude Artifacts** (`claudeArtifact/`): Published Claude Artifacts running within Claude's interactive sandbox runtime:
   - Command Deck: <https://claude.ai/code/artifact/b663355b-744d-40fa-b1f8-9516411cfaf7>
   - All Jobs (30-Day Archive): <https://claude.ai/code/artifact/daae0bab-7a71-46e8-8767-8e11dd713bce>
   These pages use `window.claude.use('mcp')` (Gmail and Calendar) and `window.claude.use('db')` (Jobs and Applications). They do not perform external HTTP `fetch()` because of Claude Artifact CSP restrictions.

2. **Self-Hosted Vercel App & Sync Agents** (`Vercel/`): Static web deployment on Vercel at <https://personal-dashboard-blush-sigma.vercel.app/> backed by GitHub Actions automation in `.github/workflows/` that syncs Gmail, Calendar, and Jobs data to public GitHub Gists. The frontend polls these Gists via client-side `fetch()`.

## Directory Structure

```text
PersonalDashboard/
├── claudeArtifact/
│   ├── dashboard.html             # Command Deck artifact (MCP + DB)
│   ├── applications.html          # My Applications artifact (DB doc)
│   ├── all-jobs.html              # All Jobs 30-day archive artifact (DB doc)
│   └── README.md                  # Artifact URLs & publishing guide
│
├── Vercel/
│   ├── dashboard.html             # Vercel Command Deck (Gist polling via fetch())
│   ├── applications.html          # Vercel Applications tracker (Gist polling)
│   ├── all-jobs.html              # Vercel All Jobs archive (Gist polling)
│   ├── vercel.json                # Vercel routing configuration
│   ├── jobs_fetch.py              # Standalone job fetch & filter script
│   ├── jobs_sync.py               # Production sync script to GitHub Gist
│   └── agents/                    # Background sync agents
│       ├── calendar_sync/
│       │   └── calendar_sync.py   # Calendar sync agent
│       ├── gmail_sync/
│       │   └── gmail_sync.py      # Gmail inbox sync agent
│       └── job_tracker/
│           ├── applications_agent.py  # Gemini Flash job classifier & sync
│           ├── gmail_auth.py          # OAuth helper script
│           ├── GMAIL_SETUP.md
│           ├── client_secret.json (gitignored)
│           └── token.json (gitignored)
│
├── .github/
│   └── workflows/                 # Scheduled GitHub Actions cron jobs
│       ├── applications-agent.yml # Runs Vercel/agents/job_tracker/applications_agent.py
│       ├── calendar-sync.yml      # Runs Vercel/agents/calendar_sync/calendar_sync.py
│       ├── gmail-sync.yml         # Runs Vercel/agents/gmail_sync/gmail_sync.py
│       └── jobs-sync.yml          # Runs Vercel/jobs_sync.py
│
├── vercel.json                    # Root fallback rewrites to /Vercel/...
├── CLAUDE.md                      # This guidance file
├── README.md                      # Project overview
└── .gitignore                     # Git exclusions
```

## Commands

- **Run jobs fetch locally**: `python3 Vercel/jobs_fetch.py` (stdlib only, no deps to install).
  Prints one JSON object to stdout (`{generatedAt, count, postings}`); non-fatal feed failures go to stderr and that feed is skipped.
- **Run jobs sync locally**: `python3 Vercel/jobs_sync.py` (requires `GIST_TOKEN` with gist scope).
- **Run applications agent locally**: `python3 Vercel/agents/job_tracker/applications_agent.py` (requires `GMAIL_TOKEN`, `GEMINI_API_KEY`, `GIST_TOKEN`, `GIST_ID`).
- **Compile/test python scripts**: `python3 -m py_compile Vercel/jobs_fetch.py Vercel/jobs_sync.py`

## Updating the Live Claude Artifacts

Editing files in `claudeArtifact/` has no effect on its own. After making changes:
1. Open Claude Code or Claude.ai.
2. Republish the updated file content to the **existing Artifact URL** (do not generate a new artifact ID).

## Updating the Self-Hosted Page (Vercel)

The self-hosted copy is a static deployment on Vercel (project `personal-dashboard`, account `sushantganji3`). Deploy from the repo root:

```bash
npx vercel --prod --yes
```

Job postings, Gmail, and Calendar update dynamically in the user's browser via Gist polling, so redeploying is only necessary when HTML, styles, or client logic change.
