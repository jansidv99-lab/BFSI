import pandas as pd
import streamlit as st

from auth.session import require_auth
from utils.state import init_session_state
from ingestion.db import (
    ensure_schema,
    get_connection,
    insert_charges,
    insert_pl,
    insert_positions,
    insert_trades,
    list_tables,
)
from ingestion.parser import (
    parse_pnl_excel,
    parse_positions_excel,
    parse_tradebook_excel,
    validate_file,
    validate_pnl_file,
    validate_tradebook_file,
)

st.set_page_config(page_title="Upload Data", page_icon="📤")
st.title("Upload Data")

init_session_state()
require_auth()

# ── Database ─────────────────────────────────────────────────────────────────

st.subheader("Database")

if st.button("Create Tables in DB"):
    try:
        conn = get_connection()
        try:
            ensure_schema(conn)
        finally:
            conn.close()
        st.success("All tables created (or already exist).")
    except Exception as e:
        st.error(f"Failed to create tables: {e}")

try:
    conn = get_connection()
    try:
        tables = list_tables(conn)
    finally:
        conn.close()
    if tables:
        st.write("Tables in DB: " + ", ".join(tables))
    else:
        st.info("No tables found. Click 'Create Tables in DB' first.")
except Exception as e:
    st.warning(f"Cannot connect to DB: {e}")

st.divider()

# ── Bulk Upload from Folder ───────────────────────────────────────────────────

st.subheader("Bulk Upload from Folder")
st.write(
    "Select multiple Excel files at once (Ctrl+click or Shift+click in the file picker). "
    "Each file is identified by name: 'positions' → daily_positions, "
    "'pnl' → daily_pl + daily_charges, 'trade' → daily_trades."
)

bulk_files = st.file_uploader(
    "Choose files", type=["xlsx"], accept_multiple_files=True, key="bulk"
)

if bulk_files:
    try:
        conn = get_connection()
    except Exception as e:
        st.error(f"Database connection failed: {e}")
        conn = None

    if conn is not None:
        try:
            for uf in bulk_files:
                name = uf.name.lower()
                file_bytes = uf.getvalue()
                st.write(f"**{uf.name}**")

                if "pnl" in name:
                    try:
                        validate_pnl_file(uf.name, file_bytes)
                        pl_rows, charges = parse_pnl_excel(file_bytes)
                        pl_ins, pl_sk = insert_pl(conn, pl_rows)
                        ch_ins, ch_sk = insert_charges(conn, charges)
                        st.success(
                            f"daily_pl: {pl_ins} inserted, {pl_sk} skipped. "
                            f"daily_charges: {ch_ins} inserted, {ch_sk} skipped."
                        )
                    except Exception as e:
                        st.error(f"Failed: {e}")

                elif "position" in name:
                    try:
                        validate_file(uf.name, file_bytes)
                        rows = parse_positions_excel(file_bytes)
                        ins, sk = insert_positions(conn, rows)
                        st.success(f"daily_positions: {ins} inserted, {sk} skipped.")
                    except Exception as e:
                        st.error(f"Failed: {e}")

                elif "trade" in name:
                    try:
                        validate_tradebook_file(uf.name, file_bytes)
                        rows = parse_tradebook_excel(file_bytes)
                        ins, sk = insert_trades(conn, rows)
                        st.success(f"daily_trades: {ins} inserted, {sk} skipped.")
                    except Exception as e:
                        st.error(f"Failed: {e}")

                else:
                    st.warning(
                        "Unrecognized filename — skipped. "
                        "Expected 'positions', 'pnl', or 'trade' in the name."
                    )
        finally:
            conn.close()

st.divider()

# ── Upload Positions ──────────────────────────────────────────────────────────

st.subheader("Upload Positions")
st.write(
    "Upload a Zerodha F&O positions Excel file (.xlsx). "
    "Duplicate rows (same trade date + symbol) will be silently skipped."
)

uploaded_positions = st.file_uploader("Choose positions file", type=["xlsx"])

