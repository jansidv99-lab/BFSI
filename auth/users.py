import psycopg2

from auth.passwords import hash_password, verify_password
from ingestion.db import get_connection

# Precomputed dummy hash used in authenticate_user to equalise timing when a
# username is not found — prevents an attacker from detecting valid usernames
# by measuring bcrypt response time.
_DUMMY_HASH = hash_password("__dummy_constant__")

_CREATE_USERS = """
CREATE TABLE IF NOT EXISTS users (
    id             SERIAL        PRIMARY KEY,
    username       VARCHAR(50)   NOT NULL UNIQUE,
    email          VARCHAR(255)  NOT NULL UNIQUE,
    password_hash  VARCHAR(72)   NOT NULL,
    created_at     TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    is_active      BOOLEAN       NOT NULL DEFAULT TRUE
);
"""


def ensure_users_table() -> None:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(_CREATE_USERS)
        conn.commit()
    finally:
        conn.close()


def create_user(username: str, email: str, plain_password: str) -> int:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            try:
                cur.execute(
                    "INSERT INTO users (username, email, password_hash) VALUES (%s, %s, %s) RETURNING id",
                    (username, email, hash_password(plain_password)),
                )
                user_id = cur.fetchone()[0]
                conn.commit()
                return user_id
            except psycopg2.errors.UniqueViolation as e:
                conn.rollback()
                constraint = e.diag.constraint_name or ""
                if "username" in constraint:
                    raise ValueError("username_taken")
                if "email" in constraint:
                    raise ValueError("email_taken")
                raise ValueError("duplicate_user")
    finally:
        conn.close()


def authenticate_user(username: str, plain_password: str) -> dict | None:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, username, email, password_hash, is_active FROM users WHERE username = %s",
                (username,),
            )
            row = cur.fetchone()
    finally:
        conn.close()

    if row is None:
        verify_password(plain_password, _DUMMY_HASH)  # constant-time path
        return None
    user_id, uname, email, pw_hash, is_active = row
    if not is_active:
        return None
    if not verify_password(plain_password, pw_hash):
        return None
    return {"id": user_id, "username": uname, "email": email}


def get_user_by_id(user_id: int) -> dict | None:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, username, email, is_active FROM users WHERE id = %s",
                (user_id,),
            )
            row = cur.fetchone()
    finally:
        conn.close()

    if row is None:
        return None
    uid, username, email, is_active = row
    return {"id": uid, "username": username, "email": email, "is_active": is_active}
