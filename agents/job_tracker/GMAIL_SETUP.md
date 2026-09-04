# Gmail API Setup Guide

One-time setup to give the Job Application Tracker agent access to your Gmail.

---

## Step 1 — Create a Google Cloud Project

1. Go to [console.cloud.google.com](https://console.cloud.google.com)
2. Click the project dropdown at the top → **New Project**
3. Name: `ApplicationTracker` → **Create**
4. Make sure the new project is selected in the dropdown

---

## Step 2 — Enable the Gmail API

1. Go to **APIs & Services → Library**
2. Search for `Gmail API`
3. Click it → **Enable**

---

## Step 3 — Configure OAuth Consent Screen

1. Go to **APIs & Services → OAuth consent screen**
2. Select **External** → **Create**
3. Fill in:
   - App name: `ApplicationTracker`
   - User support email: `sushantganji17@gmail.com`
   - Developer contact email: `sushantganji17@gmail.com`
4. Click **Save and Continue** through the rest (no scopes needed here)
5. On the **Test users** page → **Add users** → add `sushantganji17@gmail.com`
6. **Save and Continue** → **Back to Dashboard**

---

## Step 4 — Create OAuth Credentials

1. Go to **APIs & Services → Credentials**
2. Click **Create Credentials → OAuth client ID**
3. Application type: **Desktop app**
4. Name: `ApplicationTracker`
5. Click **Create**
6. Click **Download JSON** → rename the file to `client_secret.json`
7. Move `client_secret.json` into `agents/job_tracker/` in this repo

---

## Step 5 — Run the Auth Script Locally

```bash
python3 agents/job_tracker/gmail_auth.py
```

- A browser window will open — sign in with `sushantganji17@gmail.com`
- Click **Allow** when prompted for Gmail read access
- The script saves `token.json` to `agents/job_tracker/token.json`
- It prints the exact command to base64-encode the token

---

## Step 6 — Store Secrets in GitHub

Run the command the script printed:
```bash
base64 -i agents/job_tracker/token.json | tr -d '\n'
```
Copy the output.

In your GitHub repo:
1. Go to **Settings → Secrets and variables → Actions**
2. Click **New repository secret**:
   - Name: `GMAIL_TOKEN`
   - Value: the base64 string you copied
3. Add another secret:
   - Name: `GEMINI_API_KEY`
   - Value: your key from [aistudio.google.com/apikey](https://aistudio.google.com/apikey)

> ⚠️ **Do NOT commit `client_secret.json` or `token.json` to git.**
> Both are already in `.gitignore`.

---

## Step 7 — Create the Applications Gist

1. Go to [gist.github.com](https://gist.github.com)
2. Filename: `applications.json`
3. Content: `{}`
4. Click **Create public gist**
5. Copy the **Gist ID** from the URL:
   `https://gist.github.com/SushantGanji3/` **`← this hex string`**
6. Paste the Gist ID into `.github/workflows/applications-agent.yml` where it says `YOUR_GIST_ID`

---

## Done!

Trigger the first run manually:
- GitHub repo → **Actions → Job Application Tracker → Run workflow**

The first run will scan the past 3 months of Gmail and backfill all your applications.
