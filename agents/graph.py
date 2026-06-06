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

# ── LLM (lazy — reads env vars at first call, not at import time) ────────────

@functools.lru_cache(maxsize=1)
def _get_llm() -> ChatOllama:
    return ChatOllama(
        base_url=os.environ.get("OLLAMA_HOST", "http://localhost:11434"),
        model=os.environ.get("MODEL_NAME", "gemma4:e2b"),
        request_timeout=120.0,  # fail fast if Ollama stalls mid-generation
    )

# ── Table business descriptions (static) ────────────────────────────────────

_TABLE_DESCRIPTIONS = {
    "daily_positions": (
        "Open F&O positions snapshot. One row per open position (symbol + date). "
        "Use for: current exposure, unrealized P&L, holdings, risk concentration."
    ),
    "daily_pl": (
        "Realized and unrealized P&L summary per symbol per day. "
        "Use for: profitability, gains/losses, net performance, performance attribution."
    ),
    "daily_trades": (
        "Aggregated trade executions per symbol per day (quantity summed, price averaged, "
        "execution time = max). Use for: trade history, turnover, buy/sell activity, trading frequency."
    ),
    "daily_charges": (
        "Brokerage and regulatory charges per day (one row per date). "
        "Use for: brokerage impact, total cost, charge breakdown."
    ),
}

# ── State ────────────────────────────────────────────────────────────────────


class AnalysisState(TypedDict):
    question: str
    schema_context: str
    sql_query: str
    sql_valid: bool
    analytics_valid: bool
    validation_error: str
    query_results: list[dict]
    data_found: bool
    analysis: str
    final_response: str
    retry_count_sql: int
    retry_count_analysis: int


# ── Helpers ──────────────────────────────────────────────────────────────────

_REFUSAL_RE = re.compile(
    r"\b(cannot|can't|couldn't|unable\s+to|no\s+data|no\s+results?|"
    r"don't\s+have|not\s+available|sorry|insufficient\s+data)\b",
    re.IGNORECASE,
)
_QUANTITY_RE = re.compile(r"\d+(?:[.,]\d+)?")  # any number, including single digits
_MIN_CHARS = 30


def _check_analysis(analysis: str) -> tuple[bool, str]:
    text = analysis.strip()
    if len(text) < _MIN_CHARS:
        return False, f"Analysis too short ({len(text)} chars) — provide a complete answer with context."
    if _REFUSAL_RE.search(text):
        return False, "Analysis signals inability to answer — revise the SQL to retrieve relevant data."
    if not _QUANTITY_RE.search(text):
        return False, "Analysis contains no numbers — include specific values from the query results."
    return True, ""


def _assert_select_only(sql: str) -> None:
    first_word = sql.strip().split()[0].upper() if sql.strip() else ""
    if first_word != "SELECT":
        raise ValueError(f"Only SELECT queries are allowed, got: {first_word!r}")


