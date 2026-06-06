import streamlit as st

from auth.session import get_current_user, login_user
from auth.users import authenticate_user, create_user, ensure_users_table
from utils.state import init_session_state

st.set_page_config(page_title="Login", page_icon="🔐")

init_session_state()

try:
    ensure_users_table()
except Exception:
    st.warning("Could not connect to the database. Start PostgreSQL first.")

if get_current_user():
    st.switch_page("app.py")

st.title("Welcome")

tab_login, tab_register = st.tabs(["Login", "Register"])

with tab_login:
    st.subheader("Sign in")
    login_username = st.text_input("Username", key="login_username")
    login_password = st.text_input("Password", type="password", key="login_password")

    if st.button("Login", key="btn_login"):
        if not login_username or not login_password:
            st.error("Please enter both username and password.")
        else:
            user = authenticate_user(login_username, login_password)
            if user is None:
                st.error("Invalid username or password.")
            else:
                login_user(user["id"], user["username"])
                st.switch_page("app.py")

with tab_register:
    st.subheader("Create account")
    reg_username = st.text_input("Username", key="reg_username")
    reg_email = st.text_input("Email", key="reg_email")
    reg_password = st.text_input("Password", type="password", key="reg_password")
    reg_confirm = st.text_input("Confirm password", type="password", key="reg_confirm")

    if st.button("Register", key="btn_register"):
        if not reg_username or not reg_email or not reg_password:
            st.error("All fields are required.")
        elif len(reg_username) > 50:
            st.error("Username must be 50 characters or fewer.")
        elif "@" not in reg_email or "." not in reg_email.split("@")[-1]:
            st.error("Enter a valid email address.")
        elif reg_password != reg_confirm:
            st.error("Passwords do not match.")
        elif len(reg_password) < 8:
            st.error("Password must be at least 8 characters.")
        else:
            try:
                create_user(reg_username, reg_email, reg_password)
                st.success("Account created! Switch to the Login tab to sign in.")
            except ValueError as e:
                if str(e) == "username_taken":
                    st.error("That username is already taken.")
                elif str(e) == "email_taken":
                    st.error("That email is already registered.")
                else:
                    st.error("Registration failed. Please try again.")
            except Exception:
                st.error("Could not create account. Check that the database is running.")
