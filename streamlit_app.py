import streamlit as st
from datetime import datetime, timedelta
import pandas as pd
import json
import uuid
import tempfile
import os
import re

st.set_page_config(page_title="Life Dashboard", page_icon="🏠", layout="wide")

DB_SCHEMA = "POWDER_DB.LIFE_DASHBOARD"
CORTEX_MODEL = "mistral-large2"
USER_TZ = "America/Chicago"


def is_running_in_snowflake():
    try:
        from snowflake.snowpark.context import get_active_session
        get_active_session()
        return True
    except Exception:
        return False


IN_SNOWFLAKE = is_running_in_snowflake()

if not IN_SNOWFLAKE:
    if "authenticated" not in st.session_state:
        st.session_state.authenticated = False
    if not st.session_state.authenticated:
        st.title("🏠 Life Dashboard")
        pin = st.text_input("Enter PIN", type="password", max_chars=4)
        if pin == st.secrets["app"]["pin"]:
            st.session_state.authenticated = True
            st.rerun()
        elif pin:
            st.error("Incorrect PIN")
        st.stop()


def get_session():
    if "snowpark_session" not in st.session_state:
        if IN_SNOWFLAKE:
            from snowflake.snowpark.context import get_active_session
            st.session_state.snowpark_session = get_active_session()
        else:
            conn = st.connection("snowflake")
            st.session_state.snowpark_session = conn.session()
    return st.session_state.snowpark_session


def get_now():
    row = run_query(
        f"SELECT CONVERT_TIMEZONE('{USER_TZ}', CURRENT_TIMESTAMP())::TIMESTAMP_NTZ AS NOW_LOCAL"
    )
    if not row.empty:
        return pd.Timestamp(row.iloc[0]['NOW_LOCAL']).to_pydatetime()
    return datetime.now()


def sql_escape(value):
    if value is None:
        return ""
    return str(value).replace("\\", "\\\\").replace("'", "''")


def is_valid_date(date_str):
    if not date_str:
        return False
    return bool(re.match(r'^\d{4}-\d{2}-\d{2}$', str(date_str)))


def is_valid_time(time_str):
    if not time_str:
        return False
    return bool(re.match(r'^\d{2}:\d{2}$', str(time_str)))


def run_query(query):
    session = get_session()
    try:
        return session.sql(query).to_pandas()
    except Exception as e:
        st.error(f"Query error: {e}")
        return pd.DataFrame()


def run_command(query):
    session = get_session()
    try:
        session.sql(query).collect()
    except Exception as e:
        st.error(f"Command error: {e}")


def cortex_complete(prompt_text):
    session = get_session()
    escaped = sql_escape(prompt_text)
    try:
        result = session.sql(
            f"SELECT SNOWFLAKE.CORTEX.COMPLETE('{CORTEX_MODEL}', '{escaped}')"
        ).collect()
        return result[0][0] if result else ""
    except Exception as e:
        st.error(f"AI error: {e}")
        return ""


def ask_ai(question, context):
    now = get_now()
    prompt = f"""You are a helpful personal assistant for Hart Gellman. Answer the question based ONLY on the data provided below.

RULES:
- Be concise and direct (1-3 sentences max)
- If the answer is a time, include the day of the week
- If you cannot find the answer in the data, say "I don't see that in your upcoming schedule/emails/messages"
- Use relative time references when helpful (e.g., "this Saturday" instead of just the date)
- Today is {now.strftime('%A, %B %d, %Y')} and the current time is {now.strftime('%I:%M %p')} Central Time

DATA:
{context[:4000]}

QUESTION: {question}

ANSWER:"""
    return cortex_complete(prompt)


