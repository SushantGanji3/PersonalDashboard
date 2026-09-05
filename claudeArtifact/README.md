# Claude Artifacts — Command Deck

This folder contains the source code for the published Claude Artifacts. These files run inside Claude's interactive sandbox runtime and interact natively with Claude connectors and databases.

## Published Artifact URLs

- **Command Deck (Main Dashboard)**:
  <https://claude.ai/code/artifact/b663355b-744d-40fa-b1f8-9516411cfaf7>
- **All New Grad SWE Jobs (30-Day Archive)**:
  <https://claude.ai/code/artifact/daae0bab-7a71-46e8-8767-8e11dd713bce>

## Files

- `dashboard.html` — The main Command Deck artifact. Uses:
  - `window.claude.use('mcp')` for live Gmail threads and Google Calendar events.
  - `window.claude.use('db')` for live New Grad SWE jobs and application tracking status.
- `applications.html` — Standalone artifact for browsing and filtering all tracked job applications. Uses `window.claude.use('db')` (`dashboard/applications`).
- `all-jobs.html` — Standalone artifact for browsing and searching the full 30-day jobs archive. Uses `window.claude.use('db')` (`dashboard/jobs_archive`).

## How to Update the Live Artifacts

Published Artifacts cannot be updated via Git pushes. When making changes to any `.html` file in this directory:
1. Open Claude Code or Claude.ai conversation.
2. Republish the file content to the **existing Artifact URL** (do not generate a new artifact ID).
3. Test inside Claude to verify that MCP connectors and DB subscriptions connect properly.