def _extract_sql(text: str) -> str:
    """Pull SQL out of a fenced code block or return text as-is."""
    match = re.search(r"```(?:sql)?\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return text.strip()


def _rows_to_text(rows: list) -> str:
    if not rows:
        return "(no rows)"
    headers = list(rows[0].keys())
    lines = [" | ".join(headers)]
    lines.append("-" * len(lines[0]))
    for row in rows[:50]:
        lines.append(" | ".join(str(row.get(h, "")) for h in headers))
    if len(rows) > 50:
        lines.append(f"... and {len(rows) - 50} more rows")
    return "\n".join(lines)


# ── Nodes ────────────────────────────────────────────────────────────────────

def supervisor(state: AnalysisState) -> AnalysisState:
    prompt = (
        "You are a routing agent for a Zerodha F&O trading analytics system.\n\n"
        "The system has exactly these four PostgreSQL tables:\n"
        "  daily_positions  — open position snapshots per symbol per day "
        "(unrealized P&L, exposure, quantity, average price)\n"
        "  daily_pl         — realized and unrealized P&L per symbol per day "
        "(buy/sell values, realized PnL, unrealized PnL)\n"
        "  daily_trades     — trade executions aggregated per symbol per day "
        "(trade type, quantity, price, execution time)\n"
        "  daily_charges    — brokerage and regulatory charges per day "
        "(brokerage, GST, STT, stamp duty, SEBI fees)\n\n"
        f"User question: {state['question']}\n\n"
        "Can this question be answered using only these tables? "
        "Reply with exactly one word: YES or NO."
    )
    with tracer.start_as_current_span("supervisor") as span:
        span.set_attribute("openinference.span.kind", "LLM")
        span.set_attribute("llm.model_name", os.environ.get("MODEL_NAME", "gemma4:e2b"))
        span.set_attribute("input.value", state["question"])
        response = _get_llm().invoke(prompt).content.strip().upper()
        span.set_attribute("output.value", response)
    if "YES" in response:
        return {**state, "final_response": ""}
    return {
        **state,
        "final_response": (
            "I can only answer questions about your F&O trading data "
            "(positions, P&L, trades, charges). Please ask something related to that data."
        ),
    }


def schema_agent(state: AnalysisState) -> AnalysisState:
    with tracer.start_as_current_span("schema_agent") as span:
        span.set_attribute("openinference.span.kind", "TOOL")
        span.set_attribute("tool.name", "get_table_schemas")
        span.set_attribute("input.value", "fetch full column schema from DB")
        try:
            conn = get_connection()
            try:
                schemas = get_table_schemas(conn)
                date_ranges = get_date_ranges(conn)
            finally:
                conn.close()

            if not schemas:
                raise ValueError("No tables found in DB — run 'Create Tables in DB' on the Upload page first.")

            lines = []
            for table, columns in schemas.items():
                desc = _TABLE_DESCRIPTIONS.get(table, "")
                pk_cols = [c["column_name"] for c in columns if c["is_primary_key"]]
                pk_str = f"PK: {', '.join(pk_cols)}" if pk_cols else "no PK"
                lines.append(f"{table} ({pk_str})")
                if desc:
                    lines.append(f"  Description: {desc}")
                for col in columns:
                    nullable = "" if col["is_nullable"] else " NOT NULL"
                    pk_mark = " [PK]" if col["is_primary_key"] else ""
                    lines.append(f"  {col['column_name']}  {col['data_type']}{nullable}{pk_mark}")
                lines.append("")
            schema_context = "\n".join(lines).strip()

            if date_ranges:
                dr_lines = ["\nData availability:"]
                for t, dr in date_ranges.items():
                    dr_lines.append(f"  {t}: {dr['min']} → {dr['max']}")
                schema_context += "\n" + "\n".join(dr_lines)

            status = f"Fetched schema for tables: {', '.join(schemas.keys())}"
        except Exception as e:
            schema_context = "\n".join(
                f"{t}: {desc}" for t, desc in _TABLE_DESCRIPTIONS.items()
            )
            status = f"Could not fetch live schema ({e}); using static description fallback."

        span.set_attribute("output.value", status)
    return {**state, "schema_context": schema_context}


def sql_planner(state: AnalysisState) -> AnalysisState:
    prompt = (
        f"Schema:\n{state['schema_context']}\n\n"
        f"Question: {state['question']}\n\n"
        "Write a single read-only PostgreSQL SELECT query that answers this question.\n"
        "Rules:\n"
        "1. Use only column names exactly as listed in the schema above — do not invent columns.\n"
        "2. For aggregation questions (totals, averages, rankings, counts), use GROUP BY with "
        "the appropriate aggregate function (SUM, AVG, COUNT, MAX, MIN).\n"
        "3. For row-level or listing queries, add LIMIT 500. "
        "For aggregations that naturally return few rows, LIMIT may be omitted.\n"
        "4. For ranking questions (top N, worst N), use ORDER BY with the relevant column and "
        "include LIMIT N.\n"
        "Return ONLY the SQL inside a ```sql``` code block. No explanation."
    )
    with tracer.start_as_current_span("sql_planner") as span:
        span.set_attribute("openinference.span.kind", "LLM")
        span.set_attribute("llm.model_name", os.environ.get("MODEL_NAME", "gemma4:e2b"))
        span.set_attribute("input.value", state["question"])
        raw = _get_llm().invoke(prompt).content
        sql = _extract_sql(raw)
        span.set_attribute("output.value", sql)
    return {**state, "sql_query": sql}


def sql_validator(state: AnalysisState) -> AnalysisState:
    sql = state["sql_query"]
    with tracer.start_as_current_span("sql_validator") as span:
        span.set_attribute("openinference.span.kind", "TOOL")
        span.set_attribute("tool.name", "sql_explain")
        span.set_attribute("input.value", sql)
        try:
            _assert_select_only(sql)
            conn = get_connection()
            try:
                with conn.cursor() as cur:
                    cur.execute("EXPLAIN " + sql)
            finally:
                conn.rollback()
                conn.close()
            span.set_attribute("output.value", "valid")
            return {**state, "sql_valid": True, "validation_error": ""}
        except psycopg2.Error as e:
            span.set_attribute("output.value", str(e))
            return {**state, "sql_valid": False, "validation_error": str(e)}
        except Exception as e:
            span.set_attribute("output.value", str(e))
            return {**state, "sql_valid": False, "validation_error": str(e)}


def execute_sql(state: AnalysisState) -> AnalysisState:
    with tracer.start_as_current_span("execute_sql") as span:
        span.set_attribute("openinference.span.kind", "TOOL")
        span.set_attribute("tool.name", "postgres_query")
        span.set_attribute("input.value", state["sql_query"])
        try:
            _assert_select_only(state["sql_query"])
            conn = get_connection()
            try:
                with conn.cursor() as cur:
                    cur.execute(state["sql_query"])
                    columns = [desc[0] for desc in cur.description] if cur.description else []
                    rows = [dict(zip(columns, row)) for row in cur.fetchall()]
            finally:
                conn.close()
            span.set_attribute("output.value", f"{len(rows)} rows returned")
            return {**state, "query_results": rows, "data_found": len(rows) > 0}
        except Exception as e:
            span.set_attribute("output.value", str(e))
            return {
                **state,
                "query_results": [],
                "data_found": False,
                "validation_error": str(e),
                "sql_valid": False,
            }


def clarification_agent(state: AnalysisState) -> AnalysisState:
    retry_count_sql = state.get("retry_count_sql", 0) + 1
    error = state.get("validation_error") or "The query returned no rows."
    prompt = (
        f"Schema:\n{state['schema_context']}\n\n"
        f"Question: {state['question']}\n\n"
        f"Previous SQL:\n```sql\n{state['sql_query']}\n```\n\n"
        f"Problem: {error}\n\n"
        "Fix the SQL query to correctly answer the question. "
        "Return ONLY the corrected SQL inside a ```sql``` code block."
    )
    with tracer.start_as_current_span("clarification_agent") as span:
        span.set_attribute("openinference.span.kind", "LLM")
        span.set_attribute("llm.model_name", os.environ.get("MODEL_NAME", "gemma4:e2b"))
        span.set_attribute("input.value", state["question"])
        raw = _get_llm().invoke(prompt).content
        sql = _extract_sql(raw)
        span.set_attribute("output.value", sql)
    return {**state, "sql_query": sql, "retry_count_sql": retry_count_sql, "sql_valid": False, "validation_error": ""}


def analytics_agent(state: AnalysisState) -> AnalysisState:
    retry_count_analysis = state.get("retry_count_analysis", 0) + 1
    results_text = _rows_to_text(state["query_results"])
    feedback = ""
    if retry_count_analysis > 1 and state.get("validation_error"):
        feedback = (
            f"\n\nYour previous analysis was rejected: {state['validation_error']} "
            "Fix this issue in your response."
        )
    prompt = (
        f"Question: {state['question']}\n\n"
        f"Query results:\n{results_text}\n\n"
        "Analyse these results and provide a clear, concise answer to the question. "
        f"Include specific numbers. Use markdown formatting.{feedback}"
    )
    with tracer.start_as_current_span("analytics_agent") as span:
        span.set_attribute("openinference.span.kind", "LLM")
        span.set_attribute("llm.model_name", os.environ.get("MODEL_NAME", "gemma4:e2b"))
        span.set_attribute("input.value", state["question"])
        analysis = _get_llm().invoke(prompt).content.strip()
        span.set_attribute("output.value", analysis)
    return {**state, "analysis": analysis, "retry_count_analysis": retry_count_analysis}


def validation_node(state: AnalysisState) -> AnalysisState:
    with tracer.start_as_current_span("validation_node") as span:
        span.set_attribute("openinference.span.kind", "CHAIN")
        analysis = state.get("analysis", "")
        span.set_attribute("input.value", analysis[:500])
        valid, error = _check_analysis(analysis)
        if valid:
            span.set_attribute("output.value", "pass")
            return {**state, "analytics_valid": True}
        span.set_attribute("output.value", f"fail: {error}")
        return {**state, "analytics_valid": False, "validation_error": error}


def response_formatter(state: AnalysisState) -> AnalysisState:
    with tracer.start_as_current_span("response_formatter") as span:
        span.set_attribute("openinference.span.kind", "CHAIN")
        span.set_attribute("input.value", state.get("analysis", "")[:500])
        if state.get("analysis"):
            final = state["analysis"]
        else:
            final = (
                "I was unable to answer your question after several attempts. "
                "Please try rephrasing or check that the relevant data has been uploaded."
            )
        span.set_attribute("output.value", final[:500])
        return {**state, "final_response": final}


# ── Conditional edge functions ───────────────────────────────────────────────

def _route_supervisor(state: AnalysisState) -> str:
    return "schema_agent" if not state.get("final_response") else END


def _route_validator(state: AnalysisState) -> str:
    return "execute_sql" if state["sql_valid"] else "clarification_agent"


def _route_execute(state: AnalysisState) -> str:
    return "analytics_agent" if state["data_found"] else "clarification_agent"


def _route_clarification(state: AnalysisState) -> str:
    return "sql_validator" if state.get("retry_count_sql", 0) < 3 else "response_formatter"


def _route_validation(state: AnalysisState) -> str:
    if state.get("analytics_valid"):
        return "response_formatter"
    return "analytics_agent" if state.get("retry_count_analysis", 0) < 3 else "response_formatter"


# ── Graph ────────────────────────────────────────────────────────────────────

_builder = StateGraph(AnalysisState)

_builder.add_node("supervisor", supervisor)
_builder.add_node("schema_agent", schema_agent)
_builder.add_node("sql_planner", sql_planner)
_builder.add_node("sql_validator", sql_validator)
_builder.add_node("execute_sql", execute_sql)
_builder.add_node("clarification_agent", clarification_agent)
_builder.add_node("analytics_agent", analytics_agent)
_builder.add_node("validation_node", validation_node)
_builder.add_node("response_formatter", response_formatter)

_builder.set_entry_point("supervisor")
_builder.add_conditional_edges("supervisor", _route_supervisor)
_builder.add_edge("schema_agent", "sql_planner")
_builder.add_edge("sql_planner", "sql_validator")
_builder.add_conditional_edges("sql_validator", _route_validator)
_builder.add_conditional_edges("execute_sql", _route_execute)
_builder.add_conditional_edges("clarification_agent", _route_clarification)
_builder.add_edge("analytics_agent", "validation_node")
_builder.add_conditional_edges("validation_node", _route_validation)
_builder.add_edge("response_formatter", END)

graph = _builder.compile()
