import os
import base64
from datetime import datetime
from email.mime.text import MIMEText
from googleapiclient.discovery import build
from auth import get_google_creds


RECIPIENT_EMAIL = os.getenv("RECIPIENT_EMAIL", "hartgellman@gmail.com")


def send_digest_email(content):
    creds = get_google_creds()
    service = build("gmail", "v1", credentials=creds)

    today = datetime.now().strftime("%A, %B %d %Y")
    subject = f"📋 Daily Life Dashboard - {today}"

    message = MIMEText(content, "plain")
    message["to"] = RECIPIENT_EMAIL
    message["subject"] = subject

    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
    body = {"raw": raw}

    service.users().messages().send(userId="me", body=body).execute()
    print(f"✅ Digest email sent to {RECIPIENT_EMAIL}")