def process_uploaded_file(file_bytes, file_name):
    session = get_session()
    safe_name = re.sub(r'[^a-zA-Z0-9._-]', '_', file_name)
    tmp_dir = tempfile.mkdtemp()
    tmp_path = os.path.join(tmp_dir, safe_name)
    try:
        with open(tmp_path, 'wb') as f:
            f.write(file_bytes)

        session.sql(f"""
            PUT 'file://{tmp_path}' @{DB_SCHEMA}.UPLOADS_STAGE 
            AUTO_COMPRESS=FALSE OVERWRITE=TRUE
        """).collect()
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        if os.path.exists(tmp_dir):
            os.rmdir(tmp_dir)

    parse_result = session.sql(f"""
        SELECT SNOWFLAKE.CORTEX.PARSE_DOCUMENT(
            @{DB_SCHEMA}.UPLOADS_STAGE, 
            '{safe_name}', 
            {{'mode': 'OCR'}}
        ) AS PARSED
    """).collect()

    if not parse_result:
        return None, "Could not parse the document."

    parsed_text = parse_result[0][0]
    if isinstance(parsed_text, str):
        try:
            parsed_json = json.loads(parsed_text)
            extracted_text = parsed_json.get('content', parsed_text)
        except (json.JSONDecodeError, TypeError):
            extracted_text = parsed_text
    else:
        extracted_text = str(parsed_text)

    return extracted_text, None


def smart_route_document(extracted_text):
    now = get_now()
    prompt = f"""Analyze the following text extracted from an uploaded document. Determine what information it contains and return a JSON response.

Today's date is {now.strftime('%Y-%m-%d')} ({now.strftime('%A')}).

RULES:
- If it contains an event/appointment/meeting/party/game with a date/time, classify as "event"
- If it contains a to-do, reminder, or action needed (like RSVP, buy something, prepare something), classify as "action_item"  
- If it contains BOTH event details AND action items, return both
- For events: extract title, date (YYYY-MM-DD format), start_time (HH:MM 24hr), end_time (HH:MM 24hr or null), location (or null)
- For action items: extract title, description, priority (HIGH/MEDIUM/LOW), due_date (YYYY-MM-DD or null)
- If the year is not specified, assume {now.year}. If a month has already passed this year, assume next year.

Return ONLY valid JSON in this format (no other text):
{{
  "events": [
    {{"title": "...", "date": "YYYY-MM-DD", "start_time": "HH:MM", "end_time": "HH:MM", "location": "..."}}
  ],
  "action_items": [
    {{"title": "...", "description": "...", "priority": "MEDIUM", "due_date": "YYYY-MM-DD"}}
  ]
}}

DOCUMENT TEXT:
{extracted_text[:3000]}"""

    response = cortex_complete(prompt)
    try:
        start = response.find('{')
        end = response.rfind('}') + 1
        if start >= 0 and end > start:
            return json.loads(response[start:end])
    except (json.JSONDecodeError, ValueError):
        pass
    return None


def save_extracted_events(events):
    session = get_session()
    saved = 0
    for event in events:
        title = sql_escape(event.get('title', 'Untitled Event'))
        date_str = event.get('date', '')
        start_time_str = event.get('start_time', '')
        end_time_str = event.get('end_time')
        location = sql_escape(event.get('location') or '')

        if not is_valid_date(date_str):
            continue
        if not is_valid_time(start_time_str):
            start_time_str = '09:00'

        start_ts = f"{date_str} {start_time_str}:00"
        if end_time_str and is_valid_time(end_time_str):
            end_ts = f"{date_str} {end_time_str}:00"
        else:
            end_ts = start_ts

        item_id = str(uuid.uuid4())
        try:
            session.sql(f"""
                INSERT INTO {DB_SCHEMA}.EVENTS 
                (EVENT_ID, SUMMARY, START_TIME, END_TIME, LOCATION, CALENDAR_NAME, ALL_DAY, LOADED_AT)
                VALUES ('{item_id}', '{title}', '{start_ts}'::TIMESTAMP, '{end_ts}'::TIMESTAMP, 
                        '{location}', 'Uploaded', FALSE, CURRENT_TIMESTAMP())
            """).collect()
            saved += 1
        except Exception as e:
            st.warning(f"Could not save event '{event.get('title')}': {e}")
    return saved


def save_extracted_actions(action_items):
    session = get_session()
    saved = 0
    for item in action_items:
        title = sql_escape(item.get('title', 'Untitled'))
        description = sql_escape(item.get('description') or '')
        priority = item.get('priority', 'MEDIUM')
        if priority not in ('HIGH', 'MEDIUM', 'LOW'):
            priority = 'MEDIUM'
        due_date = item.get('due_date')
        due_clause = f"'{due_date}'" if due_date and is_valid_date(due_date) else "NULL"

        item_id = str(uuid.uuid4())
        try:
            session.sql(f"""
                INSERT INTO {DB_SCHEMA}.ACTION_ITEMS 
                (ITEM_ID, TITLE, DESCRIPTION, PRIORITY, DUE_DATE, SOURCE, STATUS, CREATED_AT)
                VALUES ('{item_id}', '{title}', '{description}', '{priority}', 
                        {due_clause}, 'UPLOAD', 'PENDING', CURRENT_TIMESTAMP())
            """).collect()
            saved += 1
        except Exception as e:
            st.warning(f"Could not save action item '{item.get('title')}': {e}")
    return saved


