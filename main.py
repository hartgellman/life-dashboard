import os
from datetime import datetime

from calendar_source import get_upcoming_events, format_events
from gmail_source import get_recent_emails, format_emails
from groupme_source import get_all_group_messages, format_groupme
from email_sender import send_digest_email
from doc_updater import update_google_doc


def build_dashboard_content():
    sections = []

    sections.append("📅 UPCOMING EVENTS (Next 7 Days)")
    sections.append("=" * 40)
    events = get_upcoming_events(days=7)
    sections.append(format_events(events))

    sections.append("\n📧 RECENT EMAILS (Last 24 Hours)")
    sections.append("=" * 40)
    emails = get_recent_emails(max_results=15, hours=24)
    sections.append(format_emails(emails))

    sections.append("\n💬 GROUPME UPDATES (Last 24 Hours)")
    sections.append("=" * 40)
    group_messages = get_all_group_messages(hours=24)
    sections.append(format_groupme(group_messages))

    return "\n".join(sections)


def main():
    print(f"🚀 Building daily dashboard - {datetime.now().strftime('%Y-%m-%d %H:%M')}")

    content = build_dashboard_content()

    update_google_doc(content)

    send_digest_email(content)

    print("✅ Dashboard complete!")


if __name__ == "__main__":
    main()
