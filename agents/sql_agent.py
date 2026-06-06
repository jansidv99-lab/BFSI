"""General-purpose conversational SQL data analyzer agent.

Graph: load_schema → plan_sql → run_sql → compose_answer
       with a clarify_sql retry loop on SQL errors (up to 2 retries).
"""
import functools
import os
import re

import psycopg2
from langchain_ollama import ChatOllama
from langgraph.graph import END, StateGraph
from opentelemetry import trace as otel_trace
from typing_extensions import TypedDict

from ingestion.db import get_connection, get_date_ranges, get_table_schemas

tracer = otel_trace.get_tracer(__name__)

_MAX_SQL_RETRIES = 2
_MAX_RESULT_ROWS = 50  # rows forwarded to the answer LLM


@functools.lru_cache(maxsize=1)
def _get_llm() -> ChatOllama:
    return ChatOllama(
        base_url=os.environ.get("OLLAMA_HOST", "http://localhost:11434"),
        model=os.environ.get("MODEL_NAME", "gemma4:e2b"),
        request_timeout=120.0,
    )


# ── State ─────────────────────────────────────────────────────────────────────


class DataChatState(TypedDict):
    question: str
    history: list[dict]        # [{"role": "user"|"assistant", "content": "..."}]
    schema_context: str
    sql_query: str
    sql_error: str
    query_results: list[dict]
    data_found: bool
    answer: str
    retry_count: int


# ── Helpers ───────────────────────────────────────────────────────────────────


