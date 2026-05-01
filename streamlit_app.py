import streamlit as st
from datetime import datetime, timedelta
import snowflake.connector
import pandas as pd
import json
import requests

st.set_page_config(page_title="Life Dashboard", page_icon="🏠", layout="wide")

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


@st.cache_resource
def get_connection():
    return snowflake.connector.connect(
        account=st.secrets["snowflake"]["account"],
        user=st.secrets["snowflake"]["user"],
        authenticator="programmatic_access_token",
        token=st.secrets["snowflake"]["token"],
        warehouse=st.secrets["snowflake"]["warehouse"],
        database=st.secrets["snowflake"]["database"],
        schema=st.secrets["snowflake"]["schema"],
        role=st.secrets["snowflake"]["role"],
    )


@st.cache_data(ttl=300)
def run_query(query):
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(query)
        columns = [desc[0] for desc in cur.description]
        data = cur.fetchall()
        return pd.DataFrame(data, columns=columns)
    except Exception as e:
        st.error(f"Query error: {e}")
        return pd.DataFrame()


def run_command(query):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(query)
    return cur.fetchone()


def ask_ai(question, context):
    conn = get_connection()
    cur = conn.cursor()
    prompt = f"""You are a helpful family assistant for Hart. Answer the question based ONLY on the data below. Be concise and direct. Today is {datetime.now().strftime('%A, %B %d, %Y')}.

DATA:
{context}

QUESTION: {question}

ANSWER:"""
    prompt_escaped = prompt.replace("'", "''")
    cur.execute(f"SELECT SNOWFLAKE.CORTEX.COMPLETE('mistral-large2', '{prompt_escaped}')")
    result = cur.fetchone()
    return result[0] if result else "Sorry, I couldn't process that question."


now = datetime.now()
hour = now.hour
is_morning = hour < 12
is_evening = hour >= 18

if is_morning:
    greeting = "Good morning, Hart ☀️"
    subtitle = "Here's your day at a glance"
elif is_evening:
    greeting = "Good evening, Hart 🌙"
    subtitle = "Tomorrow's preview & end-of-day wrap-up"
else:
    greeting = "Hey Hart 👋"
    subtitle = "Here's what's happening"

st.title(greeting)
st.caption(subtitle)

tab_today, tab_week, tab_messages, tab_actions, tab_ask = st.tabs(
    ["📅 Today", "📆 This Week", "💬 Messages", "✅ Action Items", "🤖 Ask"]
)

