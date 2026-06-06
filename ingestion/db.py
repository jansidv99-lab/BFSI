import os
import warnings

import psycopg2
from psycopg2 import sql as pgsql
from psycopg2.extras import execute_values

# ── Schema ───────────────────────────────────────────────────────────────────

_CREATE_POSITIONS = """
CREATE TABLE IF NOT EXISTS daily_positions (
    symbol                VARCHAR    NOT NULL,
    trade_date            DATE       NOT NULL,
    segment               VARCHAR,
    position_type         VARCHAR,
    open_quantity         NUMERIC,
    open_average          NUMERIC,
    open_value            NUMERIC,
    previous_close_price  NUMERIC,
    closing_value         NUMERIC,
    unrealized_profit     NUMERIC,
    unrealized_profit_pct NUMERIC,
    PRIMARY KEY (trade_date, symbol)
);
"""

_CREATE_DAILY_PL = """
CREATE TABLE IF NOT EXISTS daily_pl (
    symbol                 VARCHAR  NOT NULL,
    trade_date             DATE     NOT NULL,
    quantity               NUMERIC,
    buy_value              NUMERIC,
    sell_value             NUMERIC,
    realized_pnl           NUMERIC,
    realized_pnl_pct       NUMERIC,
    previous_closing_price NUMERIC,
    open_quantity          NUMERIC,
    open_quantity_type     VARCHAR,
    open_value             NUMERIC,
    unrealized_pnl         NUMERIC,
    unrealized_pnl_pct     NUMERIC,
    PRIMARY KEY (trade_date, symbol)
);
"""

_CREATE_DAILY_TRADES = """
CREATE TABLE IF NOT EXISTS daily_trades (
    symbol               VARCHAR    NOT NULL,
    trade_date           DATE       NOT NULL,
    trade_type           VARCHAR,
    quantity             NUMERIC,
    price                NUMERIC,
    order_execution_time TIMESTAMP,
    PRIMARY KEY (trade_date, symbol)
);
"""

_CREATE_DAILY_CHARGES = """
CREATE TABLE IF NOT EXISTS daily_charges (
    date                          DATE PRIMARY KEY,
    brokerage                     NUMERIC,
    exchange_transaction_charges  NUMERIC,
    clearing_charges              NUMERIC,
    central_gst                   NUMERIC,
    state_gst                     NUMERIC,
    integrated_gst                NUMERIC,
    securities_transaction_tax    NUMERIC,
    sebi_turnover_fees            NUMERIC,
    stamp_duty                    NUMERIC,
    ipft                          NUMERIC
);
"""

# ── Positions ────────────────────────────────────────────────────────────────

_INSERT_POSITIONS = """
INSERT INTO daily_positions (
    symbol, trade_date, segment, position_type,
    open_quantity, open_average, open_value,
    previous_close_price, closing_value,
    unrealized_profit, unrealized_profit_pct
) VALUES %s
ON CONFLICT (trade_date, symbol) DO NOTHING
RETURNING symbol
"""

_POSITIONS_COLS = [
    "symbol", "trade_date", "segment", "position_type",
    "open_quantity", "open_average", "open_value",
    "previous_close_price", "closing_value",
    "unrealized_profit", "unrealized_profit_pct",
]

# ── Daily P&L ────────────────────────────────────────────────────────────────

_INSERT_PL = """
INSERT INTO daily_pl (
    symbol, trade_date, quantity, buy_value, sell_value,
    realized_pnl, realized_pnl_pct, previous_closing_price,
    open_quantity, open_quantity_type, open_value,
    unrealized_pnl, unrealized_pnl_pct
) VALUES %s
ON CONFLICT (trade_date, symbol) DO NOTHING
RETURNING symbol
"""

_PL_COLS = [
    "symbol", "trade_date", "quantity", "buy_value", "sell_value",
    "realized_pnl", "realized_pnl_pct", "previous_closing_price",
    "open_quantity", "open_quantity_type", "open_value",
    "unrealized_pnl", "unrealized_pnl_pct",
]

# ── Daily Trades ─────────────────────────────────────────────────────────────

_INSERT_TRADES = """
INSERT INTO daily_trades (
    symbol, trade_date, trade_type, quantity, price, order_execution_time
) VALUES %s
ON CONFLICT (trade_date, symbol) DO NOTHING
RETURNING symbol
"""

_TRADES_COLS = [
    "symbol", "trade_date", "trade_type", "quantity", "price", "order_execution_time",
]

# ── Daily Charges ─────────────────────────────────────────────────────────────

_INSERT_CHARGES = """
INSERT INTO daily_charges (
    date, brokerage, exchange_transaction_charges, clearing_charges,
    central_gst, state_gst, integrated_gst, securities_transaction_tax,
    sebi_turnover_fees, stamp_duty, ipft
) VALUES (
    %(date)s, %(brokerage)s, %(exchange_transaction_charges)s, %(clearing_charges)s,
    %(central_gst)s, %(state_gst)s, %(integrated_gst)s, %(securities_transaction_tax)s,
    %(sebi_turnover_fees)s, %(stamp_duty)s, %(ipft)s
)
ON CONFLICT (date) DO NOTHING
RETURNING date
"""


# ── Connection ───────────────────────────────────────────────────────────────

