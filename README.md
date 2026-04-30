# 🏠 Life Dashboard

A personal dashboard that aggregates your Google Calendar, Gmail, and GroupMe messages into a daily Google Doc and email digest.

## What It Does
- **Every morning at 7:00 AM**, this runs automatically via GitHub Actions
- Pulls your **upcoming calendar events** (next 7 days) from all calendars
- Summarizes your **recent emails** (last 24 hours, excludes promos/social)
- Shows **GroupMe messages** from all your groups (last 24 hours)
- Updates a **Google Doc** with everything
- Sends you a **daily email digest**

## Setup Guide

### Step 1: Create a Google Cloud Project

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Click "New Project" → name it "Life Dashboard" → Create
3. In the left menu, go to **APIs & Services → Library**
4. Enable these APIs (search for each):
   - Google Calendar API
   - Gmail API
   - Google Docs API
5. Go to **APIs & Services → Credentials**
6. Click **+ Create Credentials → OAuth client ID**
7. If prompted, configure the OAuth consent screen:
   - Choose "External"
   - App name: "Life Dashboard"
   - Add your email as test user
8. Back in Credentials, create OAuth client:
   - Application type: **Desktop app**
   - Name: "Life Dashboard"
   - Download the JSON file → rename it to `credentials.json`
   - Place it in this project folder

### Step 2: Authorize Your Google Account (one-time)

Run this on your computer:
```bash
cd ~/Desktop/life-dashboard
pip install -r requirements.txt
python auth.py
```

A browser window will open — sign in with **hartgellman@gmail.com** and grant access.
This creates a `token.json` file with your auth credentials.

### Step 3: Create Your Google Doc

1. Go to [Google Docs](https://docs.google.com) and create a new blank document
2. Name it "Life Dashboard" (or whatever you want)
3. Copy the document ID from the URL: `https://docs.google.com/document/d/THIS_PART_IS_THE_ID/edit`

### Step 4: Push to GitHub

```bash
cd ~/Desktop/life-dashboard
git init
git add .
git commit -m "Initial commit"
git remote add origin https://github.com/hartgellman/life-dashboard.git
git push -u origin main
```

### Step 5: Add GitHub Secrets

Go to your repo → Settings → Secrets and variables → Actions → New repository secret

Add these secrets:
1. **GOOGLE_TOKEN_JSON** — paste the entire contents of `token.json`
2. **GROUPME_TOKEN** — your GroupMe access token
3. **GOOGLE_DOC_ID** — the document ID from Step 3
4. **RECIPIENT_EMAIL** — `hartgellman@gmail.com`

### Step 6: Test It

Go to your repo → Actions → "Daily Life Dashboard" → "Run workflow" → Run

Check your email and Google Doc!

## Running Locally

```bash
export GROUPME_TOKEN="your_token_here"
export GOOGLE_DOC_ID="your_doc_id_here"
export RECIPIENT_EMAIL="hartgellman@gmail.com"
python main.py
```

## Adjusting the Schedule

Edit `.github/workflows/daily.yml` and change the cron expression.
The current schedule is UTC 12:00 = 7:00 AM Eastern.

If you want a different time, use [crontab.guru](https://crontab.guru/) to calculate it.
