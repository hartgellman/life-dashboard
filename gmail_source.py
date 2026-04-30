import os
import base64
from datetime import datetime, timedelta
from googleapiclient.discovery import build
from auth import get_google_creds


def get_recent_emails(max_results=20, hours=24):
    creds = get_google_creds()
    service = build("gmail", "v1", credentials=creds)

    after_date = (datetime.utcnow() - timedelta(hours=hours)).strftime("%Y/%m/%d")
    query = f"after:{after_date} -category:promotions -category:social"

    results = service.users().messages().list(
        userId="me", q=query, maxResults=max_results
    ).execute()

    messages = results.get("messages", [])
    email_summaries = []

    for msg in messages:
        msg_data = service.users().messages().get(
            userId="me", id=msg["id"], format="metadata",
            metadataHeaders=["From", "Subject", "Date"]
        ).execute()

        headers = {h["name"]: h["value"] for h in msg_data["payload"]["headers"]}
        email_summaries.append(
            {
                "from": headers.get("From", "Unknown"),
                "subject": headers.get("Subject", "No subject"),
                "date": headers.get("Date", ""),
                "snippet": msg_data.get("snippet", ""),
            }
        )

    return email_summaries


def format_emails(emails):
    if not emails:
        return "No new emails in the last 24 hours.\n"

    lines = [""]
    for email in emails:
        sender = email["from"].split("<")[0].strip()
        lines.append(f"  📧 From: {sender}")
        lines.append(f"     Subject: {email['subject']}")
        lines.append(f"     Preview: {email['snippet'][:100]}...")
        lines.append("")

    return "\n".join(lines)
