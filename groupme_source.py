import os
import requests
from datetime import datetime, timedelta


GROUPME_TOKEN = os.getenv("GROUPME_TOKEN", "")
BASE_URL = "https://api.groupme.com/v3"


def get_groups():
    url = f"{BASE_URL}/groups"
    params = {"token": GROUPME_TOKEN, "per_page": 50}
    resp = requests.get(url, params=params)
    if resp.status_code != 200:
        return []
    return resp.json().get("response", [])


def get_recent_messages(group_id, hours=24, limit=50):
    url = f"{BASE_URL}/groups/{group_id}/messages"
    params = {"token": GROUPME_TOKEN, "limit": limit}
    resp = requests.get(url, params=params)
    if resp.status_code != 200:
        return []

    messages = resp.json().get("response", {}).get("messages", [])
    cutoff = datetime.utcnow() - timedelta(hours=hours)

    recent = []
    for msg in messages:
        msg_time = datetime.utcfromtimestamp(msg["created_at"])
        if msg_time < cutoff:
            break
        if msg.get("text"):
            recent.append(
                {
                    "name": msg.get("name", "Unknown"),
                    "text": msg["text"],
                    "time": msg_time.strftime("%I:%M %p"),
                    "likes": len(msg.get("favorited_by", [])),
                }
            )

    return recent


def get_all_group_messages(hours=24):
    groups = get_groups()
    group_messages = {}

    for group in groups:
        group_name = group.get("name", "Unknown Group")
        messages = get_recent_messages(group["id"], hours=hours)
        if messages:
            group_messages[group_name] = messages

    return group_messages


def format_groupme(group_messages):
    if not group_messages:
        return "No new GroupMe messages in the last 24 hours.\n"

    lines = []
    for group_name, messages in group_messages.items():
        lines.append(f"\n💬 {group_name} ({len(messages)} messages)")
        lines.append("-" * 30)
        for msg in messages[:10]:
            like_str = f" ❤️{msg['likes']}" if msg["likes"] > 0 else ""
            lines.append(f"  [{msg['time']}] {msg['name']}: {msg['text'][:120]}{like_str}")
        if len(messages) > 10:
            lines.append(f"  ... and {len(messages) - 10} more messages")
        lines.append("")

    return "\n".join(lines)
