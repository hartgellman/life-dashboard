import os
from datetime import datetime, timedelta
from dateutil import parser as dateparser
from googleapiclient.discovery import build
from auth import get_google_creds


def get_upcoming_events(days=7):
    creds = get_google_creds()
    service = build("calendar", "v3", credentials=creds)

    now = datetime.utcnow()
    time_min = now.isoformat() + "Z"
    time_max = (now + timedelta(days=days)).isoformat() + "Z"

    calendars_result = service.calendarList().list().execute()
    calendars = calendars_result.get("items", [])

    all_events = []
    for cal in calendars:
        cal_id = cal["id"]
        cal_name = cal.get("summary", "Unknown Calendar")
        try:
            events_result = (
                service.events()
                .list(
                    calendarId=cal_id,
                    timeMin=time_min,
                    timeMax=time_max,
                    singleEvents=True,
                    orderBy="startTime",
                )
                .execute()
            )
            events = events_result.get("items", [])
            for event in events:
                start = event["start"].get("dateTime", event["start"].get("date"))
                all_events.append(
                    {
                        "calendar": cal_name,
                        "summary": event.get("summary", "No title"),
                        "start": start,
                        "location": event.get("location", ""),
                        "description": event.get("description", ""),
                    }
                )
        except Exception:
            continue

    all_events.sort(key=lambda e: e["start"])
    return all_events


def format_events(events):
    if not events:
        return "No upcoming events this week.\n"

    lines = []
    current_date = None
    for event in events:
        try:
            dt = dateparser.parse(event["start"])
            date_str = dt.strftime("%A, %B %d")
            time_str = dt.strftime("%I:%M %p")
        except (ValueError, TypeError):
            date_str = event["start"]
            time_str = "All day"

        if date_str != current_date:
            current_date = date_str
            lines.append(f"\n📅 {date_str}")
            lines.append("-" * 30)

        location = f" | 📍 {event['location']}" if event["location"] else ""
        calendar_tag = f"[{event['calendar']}]"
        lines.append(f"  {time_str} - {event['summary']} {calendar_tag}{location}")

    return "\n".join(lines) + "\n"
