# Personal Dashboard — Command Deck

Two targets with cleanly separated components:

- **Claude Artifacts** (`claudeArtifact/`): <https://claude.ai/code/artifact/b663355b-744d-40fa-b1f8-9516411cfaf7>
- **Self-Hosted App** (`Vercel/`): <https://personal-dashboard-blush-sigma.vercel.app/>

## Directory Organization

```text
PersonalDashboard/
├── claudeArtifact/                # Claude Artifact pages (MCP & Claude DB)
│   ├── dashboard.html             # Command Deck artifact
│   ├── applications.html          # My Applications artifact
│   ├── all-jobs.html              # 30-Day SWE Jobs Archive artifact
│   └── README.md                  # Artifact IDs and publishing guide
│
├── Vercel/                        # Self-hosted frontend, backend sync & agents
│   ├── dashboard.html             # Web dashboard (polls Gists via fetch())
│   ├── applications.html          # Web application tracker
│   ├── all-jobs.html              # Web jobs archive
│   ├── vercel.json                # Vercel routing
│   ├── jobs_fetch.py              # Aggregator fetcher
│   ├── jobs_sync.py               # Gist sync runner
│   └── agents/                    # Data sync agents (feed Gists for Vercel)
│       ├── calendar_sync/         # Google Calendar sync agent
│       ├── gmail_sync/            # Gmail inbox sync agent
│       └── job_tracker/           # Applications agent (Gemini Flash)
│
├── .github/                       # GitHub Actions cron workflows
│   └── workflows/
│       ├── applications-agent.yml
│       ├── calendar-sync.yml
│       ├── gmail-sync.yml
│       └── jobs-sync.yml
│
├── vercel.json                    # Root fallback rewrites
├── CLAUDE.md                      # Guidance for Claude Code
├── README.md                      # This overview
└── .gitignore                     # Git exclusions
```

## Why Two Folders

Published Claude Artifacts enforce a strict Content Security Policy (CSP) blocking external `fetch()`/XHR calls. In the artifact runtime, live data is accessed natively via `window.claude.use('mcp')` (Gmail & Calendar connectors) and `window.claude.use('db')`.

The self-hosted Vercel copy runs outside of Claude, so it cannot access Claude's in-browser connectors. Instead, background GitHub Actions cron jobs (`.github/workflows/`) run standalone Python scripts in `Vercel/agents/` and `Vercel/jobs_sync.py` to push structured snapshots to public GitHub Gists, which `Vercel/*.html` polls directly via `fetch()`.

## Deploying Updates

- **Claude Artifacts**: After editing files in `claudeArtifact/`, paste or republish the contents to the existing Artifact URLs in Claude.
- **Vercel**: Run `npx vercel --prod --yes` from the repo root to deploy changes to the live site.