with tab_today:
    if is_evening:
        st.subheader("Tomorrow")
        target_date = (now + timedelta(days=1)).strftime('%Y-%m-%d')
    else:
        st.subheader("Today's Schedule")
        target_date = now.strftime('%Y-%m-%d')

    events_today = run_query(f"""
        SELECT SUMMARY, START_TIME, END_TIME, LOCATION, CALENDAR_NAME, ALL_DAY
        FROM POWDER_DB.LIFE_DASHBOARD.EVENTS
        WHERE DATE(START_TIME) = '{target_date}'
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
    st.subheader("Recent Emails")
    emails = run_query("""
        SELECT SENDER, SUBJECT, SNIPPET
        FROM POWDER_DB.LIFE_DASHBOARD.EMAILS
        ORDER BY LOADED_AT DESC
        LIMIT 8
    """)

    if emails.empty:
        st.info("No recent emails")
    else:
        for _, email in emails.iterrows():
            sender = str(email['SENDER']).split('<')[0].strip()
            with st.expander(f"📧 **{sender}** — {email['SUBJECT']}"):
                st.write(email['SNIPPET'])

with tab_week:
    st.subheader("Next 7 Days")
    weekly_events = run_query("""
        SELECT SUMMARY, START_TIME, LOCATION, CALENDAR_NAME, ALL_DAY
        FROM POWDER_DB.LIFE_DASHBOARD.EVENTS
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

with tab_messages:
    st.subheader("GroupMe Updates (Last 48h)")
    messages = run_query("""
        SELECT GROUP_NAME, SENDER_NAME, MESSAGE_TEXT, LIKES_COUNT, SENT_AT
        FROM POWDER_DB.LIFE_DASHBOARD.GROUPME_MESSAGES
        ORDER BY SENT_AT DESC
        LIMIT 50
    """)

    if messages.empty:
        st.info("No recent messages")
    else:
        groups = messages['GROUP_NAME'].unique()
        for group in groups:
            group_msgs = messages[messages['GROUP_NAME'] == group]
            with st.expander(f"💬 **{group}** ({len(group_msgs)} messages)"):
                for _, msg in group_msgs.iterrows():
                    likes = f" ❤️{msg['LIKES_COUNT']}" if msg['LIKES_COUNT'] > 0 else ""
                    time_str = pd.Timestamp(msg['SENT_AT']).strftime('%I:%M %p') if msg['SENT_AT'] else ""
                    st.markdown(f"**{msg['SENDER_NAME']}** [{time_str}]: {msg['MESSAGE_TEXT']}{likes}")

with tab_actions:
    st.subheader("Action Items")

    pending = run_query("""
        SELECT ITEM_ID, TITLE, DESCRIPTION, PRIORITY, DUE_DATE, SOURCE, CREATED_AT
        FROM POWDER_DB.LIFE_DASHBOARD.ACTION_ITEMS
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
                if st.button("✓ Done", key=f"done_{item['ITEM_ID']}"):
                    run_command(f"""
                        UPDATE POWDER_DB.LIFE_DASHBOARD.ACTION_ITEMS 
                        SET STATUS = 'COMPLETED', 
                            COMPLETED_AT = CURRENT_TIMESTAMP(),
                            EXPIRES_AT = DATEADD('day', 7, CURRENT_TIMESTAMP())
                        WHERE ITEM_ID = '{item['ITEM_ID']}'
                    """)
                    st.cache_data.clear()
                    st.rerun()

    st.divider()
    with st.expander("Recently Completed"):
        completed = run_query("""
            SELECT TITLE, COMPLETED_AT
            FROM POWDER_DB.LIFE_DASHBOARD.ACTION_ITEMS
            WHERE STATUS = 'COMPLETED' AND EXPIRES_AT > CURRENT_TIMESTAMP()
            ORDER BY COMPLETED_AT DESC
        """)
        if completed.empty:
            st.caption("No recently completed items")
        else:
            for _, item in completed.iterrows():
                st.markdown(f"~~{item['TITLE']}~~ ✓")

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

        context_events = run_query("""
            SELECT SUMMARY, START_TIME, END_TIME, LOCATION, CALENDAR_NAME
            FROM POWDER_DB.LIFE_DASHBOARD.EVENTS
            WHERE START_TIME BETWEEN CURRENT_TIMESTAMP() AND DATEADD('day', 14, CURRENT_TIMESTAMP())
            ORDER BY START_TIME
        """)

        context_str = "CALENDAR EVENTS (next 2 weeks):\n"
        for _, e in context_events.iterrows():
            context_str += f"- {e['SUMMARY']} on {e['START_TIME']} at {e['LOCATION']}\n"

        context_emails = run_query("""
            SELECT SENDER, SUBJECT, SNIPPET FROM POWDER_DB.LIFE_DASHBOARD.EMAILS LIMIT 10
        """)
        context_str += "\nRECENT EMAILS:\n"
        for _, e in context_emails.iterrows():
            context_str += f"- From {e['SENDER']}: {e['SUBJECT']} - {str(e['SNIPPET'])[:100]}\n"

        with st.chat_message("assistant"):
            response = ask_ai(prompt, context_str)
            st.write(response)

        st.session_state.chat_messages.append({"role": "assistant", "content": response})

st.sidebar.markdown("---")
st.sidebar.caption(f"Last viewed: {now.strftime('%I:%M %p')}")
if st.sidebar.button("🔄 Refresh Data"):
    run_command("CALL POWDER_DB.LIFE_DASHBOARD.REFRESH_CALENDAR()")
    run_command("CALL POWDER_DB.LIFE_DASHBOARD.REFRESH_EMAILS()")
    run_command("CALL POWDER_DB.LIFE_DASHBOARD.REFRESH_GROUPME()")
    st.cache_data.clear()
    st.rerun()