now = get_now()
hour = now.hour
is_morning = hour < 12
is_evening = hour >= 18

if is_morning:
    greeting = "Good morning, Hart"
    subtitle = "Here's your day at a glance"
elif is_evening:
    greeting = "Good evening, Hart"
    subtitle = "Tomorrow's preview & end-of-day wrap-up"
else:
    greeting = "Hey Hart"
    subtitle = "Here's what's happening"

st.title(greeting)
st.caption(subtitle)

tab_today, tab_week, tab_messages, tab_actions, tab_upload, tab_ask = st.tabs(
    ["Today", "This Week", "Messages", "Action Items", "Upload", "Ask"]
)

with tab_today:
    school_emails = run_query(f"""
        SELECT SENDER, SUBJECT, SNIPPET, RECEIVED_AT
        FROM {DB_SCHEMA}.SCHOOL_EMAILS
        WHERE RECEIVED_AT >= DATEADD('day', -7, CURRENT_TIMESTAMP())
        ORDER BY RECEIVED_AT DESC
    """)

    if not school_emails.empty:
        st.subheader("🏫 School Alerts")
        school_text = ""
        for _, se in school_emails.iterrows():
            sender = str(se['SENDER']).split('<')[0].strip()
            school_text += f"From: {sender} | Subject: {se['SUBJECT']} | {str(se['SNIPPET'])[:300]}\n\n"

        school_summary = cortex_complete(f"""You are Hart Gellman's assistant summarizing school emails from Monte Cassino (his kids Harrison and Hunter attend there).

Give Hart a useful summary of what's happening at school. Include:
- What the kids are learning or working on in class
- What activities or events happened this week
- Upcoming events, dates, or deadlines parents should know about
- Health/nurse visits (briefly note what happened and if follow-up is needed)
- Any fun highlights or achievements

If an email explicitly asks Hart to DO something (RSVP, bring supplies, fill out a form, volunteer), start that bullet with "ACTION:" and include the specific details/deadline.

Format: concise bullet points, grouped logically. Skip Facebook notifications and vague social media updates.

Today is {now.strftime('%A, %B %d, %Y')}.

EMAILS (most recent first):
{school_text[:3500]}

SUMMARY:""")

        if school_summary and school_summary.strip():
            st.markdown(school_summary)
        else:
            st.info("No notable school updates this week.")
        st.divider()

    if is_evening:
        st.subheader("Tomorrow")
        target_date = (now + timedelta(days=1)).strftime('%Y-%m-%d')
    else:
        st.subheader("Today's Schedule")
        target_date = now.strftime('%Y-%m-%d')

    events_today = run_query(f"""
        SELECT SUMMARY, 
               CONVERT_TIMEZONE('{USER_TZ}', START_TIME)::TIMESTAMP_NTZ AS START_TIME,
               CONVERT_TIMEZONE('{USER_TZ}', END_TIME)::TIMESTAMP_NTZ AS END_TIME,
               LOCATION, CALENDAR_NAME, ALL_DAY
        FROM {DB_SCHEMA}.EVENTS
        WHERE DATE(CONVERT_TIMEZONE('{USER_TZ}', START_TIME)) = '{target_date}'
        ORDER BY ALL_DAY DESC, START_TIME
    """)

    if events_today.empty:
        st.info("No events scheduled. Enjoy the free time!")
    else:
        for _, event in events_today.iterrows():
            col1, col2 = st.columns([3, 1])
            with col1:
                if event['ALL_DAY']:
                    time_str = "All day"
                else:
                    time_str = pd.Timestamp(event['START_TIME']).strftime('%I:%M %p')
                st.markdown(f"**{time_str}** — {event['SUMMARY']}")
                if event['LOCATION']:
                    st.caption(f"📍 {event['LOCATION']}")
            with col2:
                st.caption(event['CALENDAR_NAME'])

    st.divider()
    st.subheader("Email Highlights")
    emails = run_query(f"""
        SELECT SENDER, SUBJECT, SNIPPET
        FROM {DB_SCHEMA}.EMAILS
        ORDER BY LOADED_AT DESC
        LIMIT 20
    """)

    if emails.empty:
        st.info("No recent emails")
    else:
        email_text = ""
        for _, email in emails.iterrows():
            sender = str(email['SENDER']).split('<')[0].strip()
            email_text += f"From: {sender} | Subject: {email['SUBJECT']} | Preview: {str(email['SNIPPET'])[:200]}\n"

        email_summary = cortex_complete(f"""You are Hart Gellman's personal email assistant. Review these emails and extract ONLY what matters.

PRIORITY SENDERS (always surface these):
- Monte Cassino (school) - any email about events, schedules, deadlines, or announcements
- Schools, teachers, coaches
- Family members
- Medical/health appointments

IGNORE: Marketing, promotions, newsletters, spam, security alerts from apps

For each important email, write one bullet point summarizing the key info or action needed.
If no emails are important, say "Nothing requiring attention."

EMAILS:
{email_text[:3000]}

IMPORTANT HIGHLIGHTS (bullet points only):""")

        if email_summary and email_summary.strip():
            st.markdown(email_summary)
        else:
            st.info("Nothing requiring attention in recent emails.")