def _extract_sql(text: str) -> str:
    match = re.search(r"```(?:sql)?\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
    return match.group(1).strip() if match else text.strip()


def _assert_select_only(sql: str) -> None:
    first = sql.strip().split()[0].upper() if sql.strip() else ""
    if first != "SELECT":
        raise ValueError(f"Only SELECT queries are allowed, got: {first!r}")


def _rows_to_text(rows: list[dict]) -> str:
    if not rows:
        return "(no rows returned)"
    headers = list(rows[0].keys())
    lines = [" | ".join(headers), "-" * max(len(" | ".join(headers)), 4)]
    for row in rows[:_MAX_RESULT_ROWS]:
        lines.append(" | ".join(str(row.get(h, "")) for h in headers))
    if len(rows) > _MAX_RESULT_ROWS:
        lines.append(f"... and {len(rows) - _MAX_RESULT_ROWS} more rows")
    return "\n".join(lines)


def _history_to_text(history: list[dict]) -> str:
    """Render conversation history as a readable block for the LLM."""
    if not history:
        return ""
    parts = []
    for msg in history[-6:]:  # last 3 turns (6 messages) to keep context bounded
        role = msg.get("role", "user").capitalize()
        parts.append(f"{role}: {msg.get('content', '')}")
    return "\n".join(parts)


# ── Nodes ─────────────────────────────────────────────────────────────────────


def load_schema(state: DataChatState) -> DataChatState:
    with tracer.start_as_current_span("load_schema") as span:
        span.set_attribute("openinference.span.kind", "TOOL")
        span.set_attribute("tool.name", "get_table_schemas")
        try:
            conn = get_connection()
            try:
                schemas = get_table_schemas(conn)
                date_ranges = get_date_ranges(conn)
            finally:
                conn.close()

            if not schemas:
                raise ValueError("No tables found — upload data first.")

            lines = []
            for table, columns in schemas.items():
                pk_cols = [c["column_name"] for c in columns if c["is_primary_key"]]
                pk_str = f"PK: {', '.join(pk_cols)}" if pk_cols else ""
                lines.append(f"Table: {table}" + (f"  ({pk_str})" if pk_str else ""))
                for col in columns:
                    nullable = "" if col["is_nullable"] else " NOT NULL"
                    pk_mark = " [PK]" if col["is_primary_key"] else ""
                    lines.append(f"  {col['column_name']}  {col['data_type']}{nullable}{pk_mark}")
                lines.append("")
            schema_context = "\n".join(lines).strip()

            if date_ranges:
                dr_lines = ["\nData date ranges:"]
                for t, dr in date_ranges.items():
                    dr_lines.append(f"  {t}: {dr['min']} to {dr['max']}")
                schema_context += "\n" + "\n".join(dr_lines)

            span.set_attribute("output.value", f"Loaded {len(schemas)} tables")
        except Exception as e:
            schema_context = f"(Schema unavailable: {e})"
            span.set_attribute("output.value", str(e))
    return {**state, "schema_context": schema_context}


def plan_sql(state: DataChatState) -> DataChatState:
    history_text = _history_to_text(state.get("history", []))
    history_block = f"\nConversation so far:\n{history_text}\n" if history_text else ""

    prompt = (
        f"You are a PostgreSQL expert. Given the database schema below, "
        f"write a single read-only SELECT query to answer the user's question.\n\n"
        f"Schema:\n{state['schema_context']}\n"
        f"{history_block}\n"
        f"Current question: {state['question']}\n\n"
        "Rules:\n"
        "1. Use only column names that appear in the schema above.\n"
        "2. For aggregations use GROUP BY with SUM/AVG/COUNT/MAX/MIN.\n"
        "3. For listing queries add LIMIT 500.\n"
        "4. For ranking (top N / worst N) use ORDER BY + LIMIT N.\n"
        "5. SELECT only — no INSERT, UPDATE, DELETE, DROP, CREATE.\n"
        "Return ONLY the SQL inside a ```sql``` code block. No explanation."
    )
    with tracer.start_as_current_span("plan_sql") as span:
        span.set_attribute("openinference.span.kind", "LLM")
        span.set_attribute("llm.model_name", os.environ.get("MODEL_NAME", "gemma4:e2b"))
        span.set_attribute("input.value", state["question"])
        raw = _get_llm().invoke(prompt).content
        sql = _extract_sql(raw)
        span.set_attribute("output.value", sql)
    return {**state, "sql_query": sql, "sql_error": ""}


def run_sql(state: DataChatState) -> DataChatState:
    sql = state["sql_query"]
    with tracer.start_as_current_span("run_sql") as span:
        span.set_attribute("openinference.span.kind", "TOOL")
        span.set_attribute("tool.name", "postgres_query")
        span.set_attribute("input.value", sql)
        try:
            _assert_select_only(sql)
            conn = get_connection()
            try:
                # Validate with EXPLAIN first (read-only)
                with conn.cursor() as cur:
                    cur.execute("EXPLAIN " + sql)
                conn.rollback()
                # Execute
                with conn.cursor() as cur:
                    cur.execute(sql)
                    cols = [d[0] for d in cur.description] if cur.description else []
                    rows = [dict(zip(cols, row)) for row in cur.fetchall()]
            finally:
                conn.close()
            span.set_attribute("output.value", f"{len(rows)} rows")
            return {**state, "query_results": rows, "data_found": len(rows) > 0, "sql_error": ""}
        except (psycopg2.Error, ValueError) as e:
            span.set_attribute("output.value", f"error: {e}")
            return {**state, "query_results": [], "data_found": False, "sql_error": str(e)}
        except Exception as e:
            span.set_attribute("output.value", f"error: {e}")
            return {**state, "query_results": [], "data_found": False, "sql_error": str(e)}


def clarify_sql(state: DataChatState) -> DataChatState:
    retry_count = state.get("retry_count", 0) + 1
    error = state.get("sql_error") or "Query returned no rows."
    prompt = (
        f"Schema:\n{state['schema_context']}\n\n"
        f"Question: {state['question']}\n\n"
        f"Previous SQL:\n```sql\n{state['sql_query']}\n```\n\n"
        f"Problem: {error}\n\n"
        "Fix the SQL so it correctly answers the question. "
        "Return ONLY the corrected SQL in a ```sql``` block."
    )
    with tracer.start_as_current_span("clarify_sql") as span:
        span.set_attribute("openinference.span.kind", "LLM")
        span.set_attribute("input.value", f"retry {retry_count}: {error}")
        raw = _get_llm().invoke(prompt).content
        sql = _extract_sql(raw)
        span.set_attribute("output.value", sql)
    return {**state, "sql_query": sql, "retry_count": retry_count, "sql_error": ""}


def compose_answer(state: DataChatState) -> DataChatState:
    history_text = _history_to_text(state.get("history", []))
    history_block = f"\nConversation so far:\n{history_text}\n" if history_text else ""
    results_text = _rows_to_text(state["query_results"])

    if not state["data_found"]:
        error = state.get("sql_error", "")
        if error:
            answer = (
                f"I wasn't able to retrieve data for that question. "
                f"SQL error: {error}\n\n"
                "Please rephrase your question or check that the relevant data has been uploaded."
            )
        else:
            answer = (
                "The query ran successfully but returned no rows. "
                "The data you're asking about may not exist in the database yet."
            )
        return {**state, "answer": answer}

    prompt = (
        f"You are a helpful data analyst. Answer the user's question based on the query results below.\n"
        f"{history_block}\n"
        f"Current question: {state['question']}\n\n"
        f"Query results:\n{results_text}\n\n"
        "Provide a clear, concise answer with specific numbers from the data. "
        "Use markdown formatting (tables, bold, bullet points) where it improves readability. "
        "Reference the conversation history if the question builds on a previous exchange."
    )
    with tracer.start_as_current_span("compose_answer") as span:
        span.set_attribute("openinference.span.kind", "LLM")
        span.set_attribute("llm.model_name", os.environ.get("MODEL_NAME", "gemma4:e2b"))
        span.set_attribute("input.value", state["question"])
        answer = _get_llm().invoke(prompt).content.strip()
        span.set_attribute("output.value", answer[:500])
    return {**state, "answer": answer}


# ── Edges ─────────────────────────────────────────────────────────────────────


def _route_after_run(state: DataChatState) -> str:
    if not state.get("sql_error"):
        return "compose_answer"
    if state.get("retry_count", 0) < _MAX_SQL_RETRIES:
        return "clarify_sql"
    return "compose_answer"  # give up, compose_answer handles the error case


# ── Graph ─────────────────────────────────────────────────────────────────────

_builder = StateGraph(DataChatState)

_builder.add_node("load_schema", load_schema)
_builder.add_node("plan_sql", plan_sql)
_builder.add_node("run_sql", run_sql)
_builder.add_node("clarify_sql", clarify_sql)
_builder.add_node("compose_answer", compose_answer)

_builder.set_entry_point("load_schema")
_builder.add_edge("load_schema", "plan_sql")
_builder.add_edge("plan_sql", "run_sql")
_builder.add_conditional_edges("run_sql", _route_after_run)
_builder.add_edge("clarify_sql", "run_sql")
_builder.add_edge("compose_answer", END)

data_chat_graph = _builder.compile()
