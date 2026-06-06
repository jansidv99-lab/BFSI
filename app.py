import os
import streamlit as st
from dotenv import load_dotenv
from opentelemetry import trace as otel_trace
import httpx

from auth.session import get_current_user, logout_user, require_auth
from auth.users import ensure_users_table
from utils.state import init_session_state

load_dotenv()

API_BASE_URL = os.environ.get("API_BASE_URL", "http://localhost:8000")
PHOENIX_ENDPOINT = os.environ.get("PHOENIX_ENDPOINT", "")
PHOENIX_PROJECT = os.environ.get("PHOENIX_PROJECT", "chatbot")

if PHOENIX_ENDPOINT:
    @st.cache_resource
    def _init_phoenix():
        from phoenix.otel import register  # noqa: PLC0415
        register(endpoint=PHOENIX_ENDPOINT, project_name=PHOENIX_PROJECT)

    _init_phoenix()

tracer = otel_trace.get_tracer(__name__)


def stream_response(messages: list[dict], token: str):
    history = messages[:-1]
    last = messages[-1]["content"]
    with httpx.Client(timeout=120) as client:
        with client.stream(
            "POST",
            f"{API_BASE_URL}/chat/",
            json={"message": last, "history": history},
            headers={"Authorization": f"Bearer {token}"},
        ) as resp:
            if resp.status_code != 200:
                raise ConnectionError(f"API error {resp.status_code}")
            for line in resp.iter_lines():
                if line.startswith("data: "):
                    yield line[6:]


def generate_suggestions(messages: list[dict], token: str) -> list[str]:
    prompt_msg = (
        "Give me 3 short follow-up questions I could ask about this topic. "
        "Reply with just the questions, one per line, no numbering or bullets."
    )
    all_messages = messages + [{"role": "user", "content": prompt_msg}]
    try:
        tokens = list(stream_response(all_messages, token))
        text = "".join(tokens).strip()
        lines = text.splitlines()
        return [line.strip() for line in lines if line.strip()][:3]
    except Exception:
        return []


init_session_state()
try:
    ensure_users_table()
except Exception:
    pass
require_auth()

st.markdown("""
<style>
/* ── Background ─────────────────────────────────────────── */
.stApp {
    background-color: #0e0e1a;
}

/* ── Sidebar ─────────────────────────────────────────────── */
[data-testid="stSidebar"] {
    background-color: #151528;
    border-right: 1px solid #2a2a4a;
}

/* ── Buttons ─────────────────────────────────────────────── */
.stButton > button {
    background-color: #6c63ff;
    color: #ffffff;
    border: none;
    border-radius: 8px;
    font-weight: 600;
    transition: background-color 0.2s ease;
}
.stButton > button:hover {
    background-color: #5a52d5;
    border: none;
    color: #ffffff;
}
.stButton > button:active {
    background-color: #4a43b5;
    border: none;
    color: #ffffff;
}

/* ── Chat input box ──────────────────────────────────────── */
[data-testid="stChatInput"] {
    background-color: #1a1a30 !important;
    border: 1px solid #3a3a60 !important;
    border-radius: 12px !important;
}

/* ── Title ───────────────────────────────────────────────── */
h1 {
    color: #7c73ff !important;
}
</style>
""", unsafe_allow_html=True)

st.title("Chatbot")

with st.sidebar:
    user = get_current_user()
    if user:
        st.markdown(f"Signed in as **{user['username']}**")
        if st.button("Logout"):
            logout_user()

    if st.button("Clear conversation"):
        st.session_state.messages = []
        st.session_state.suggestions = []
        st.rerun()

    if st.session_state.suggestions:
        st.divider()
        st.markdown("**You might want to ask:**")
        for s in st.session_state.suggestions:
            st.markdown(f"- *{s}*")

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

if prompt := st.chat_input("Message Model..."):
    st.session_state.suggestions = []
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    token = st.session_state.get("auth_access_token", "")

    with st.chat_message("assistant"):
        with tracer.start_as_current_span("chat_turn") as agent_span:
            agent_span.set_attribute("openinference.span.kind", "AGENT")
            agent_span.set_attribute("input.value", prompt)
            try:
                with tracer.start_as_current_span("stream_response") as chain_span:
                    chain_span.set_attribute("openinference.span.kind", "CHAIN")
                    response = st.write_stream(stream_response(st.session_state.messages, token))
                    chain_span.set_attribute("output.value", response)

                st.session_state.messages.append({"role": "assistant", "content": response})

                with tracer.start_as_current_span("generate_suggestions") as chain_span:
                    chain_span.set_attribute("openinference.span.kind", "CHAIN")
                    st.session_state.suggestions = generate_suggestions(st.session_state.messages, token)

                agent_span.set_attribute("output.value", response)

            except ConnectionError as e:
                st.error(f"Cannot reach API server at {API_BASE_URL}. Start it with: uvicorn api.main:app --reload\n\n{e}")
            except Exception as e:
                st.error(f"Unexpected error: {e}")