with tab_week:
    st.subheader("Next 7 Days")
    weekly_events = run_query(f"""
        SELECT SUMMARY, 
               CONVERT_TIMEZONE('{USER_TZ}', START_TIME)::TIMESTAMP_NTZ AS START_TIME,
               LOCATION, CALENDAR_NAME, ALL_DAY
        FROM {DB_SCHEMA}.EVENTS
        WHERE START_TIME BETWEEN CURRENT_TIMESTAMP() AND DATEADD('day', 7, CURRENT_TIMESTAMP())
        ORDER BY START_TIME
    """)

    if weekly_events.empty:
        st.info("No events this week")
    else:
        current_date = None
        for _, event in weekly_events.iterrows():
            event_date = pd.Timestamp(event['START_TIME']).strftime('%A, %B %d')
            if event_date != current_date:
                current_date = event_date
                st.markdown(f"### {event_date}")
            if event['ALL_DAY']:
                time_str = "All day"
            else:
                time_str = pd.Timestamp(event['START_TIME']).strftime('%I:%M %p')
            loc = f" · 📍 {event['LOCATION']}" if event['LOCATION'] else ""
            st.markdown(f"- **{time_str}** — {event['SUMMARY']}{loc}")

    st.divider()
    st.subheader("Coming Up (2-4 Weeks Out)")
    st.caption("Events that may need preparation")

    lookahead_events = run_query(f"""
        SELECT SUMMARY, 
               CONVERT_TIMEZONE('{USER_TZ}', START_TIME)::TIMESTAMP_NTZ AS START_TIME,
               LOCATION, CALENDAR_NAME
        FROM {DB_SCHEMA}.EVENTS
        WHERE START_TIME BETWEEN DATEADD('day', 7, CURRENT_TIMESTAMP()) 
              AND DATEADD('day', 28, CURRENT_TIMESTAMP())
        ORDER BY START_TIME
    """)

    if lookahead_events.empty:
        st.info("Nothing on the horizon")
    else:
        lookahead_text = ""
        for _, e in lookahead_events.iterrows():
            event_day = pd.Timestamp(e['START_TIME']).strftime('%A %b %d')
            loc = f" at {e['LOCATION']}" if e['LOCATION'] else ""
            lookahead_text += f"- {e['SUMMARY']} | {event_day}{loc}\n"

        lookahead_summary = cortex_complete(f"""Review these upcoming events (2-4 weeks from now) and identify ONLY those that require Hart to prepare or take action beforehand.

EVENTS THAT NEED ACTION (include these):
- Kids' birthday parties (need to buy a gift, RSVP)
- Travel/flights (need to pack, arrange pet/house care, confirm reservations)
- Hosting at our home (need supplies, food, cleaning)
- School events: field days, performances, publishing parties (may need supplies, costumes, volunteer sign-up)
- Holidays like Mother's Day, Father's Day (need gift/plans)

EVENTS THAT DON'T NEED ACTION (exclude these):
- Lauren's tennis matches or sports she plays
- Regular recurring family syncs
- Watching sports (Kentucky Derby, etc.)
- Regular kids' soccer practice
- Someone else's dinner/plans Hart isn't organizing

For each event needing action, write ONE bullet: the event name, date, and what prep is likely needed.
If nothing needs prep, say "Nothing requiring preparation in the next 2-4 weeks."

Today is {now.strftime('%A, %B %d, %Y')}.

EVENTS:
{lookahead_text[:3000]}

HEADS UP:""")

        if lookahead_summary and lookahead_summary.strip():
            st.markdown(lookahead_summary)
        else:
            st.info("Nothing requiring preparation in the next 2-4 weeks.")

