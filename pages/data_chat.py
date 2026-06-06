import os
import time

import httpx
import pandas as pd
import streamlit as st

from auth.session import require_auth
from ingestion.db import get_connection, get_row_counts, list_tables
from utils.state import init_session_state

API_BASE_URL = os.environ.get("API_BASE_URL", "http://localhost:8000")

st.set_page_config(page_title="Data Chat", page_icon="🗄️")
st.title("🗄️ Data Chat")
st.caption("Ask questions about your database in plain English.")

init_session_state()
require_auth()


# ── DB readiness check ────────────────────────────────────────────────────────


@st.cache_data(ttl=30)
def _fetch_tables() -> tuple[list[str], str | None]:
    try:
        conn = get_connection()
        try:
            return list_tables(conn), None
        finally:
            conn.close()
    except Exception as e:
        return [], str(e)


@st.cache_data(ttl=30)
def _fetch_row_counts() -> dict[str, int | None]:
    try:
        conn = get_connection()
        try:
            return get_row_counts(conn)
        finally:
            conn.close()
    except Exception:
        return {}


def _word_stream(text: str):
    for word in text.split():
        yield word + " "
        time.sleep(0.020)


tables, db_error = _fetch_tables()
row_counts = _fetch_row_counts()

# ── Sidebar ───────────────────────────────────────────────────────────────────

with st.sidebar:
    if st.button("Clear conversation"):
        st.session_state.data_chat_history = []
        st.rerun()

    if st.button("Refresh schema"):
        _fetch_tables.clear()
        _fetch_row_counts.clear()
        st.rerun()

    st.divider()
    st.markdown("**Available Tables**")
    if tables:
        for t in tables:
            count = row_counts.get(t)
            count_str = f" — {count:,} rows" if count else ""
            st.markdown(f"- `{t}`{count_str}")
    else:
        st.markdown("_No tables found_")

    st.divider()
    st.markdown(
        "**Example questions**\n"
        "- Show me the top 5 symbols by realized PnL\n"
        "- What was the total brokerage paid last month?\n"
        "- Which trade had the highest quantity?\n"
        "- Compare unrealized PnL across symbols"
    )

# ── Readiness gates ───────────────────────────────────────────────────────────

if db_error:
    st.error(
        f"Cannot connect to the database: {db_error}\n\n"
        "Check that PostgreSQL is running and the `PG_*` environment variables are set."
    )
    st.stop()

if not tables:
    st.warning(
        "No tables found in the database.  \n"
        "Go to the **Upload** page, click **Create Tables in DB**, then upload your data files."
    )
    st.stop()

# ── Chat history ──────────────────────────────────────────────────────────────

for entry in st.session_state.data_chat_history:
    with st.chat_message("user"):
        st.markdown(entry["question"])
    with st.chat_message("assistant"):
        st.markdown(entry["answer"])
        if entry.get("sql_query"):
            with st.expander("SQL Query"):
                st.code(entry["sql_query"], language="sql")
        if entry.get("query_results"):
            with st.expander(f"Raw data ({len(entry['query_results'])} rows)"):
                st.dataframe(pd.DataFrame(entry["query_results"]).head(200))

# ── Chat input ────────────────────────────────────────────────────────────────

if question := st.chat_input("Ask anything about your data…"):
    with st.chat_message("user"):
        st.markdown(question)

    token = st.session_state.get("auth_access_token", "")

    # Build history payload for the API (last 6 messages = 3 turns)
    history_payload = []
    for entry in st.session_state.data_chat_history[-3:]:
        history_payload.append({"role": "user", "content": entry["question"]})
        history_payload.append({"role": "assistant", "content": entry["answer"]})

    with st.chat_message("assistant"):
        result = {"answer": "", "sql_query": "", "query_results": [], "data_found": False}
        try:
            with st.spinner("Querying database…"):
                resp = httpx.post(
                    f"{API_BASE_URL}/data-chat/",
                    json={"question": question, "history": history_payload},
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=180,
                )
            resp.raise_for_status()
            result = resp.json()
        except httpx.HTTPStatusError as e:
            st.error(f"API error {e.response.status_code}: {e.response.text}")
            st.stop()
        except Exception as e:
            st.error(
                f"Cannot reach API at {API_BASE_URL}. "
                f"Start it with: uvicorn api.main:app --reload\n\n{e}"
            )
            st.stop()

        if result.get("data_found"):
            st.write_stream(_word_stream(result["answer"]))
        else:
            st.markdown(result["answer"])

        if result.get("sql_query"):
            with st.expander("SQL Query"):
                st.code(result["sql_query"], language="sql")

        if result.get("query_results"):
            with st.expander(f"Raw data ({len(result['query_results'])} rows)"):
                st.dataframe(pd.DataFrame(result["query_results"]).head(200))

    st.session_state.data_chat_history.append({
        "question": question,
        "answer": result["answer"],
        "sql_query": result.get("sql_query", ""),
        "query_results": result.get("query_results", [])[:200],
    })