if uploaded_positions is not None:
    file_bytes = uploaded_positions.getvalue()
    try:
        validate_file(uploaded_positions.name, file_bytes)
    except ValueError as e:
        st.error(f"Validation failed: {e}")
        st.stop()
    try:
        rows = parse_positions_excel(file_bytes)
    except Exception as e:
        st.error(f"Failed to parse file: {e}")
        st.stop()
    dates = [r["trade_date"] for r in rows]
    st.info(f"**{len(rows)} rows parsed** — {min(dates)} → {max(dates)}")
    st.dataframe(
        pd.DataFrame(rows)[["symbol", "trade_date", "open_quantity", "unrealized_profit"]].head(5),
        use_container_width=True,
        hide_index=True,
    )
    if st.button("Insert into DB", key="btn_positions"):
        try:
            conn = get_connection()
            try:
                inserted, skipped = insert_positions(conn, rows)
            finally:
                conn.close()
        except Exception as e:
            st.error(f"Database error: {e}")
            st.stop()
        st.success(f"Done. {inserted} rows inserted, {skipped} duplicates skipped.")

st.divider()

# ── Upload P&L File ───────────────────────────────────────────────────────────

st.subheader("Upload P&L File")
st.write(
    "Upload a Zerodha F&O P&L Excel file (.xlsx). "
    "Inserts into daily_pl and daily_charges."
)

uploaded_pnl = st.file_uploader("Choose P&L file", type=["xlsx"], key="pnl")

if uploaded_pnl is not None:
    file_bytes = uploaded_pnl.getvalue()
    try:
        validate_pnl_file(uploaded_pnl.name, file_bytes)
    except ValueError as e:
        st.error(f"Validation failed: {e}")
        st.stop()
    try:
        pl_rows, charges = parse_pnl_excel(file_bytes)
    except Exception as e:
        st.error(f"Failed to parse file: {e}")
        st.stop()
    dates = [r["trade_date"] for r in pl_rows]
    st.info(
        f"**{len(pl_rows)} P&L rows parsed** — {min(dates)} → {max(dates)}"
        f" · charges date: {charges['date']}"
    )
    st.dataframe(
        pd.DataFrame(pl_rows)[["symbol", "trade_date", "buy_value", "sell_value", "realized_pnl"]].head(5),
        use_container_width=True,
        hide_index=True,
    )
    if st.button("Insert into DB", key="btn_pnl"):
        try:
            conn = get_connection()
            try:
                pl_inserted, pl_skipped = insert_pl(conn, pl_rows)
                ch_inserted, ch_skipped = insert_charges(conn, charges)
            finally:
                conn.close()
        except Exception as e:
            st.error(f"Database error: {e}")
            st.stop()
        st.success(
            f"daily_pl: {pl_inserted} rows inserted, {pl_skipped} duplicates skipped. "
            f"daily_charges: {ch_inserted} row inserted, {ch_skipped} duplicates skipped."
        )

st.divider()

# ── Upload Trade Book ─────────────────────────────────────────────────────────

st.subheader("Upload Trade Book")
st.write(
    "Upload a Zerodha F&O tradebook Excel file (.xlsx). "
    "Trades are aggregated by symbol + date before inserting."
)

uploaded_tb = st.file_uploader("Choose tradebook file", type=["xlsx"], key="tradebook")

if uploaded_tb is not None:
    file_bytes = uploaded_tb.getvalue()
    try:
        validate_tradebook_file(uploaded_tb.name, file_bytes)
    except ValueError as e:
        st.error(f"Validation failed: {e}")
        st.stop()
    try:
        trade_rows = parse_tradebook_excel(file_bytes)
    except Exception as e:
        st.error(f"Failed to parse file: {e}")
        st.stop()
    dates = [r["trade_date"] for r in trade_rows]
    st.info(f"**{len(trade_rows)} aggregated rows parsed** — {min(dates)} → {max(dates)}")
    st.dataframe(
        pd.DataFrame(trade_rows)[["symbol", "trade_date", "trade_type", "quantity", "price"]].head(5),
        use_container_width=True,
        hide_index=True,
    )
    if st.button("Insert into DB", key="btn_trades"):
        try:
            conn = get_connection()
            try:
                inserted, skipped = insert_trades(conn, trade_rows)
            finally:
                conn.close()
        except Exception as e:
            st.error(f"Database error: {e}")
            st.stop()
        st.success(f"Done. {inserted} rows inserted, {skipped} duplicates skipped.")