with tab_messages:
    st.subheader("GroupMe Highlights")
    st.caption("AI-extracted updates from your group chats")
    messages = run_query(f"""
        SELECT GROUP_NAME, SENDER_NAME, MESSAGE_TEXT, LIKES_COUNT, SENT_AT
        FROM {DB_SCHEMA}.GROUPME_MESSAGES
        ORDER BY SENT_AT DESC
        LIMIT 60
    """)

    if messages.empty:
        st.info("No recent messages")
    else:
        msg_text = ""
        for _, msg in messages.iterrows():
            time_str = pd.Timestamp(msg['SENT_AT']).strftime('%b %d %I:%M %p') if msg['SENT_AT'] else ""
            msg_text += f"[{msg['GROUP_NAME']}] {msg['SENDER_NAME']} ({time_str}): {str(msg['MESSAGE_TEXT'])[:150]}\n"

        groupme_summary = cortex_complete(f"""You are Hart Gellman's personal assistant reviewing GroupMe messages from parent groups, family chats, and friend groups.

EXTRACT ONLY messages about:
- Schedule changes (practice cancelled, game moved, time change)
- New events or activities announced
- Teacher/staff birthdays or appreciation days
- RSVPs needed or deadlines
- Important logistics (carpool changes, aftercare updates, field assignments)
- Party/event planning that requires action

IGNORE: Casual chat, "thank you" messages, reactions, expired polls, general banter

Format as bullet points grouped by topic. Include the group name and key details.
If nothing noteworthy, say "No important updates from group chats."

MESSAGES:
{msg_text[:3500]}

KEY UPDATES:""")

        if groupme_summary and groupme_summary.strip():
            st.markdown(groupme_summary)
        else:
            st.info("No important updates from group chats.")

        with st.expander("View all recent messages"):
            groups = messages['GROUP_NAME'].unique()
            for group in groups:
                group_msgs = messages[messages['GROUP_NAME'] == group]
                st.markdown(f"**{group}** ({len(group_msgs)} messages)")
                for _, msg in group_msgs.iterrows():
                    likes = f" {msg['LIKES_COUNT']} likes" if msg['LIKES_COUNT'] > 0 else ""
                    time_str = pd.Timestamp(msg['SENT_AT']).strftime('%I:%M %p') if msg['SENT_AT'] else ""
                    st.caption(f"{msg['SENDER_NAME']} [{time_str}]: {msg['MESSAGE_TEXT']}{likes}")
                st.markdown("---")