def get_connection():
    password = os.environ.get("PG_PASSWORD")
    if password is None:
        warnings.warn(
            "PG_PASSWORD env var is not set; falling back to default 'chatbot123'. "
            "Set PG_PASSWORD explicitly in production.",
            stacklevel=2,
        )
        password = "chatbot123"
    return psycopg2.connect(
        host=os.environ.get("PG_HOST", "localhost"),
        port=int(os.environ.get("PG_PORT", "5432")),
        dbname=os.environ.get("PG_DB", "chatbot"),
        user=os.environ.get("PG_USER", "chatbot"),
        password=password,
    )


# ── Schema management ────────────────────────────────────────────────────────

def ensure_schema(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(_CREATE_POSITIONS)
        cur.execute(_CREATE_DAILY_PL)
        cur.execute(_CREATE_DAILY_TRADES)
        cur.execute(_CREATE_DAILY_CHARGES)
    conn.commit()


def list_tables(conn) -> list[str]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' ORDER BY table_name"
        )
        return [row[0] for row in cur.fetchall()]


_KNOWN_TABLES = ("daily_positions", "daily_pl", "daily_trades", "daily_charges")

_TABLE_DATE_COLS: dict[str, str] = {
    "daily_positions": "trade_date",
    "daily_pl":        "trade_date",
    "daily_trades":    "trade_date",
    "daily_charges":   "date",
}


def get_row_counts(conn) -> dict[str, int | None]:
    """Return row count per known table. None means the table doesn't exist yet."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name IN %s",
            (_KNOWN_TABLES,),
        )
        existing = {row[0] for row in cur.fetchall()}

    if not existing:
        return {t: None for t in _KNOWN_TABLES}

    # Single round-trip: names come from the hardcoded _KNOWN_TABLES tuple, never user input
    union_sql = " UNION ALL ".join(  # noqa: S608
        f"SELECT '{t}', COUNT(*) FROM {t}" for t in existing
    )
    with conn.cursor() as cur:
        cur.execute(union_sql)
        live: dict[str, int] = dict(cur.fetchall())

    return {t: live.get(t) if t in existing else None for t in _KNOWN_TABLES}


def get_date_ranges(conn) -> dict[str, dict]:
    """Return {table: {min: date, max: date}} for tables that have at least one row."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name IN %s",
            (_KNOWN_TABLES,),
        )
        existing = {row[0] for row in cur.fetchall()}

    tables_to_query = {t: c for t, c in _TABLE_DATE_COLS.items() if t in existing}
    if not tables_to_query:
        return {}

    parts = [
        pgsql.SQL("SELECT {tname}, MIN({col}), MAX({col}) FROM {tbl}").format(
            tname=pgsql.Literal(table),
            col=pgsql.Identifier(date_col),
            tbl=pgsql.Identifier(table),
        )
        for table, date_col in tables_to_query.items()
    ]
    with conn.cursor() as cur:
        cur.execute(pgsql.SQL(" UNION ALL ").join(parts))
        rows = cur.fetchall()

    return {
        table: {"min": min_date, "max": max_date}
        for table, min_date, max_date in rows
        if min_date and max_date
    }


def get_table_schemas(conn) -> dict[str, list[dict]]:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT
                c.table_name,
                c.column_name,
                c.data_type,
                c.is_nullable,
                c.ordinal_position,
                CASE WHEN tc.constraint_type = 'PRIMARY KEY' THEN true ELSE false END AS is_primary_key
            FROM information_schema.columns c
            LEFT JOIN information_schema.key_column_usage kcu
                ON c.table_schema = kcu.table_schema
                AND c.table_name  = kcu.table_name
                AND c.column_name = kcu.column_name
            LEFT JOIN information_schema.table_constraints tc
                ON kcu.constraint_name = tc.constraint_name
                AND tc.constraint_type = 'PRIMARY KEY'
            WHERE c.table_schema = 'public'
            ORDER BY c.table_name, c.ordinal_position
        """)
        rows = cur.fetchall()

    result: dict[str, list[dict]] = {}
    for table_name, col_name, data_type, is_nullable, _, is_pk in rows:
        result.setdefault(table_name, []).append({
            "column_name": col_name,
            "data_type": data_type,
            "is_nullable": is_nullable == "YES",
            "is_primary_key": bool(is_pk),
        })
    return result


# ── Insert functions ─────────────────────────────────────────────────────────

def insert_positions(conn, rows: list[dict]) -> tuple[int, int]:
    values = [tuple(row[col] for col in _POSITIONS_COLS) for row in rows]
    with conn.cursor() as cur:
        returned = execute_values(cur, _INSERT_POSITIONS, values, fetch=True)
    conn.commit()
    inserted = len(returned)
    skipped = len(rows) - inserted
    return inserted, skipped


def insert_pl(conn, rows: list[dict]) -> tuple[int, int]:
    values = [tuple(row[col] for col in _PL_COLS) for row in rows]
    with conn.cursor() as cur:
        returned = execute_values(cur, _INSERT_PL, values, fetch=True)
    conn.commit()
    inserted = len(returned)
    skipped = len(rows) - inserted
    return inserted, skipped


def insert_trades(conn, rows: list[dict]) -> tuple[int, int]:
    values = [tuple(row[col] for col in _TRADES_COLS) for row in rows]
    with conn.cursor() as cur:
        returned = execute_values(cur, _INSERT_TRADES, values, fetch=True)
    conn.commit()
    inserted = len(returned)
    skipped = len(rows) - inserted
    return inserted, skipped


def insert_charges(conn, charges: dict) -> tuple[int, int]:
    with conn.cursor() as cur:
        cur.execute(_INSERT_CHARGES, charges)
        returned = cur.fetchall()
    conn.commit()
    inserted = len(returned)
    skipped = 1 - inserted
    return inserted, skipped
