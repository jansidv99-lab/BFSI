import jwt
import streamlit as st

from auth.tokens import (
    create_access_token,
    create_refresh_token,
    decode_token,
)
from auth.users import get_user_by_id


def login_user(user_id: int, username: str) -> None:
    st.session_state["auth_access_token"] = create_access_token(user_id, username)
    st.session_state["auth_refresh_token"] = create_refresh_token(user_id)
    st.session_state["auth_user"] = {"id": user_id, "username": username}


def logout_user() -> None:
    st.session_state["auth_access_token"] = None
    st.session_state["auth_refresh_token"] = None
    st.session_state["auth_user"] = None
    st.rerun()


def get_current_user() -> dict | None:
    access_token = st.session_state.get("auth_access_token")
    if not access_token:
        return None
    try:
        decode_token(access_token)
        return st.session_state.get("auth_user")
    except jwt.ExpiredSignatureError:
        if _try_refresh():
            return st.session_state.get("auth_user")
        return None
    except Exception:
        logout_user()
        return None


def require_auth() -> None:
    user = get_current_user()
    if user is None:
        st.error("You must be logged in to view this page.")
        st.page_link("pages/login.py", label="Go to Login")
        st.stop()


def _try_refresh() -> bool:
    refresh_token = st.session_state.get("auth_refresh_token")
    if not refresh_token:
        return False
    try:
        payload = decode_token(refresh_token)
        if payload.get("type") != "refresh":
            return False
        user_id = int(payload["sub"])
        user = get_user_by_id(user_id)
        if not user or not user.get("is_active"):
            return False
        st.session_state["auth_access_token"] = create_access_token(user_id, user["username"])
        return True
    except Exception:
        return False