with tab_actions:
    st.subheader("Action Items")

    pending = run_query(f"""
        SELECT ITEM_ID, TITLE, DESCRIPTION, PRIORITY, DUE_DATE, SOURCE, CREATED_AT
        FROM {DB_SCHEMA}.ACTION_ITEMS
        WHERE STATUS = 'PENDING'
        ORDER BY 
            CASE PRIORITY WHEN 'HIGH' THEN 1 WHEN 'MEDIUM' THEN 2 ELSE 3 END,
            DUE_DATE NULLS LAST
    """)

    if pending.empty:
        st.success("All caught up! No pending action items.")
    else:
        for _, item in pending.iterrows():
            col1, col2 = st.columns([4, 1])
            with col1:
                priority_icon = {"HIGH": "🔴", "MEDIUM": "🟡", "LOW": "🟢"}.get(item['PRIORITY'], "⚪")
                st.markdown(f"{priority_icon} **{item['TITLE']}**")
                if item['DESCRIPTION']:
                    st.caption(item['DESCRIPTION'])
                if item['DUE_DATE']:
                    st.caption(f"Due: {item['DUE_DATE']}")
            with col2:
                item_id_escaped = sql_escape(item['ITEM_ID'])
                if st.button("Done", key=f"done_{item['ITEM_ID']}"):
                    run_command(f"""
                        UPDATE {DB_SCHEMA}.ACTION_ITEMS 
                        SET STATUS = 'COMPLETED', 
                            COMPLETED_AT = CURRENT_TIMESTAMP(),
                            EXPIRES_AT = DATEADD('day', 7, CURRENT_TIMESTAMP())
                        WHERE ITEM_ID = '{item_id_escaped}'
                    """)
                    st.rerun()

    st.divider()
    with st.expander("Recently Completed"):
        completed = run_query(f"""
            SELECT TITLE, COMPLETED_AT
            FROM {DB_SCHEMA}.ACTION_ITEMS
            WHERE STATUS = 'COMPLETED' AND EXPIRES_AT > CURRENT_TIMESTAMP()
            ORDER BY COMPLETED_AT DESC
        """)
        if completed.empty:
            st.caption("No recently completed items")
        else:
            for _, item in completed.iterrows():
                st.markdown(f"~~{item['TITLE']}~~ Done")

with tab_upload:
    st.subheader("Upload a Document")
    st.caption("Upload a photo, PDF, or screenshot of an invitation, calendar, flyer, etc. AI will extract events and action items.")

    uploaded_file = st.file_uploader(
        "Choose a file",
        type=["pdf", "png", "jpg", "jpeg", "gif", "bmp", "tiff"],
        help="Supported: PDF, PNG, JPG, GIF, BMP, TIFF"
    )

    if uploaded_file is not None:
        if uploaded_file.type and uploaded_file.type.startswith("image"):
            st.image(uploaded_file, caption=uploaded_file.name, use_container_width=True)
        else:
            st.info(f"Uploaded: {uploaded_file.name} ({uploaded_file.size / 1024:.1f} KB)")

        if st.button("Extract & Add to Dashboard"):
            with st.spinner("Parsing document..."):
                try:
                    file_bytes = uploaded_file.getvalue()
                    extracted_text, error = process_uploaded_file(file_bytes, uploaded_file.name)
                except Exception as e:
                    extracted_text, error = None, f"Upload failed: {e}"

            if error:
                st.error(error)
            elif not extracted_text or not extracted_text.strip():
                st.warning("The document appears to be blank or could not be read.")
            else:
                with st.spinner("AI is analyzing the content..."):
                    result = smart_route_document(extracted_text)

                if result is None:
                    st.warning("Could not extract structured information. Here's what was found:")
                    st.text(extracted_text[:1000])
                else:
                    events = result.get('events', [])
                    actions = result.get('action_items', [])

                    if events:
                        count = save_extracted_events(events)
                        if count > 0:
                            st.success(f"Added {count} event(s) to your calendar!")
                            for ev in events:
                                st.markdown(f"- **{ev.get('title')}** on {ev.get('date')} at {ev.get('start_time')}")

                    if actions:
                        count = save_extracted_actions(actions)
                        if count > 0:
                            st.success(f"Added {count} action item(s)!")
                            for ai_item in actions:
                                st.markdown(f"- **{ai_item.get('title')}** (Priority: {ai_item.get('priority')})")

                    if not events and not actions:
                        st.info("No events or action items found in this document.")
                        st.text(extracted_text[:500])

