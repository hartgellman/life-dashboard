import os
from datetime import datetime
from googleapiclient.discovery import build
from auth import get_google_creds


DOC_ID = os.getenv("GOOGLE_DOC_ID", "")


def update_google_doc(content):
    if not DOC_ID:
        print("⚠️  No GOOGLE_DOC_ID set. Skipping Google Doc update.")
        return

    creds = get_google_creds()
    service = build("docs", "v1", credentials=creds)

    doc = service.documents().get(documentId=DOC_ID).execute()
    body = doc.get("body", {})
    content_elements = body.get("content", [])

    end_index = content_elements[-1]["endIndex"] if content_elements else 1

    requests = []
    if end_index > 2:
        requests.append(
            {"deleteContentRange": {"range": {"startIndex": 1, "endIndex": end_index - 1}}}
        )

    today = datetime.now().strftime("%A, %B %d %Y at %I:%M %p")
    header = f"🏠 Life Dashboard\nLast updated: {today}\n{'=' * 50}\n\n"
    full_content = header + content

    requests.append({"insertText": {"location": {"index": 1}, "text": full_content}})

    service.documents().batchUpdate(documentId=DOC_ID, body={"requests": requests}).execute()
    print(f"✅ Google Doc updated: https://docs.google.com/document/d/{DOC_ID}/edit")