with tab_ask:
    st.subheader("Ask About Your Life")
    st.caption("Ask any question about your calendar, emails, or messages")

    if "chat_messages" not in st.session_state:
        st.session_state.chat_messages = []

    for msg in st.session_state.chat_messages:
        with st.chat_message(msg["role"]):
            st.write(msg["content"])

    if prompt := st.chat_input("e.g., What time is soccer on Saturday?"):
        st.session_state.chat_messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.write(prompt)

        context_events = run_query(f"""
            SELECT SUMMARY, 
                   CONVERT_TIMEZONE('{USER_TZ}', START_TIME)::TIMESTAMP_NTZ AS START_TIME,
                   CONVERT_TIMEZONE('{USER_TZ}', END_TIME)::TIMESTAMP_NTZ AS END_TIME,
                   LOCATION, CALENDAR_NAME
            FROM {DB_SCHEMA}.EVENTS
            WHERE START_TIME BETWEEN CURRENT_TIMESTAMP() AND DATEADD('day', 14, CURRENT_TIMESTAMP())
            ORDER BY START_TIME
        """)

        context_str = f"TODAY: {now.strftime('%A, %B %d, %Y')} (Central Time)\n\n"
        context_str += "CALENDAR EVENTS (next 2 weeks):\n"
        if not context_events.empty:
            for _, e in context_events.iterrows():
                event_day = pd.Timestamp(e['START_TIME']).strftime('%A %b %d')
                event_time = pd.Timestamp(e['START_TIME']).strftime('%I:%M %p')
                loc = f" at {e['LOCATION']}" if e['LOCATION'] else ""
                context_str += f"- {e['SUMMARY']} | {event_day} {event_time}{loc} | Calendar: {e['CALENDAR_NAME']}\n"
        else:
            context_str += "- No upcoming events\n"

        context_emails = run_query(f"""
            SELECT SENDER, SUBJECT, SNIPPET FROM {DB_SCHEMA}.EMAILS 
            ORDER BY LOADED_AT DESC LIMIT 15
        """)
        context_str += "\nRECENT EMAILS:\n"
        if not context_emails.empty:
            for _, e in context_emails.iterrows():
                sender = str(e['SENDER']).split('<')[0].strip()
                context_str += f"- From {sender}: {e['SUBJECT']} — {str(e['SNIPPET'])[:150]}\n"

        context_messages = run_query(f"""
            SELECT GROUP_NAME, SENDER_NAME, MESSAGE_TEXT, SENT_AT
            FROM {DB_SCHEMA}.GROUPME_MESSAGES
            ORDER BY SENT_AT DESC LIMIT 20
        """)
        context_str += "\nGROUPME MESSAGES (recent):\n"
        if not context_messages.empty:
            for _, m in context_messages.iterrows():
                msg_time = pd.Timestamp(m['SENT_AT']).strftime('%b %d %I:%M %p') if m['SENT_AT'] else ""
                context_str += f"- [{m['GROUP_NAME']}] {m['SENDER_NAME']} ({msg_time}): {str(m['MESSAGE_TEXT'])[:100]}\n"

        context_actions = run_query(f"""
            SELECT TITLE, PRIORITY, DUE_DATE FROM {DB_SCHEMA}.ACTION_ITEMS 
            WHERE STATUS = 'PENDING' ORDER BY CREATED_AT DESC LIMIT 10
        """)
        context_str += "\nPENDING ACTION ITEMS:\n"
        if not context_actions.empty:
            for _, a in context_actions.iterrows():
                due = f" (due {a['DUE_DATE']})" if a['DUE_DATE'] else ""
                context_str += f"- [{a['PRIORITY']}] {a['TITLE']}{due}\n"

        context_school = run_query(f"""
            SELECT SENDER, SUBJECT, SNIPPET FROM {DB_SCHEMA}.SCHOOL_EMAILS 
            ORDER BY RECEIVED_AT DESC LIMIT 10
        """)
        context_str += "\nSCHOOL EMAILS (Monte Cassino - recent):\n"
        if not context_school.empty:
            for _, s in context_school.iterrows():
                sender = str(s['SENDER']).split('<')[0].strip()
                context_str += f"- From {sender}: {s['SUBJECT']} — {str(s['SNIPPET'])[:150]}\n"

        with st.chat_message("assistant"):
            with st.spinner("Thinking..."):
                response = ask_ai(prompt, context_str)
            st.write(response)

        st.session_state.chat_messages.append({"role": "assistant", "content": response})

st.sidebar.markdown("---")
st.sidebar.caption(f"Last refreshed: {now.strftime('%b %d, %I:%M %p')} CT")
if st.sidebar.button("Refresh Data"):
    with st.spinner("Refreshing all data sources..."):
        run_command(f"CALL {DB_SCHEMA}.REFRESH_CALENDAR()")
        run_command(f"CALL {DB_SCHEMA}.REFRESH_EMAILS()")
        run_command(f"CALL {DB_SCHEMA}.REFRESH_GROUPME()")
    st.rerun()
