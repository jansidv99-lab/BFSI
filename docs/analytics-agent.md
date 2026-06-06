# F&O Analytics Agent — Developer Reference

The analytics agent is a LangGraph `StateGraph` defined in `agents/graph.py`. It takes a natural-language question about Zerodha F&O trading data, generates and validates a SQL query, executes it against PostgreSQL, and produces a markdown answer.

Entry point: `pages/analytics.py` calls `graph.stream(initial_state, stream_mode="updates")`, iterating over node-level updates to drive the `st.status` progress display.

---

## State

All nodes read from and write to `AnalysisState`, a `TypedDict` passed through the graph:

```python
class AnalysisState(TypedDict):
    question:              str          # original user question — never mutated
    schema_context:        str          # table schema string built by schema_agent
    sql_query:             str          # current SQL (rewritten on each clarification)
    sql_valid:             bool         # set by sql_validator
    analytics_valid:       bool         # set by validation_node
    validation_error:      str          # human-readable error for clarification_agent
    query_results:         list[dict]   # rows returned by execute_sql
    data_found:            bool         # True if query_results is non-empty
    analysis:              str          # markdown answer written by analytics_agent
    final_response:        str          # final string surfaced to the UI
    retry_count_sql:       int          # incremented by clarification_agent; SQL retries cap at 3
    retry_count_analysis:  int          # incremented by analytics_agent; analysis retries cap at 3
```

The two retry counters are independent: SQL failures and analysis failures each have their own budget of 3. Every node returns `{**state, <changed keys>}` — nodes only write the fields they own.

---

## Graph

```
supervisor ──(YES or ambiguous)──► schema_agent
    │
    └─(non-YES response)──► END   (early exit with denial message)

schema_agent
    │
    ▼
sql_planner
    │
    ▼
sql_validator ──(invalid)──► clarification_agent ──(retry_count_sql < 3)──► sql_validator
    │                                │
    │                    (retry_count_sql ≥ 3)
    │                                │
    ▼ (valid)                        ▼
execute_sql ──(no rows)──────────────┘
    │
    ▼ (rows found)
analytics_agent
    │
    ▼
validation_node ──(fail, retry_count_analysis < 3)──► analytics_agent
    │
    ▼ (pass, or retry_count_analysis ≥ 3)
response_formatter
    │
    ▼
END
```

---

## Nodes

### `supervisor`

**Kind:** `LLM`  
**Reads:** `question`  
**Writes:** `final_response`

Asks the LLM whether the question is answerable from the four F&O tables. If the response contains `"YES"`, `final_response` is set to `""` (falsy) and `_route_supervisor` continues to `schema_agent`. If the response does not contain `"YES"`, `final_response` is set to a denial message and `_route_supervisor` routes directly to `END` — the rest of the pipeline is skipped.

---

### `schema_agent`

**Kind:** `TOOL`  
**Reads:** nothing from state  
**Writes:** `schema_context`

Queries `information_schema.columns` and primary key constraints to build a formatted string describing every public table:

```
daily_positions (PK: trade_date, symbol)
  Description: Open F&O positions snapshot. ...
  symbol  character varying NOT NULL [PK]
  trade_date  date NOT NULL [PK]
  segment  character varying
  ...
```

This string is the sole source of schema information for all downstream LLM nodes. If the DB is unreachable, it falls back to a brief static description from `_TABLE_DESCRIPTIONS` (no column detail).

Business descriptions per table live in `_TABLE_DESCRIPTIONS` in `graph.py`. Edit that dict to change what context the LLM gets about each table's purpose.

---

### `sql_planner`

**Kind:** `LLM`  
**Reads:** `schema_context`, `question`  
**Writes:** `sql_query`

Generates a PostgreSQL SELECT query. The prompt instructs the model to:
- Use only column names from `schema_context`
- Use `GROUP BY` + aggregate functions for summary questions
- Apply `LIMIT 500` for row-level queries; omit it for aggregations
- Use `ORDER BY` + `LIMIT N` for ranking questions

SQL is extracted from a fenced ` ```sql ``` ` block via `_extract_sql()`. If the model returns SQL without a code fence, the raw text is used as-is.

---

### `sql_validator`

**Kind:** `TOOL`  
**Reads:** `sql_query`  
**Writes:** `sql_valid`, `validation_error`

Two checks, in order:
1. `_assert_select_only()` — rejects anything whose first token is not `SELECT`
2. `EXPLAIN <sql>` against a real PostgreSQL connection — catches syntax errors and references to non-existent columns/tables

Uses `conn.rollback()` before `conn.close()` to ensure no implicit transaction is left open. Sets `sql_valid: True` only if both checks pass.

---

### `execute_sql`

**Kind:** `TOOL`  
**Reads:** `sql_query`  
**Writes:** `query_results`, `data_found`, (on error) `validation_error`, `sql_valid`

Runs the validated SELECT and returns rows as `list[dict]`. Column names come from `cursor.description`. Sets `data_found: True` if at least one row was returned.

On exception (e.g., a race condition where the table was dropped between validation and execution), it sets `sql_valid: False` and `data_found: False`, routing back to `clarification_agent`.

---

### `clarification_agent`

**Kind:** `LLM`  
**Reads:** `schema_context`, `question`, `sql_query`, `validation_error`, `retry_count_sql`  
**Writes:** `sql_query`, `retry_count_sql`, `sql_valid` (reset to `False`), `validation_error` (cleared)

Attempts to fix the SQL by passing the previous query and the error message back to the LLM. Increments `retry_count_sql` on every call. When `retry_count_sql` reaches 3, `_route_clarification` short-circuits to `response_formatter` with a failure message.

The error message comes from either:
- `validation_error` set by `sql_validator` or `execute_sql` (syntax error, no rows, etc.)
- `validation_error` set by `validation_node` (if the analysis fails — though `validation_node` failure re-routes to `analytics_agent`, not back here)

---

### `analytics_agent`

**Kind:** `LLM`  
**Reads:** `question`, `query_results`, `retry_count_analysis`, `validation_error`  
**Writes:** `analysis`, `retry_count_analysis`

Converts `query_results` to a pipe-delimited text table via `_rows_to_text()` (capped at 50 displayed rows; remainder noted in the output). Asks the LLM to analyse the data and write a markdown answer with specific numbers. On retry (when `retry_count_analysis > 1`), appends the prior rejection reason from `validation_error` to the prompt so the model knows what to fix.

---

### `validation_node`

**Kind:** `CHAIN`  
**Reads:** `analysis`  
**Writes:** `analytics_valid`, (on failure) `validation_error`

Heuristic quality gate implemented by `_check_analysis()` in `graph.py`. Three failure conditions (checked in order):
- `len(analysis) < 30` (`_MIN_CHARS`) — analysis too short
- `_REFUSAL_RE` matches — analysis contains a refusal phrase (e.g. "cannot", "no data", "sorry", "unable to")
- `_QUANTITY_RE` does not match — analysis contains no numbers at all

All three must pass for `analytics_valid: True`. On failure, `validation_error` is set to the specific rejection reason and `_route_validation` re-routes to `analytics_agent` (up to 3 times via `retry_count_analysis`), then gives up and goes to `response_formatter`.

---

### `response_formatter`

**Kind:** `CHAIN`  
**Reads:** `analysis`  
**Writes:** `final_response`

If `analysis` is non-empty, it becomes `final_response`. Otherwise returns a standard failure message. This node always routes to `END`.

---

## Routing Functions

| Function | Condition | Routes to |
|---|---|---|
| `_route_supervisor` | `final_response` is falsy (YES response) | `schema_agent` |
| `_route_supervisor` | `final_response` is truthy (non-YES response) | `END` |
| `_route_validator` | `sql_valid: True` | `execute_sql` |
| `_route_validator` | `sql_valid: False` | `clarification_agent` |
| `_route_execute` | `data_found: True` | `analytics_agent` |
| `_route_execute` | `data_found: False` | `clarification_agent` |
| `_route_clarification` | `retry_count_sql < 3` | `sql_validator` |
| `_route_clarification` | `retry_count_sql >= 3` | `response_formatter` |
| `_route_validation` | `analytics_valid: True` | `response_formatter` |
| `_route_validation` | `analytics_valid: False` and `retry_count_analysis < 3` | `analytics_agent` |
| `_route_validation` | `analytics_valid: False` and `retry_count_analysis >= 3` | `response_formatter` |

---

## LLM Client

`ChatOllama` is initialized once (lazily) via `@functools.lru_cache(maxsize=1)`:

```python
@functools.lru_cache(maxsize=1)
def _get_llm() -> ChatOllama:
    return ChatOllama(
        base_url=os.environ.get("OLLAMA_HOST", "http://localhost:11434"),
        model=os.environ.get("MODEL_NAME", "gemma4:e2b"),
    )
```

`OLLAMA_HOST` and `MODEL_NAME` are read at first call, not at import time. This matters because Streamlit may load the module before env vars from `.env` are set. Call `_get_llm.cache_clear()` in tests if you need to swap the model between test cases.

All LLM calls are synchronous (`_get_llm().invoke(prompt)`). There is no streaming in the agent pipeline — the analytics page shows a spinner while the full graph runs.

---

## Observability

Every node opens a manual OpenTelemetry span following OpenInference conventions:

```python
with tracer.start_as_current_span("node_name") as span:
    span.set_attribute("openinference.span.kind", "LLM" | "TOOL" | "CHAIN")
    span.set_attribute("input.value", ...)
    span.set_attribute("output.value", ...)
```

The outer `fo_analysis` span is set in `pages/analytics.py`, wrapping the entire `graph.stream()` call. In Phoenix, you'll see a tree like:

```
fo_analysis (AGENT)
  supervisor (LLM)
  schema_agent (TOOL)
  sql_planner (LLM)
  sql_validator (TOOL)
  execute_sql (TOOL)          ← appears if sql was valid
  analytics_agent (LLM)       ← appears if rows returned
  validation_node (CHAIN)
  response_formatter (CHAIN)
```

Nodes that aren't reached (e.g., `clarification_agent` on a first-pass success) won't appear in the trace.

---

## Adding a New Node

1. **Define the function** following the existing pattern:
   ```python
   def my_node(state: AnalysisState) -> AnalysisState:
       with tracer.start_as_current_span("my_node") as span:
           span.set_attribute("openinference.span.kind", "LLM")   # or TOOL / CHAIN
           span.set_attribute("input.value", state["question"])
           result = ...
           span.set_attribute("output.value", result)
       return {**state, "my_field": result}
   ```

2. **Add a field to `AnalysisState`** if the node writes new data.

3. **Register the node** in the graph builder:
   ```python
   _builder.add_node("my_node", my_node)
   ```

4. **Wire edges** — either a fixed edge or a conditional edge with a routing function:
   ```python
   _builder.add_edge("previous_node", "my_node")           # unconditional
   _builder.add_conditional_edges("my_node", _route_my_node)  # conditional
   ```

5. **Add a span kind** that matches the node's role (LLM call → `LLM`, DB/external call → `TOOL`, pure logic → `CHAIN`).

---

## Known Behaviors

- **Supervisor denial routing is active.** If the LLM does not reply with a "YES"-containing string, the graph exits immediately with a denial message. Off-topic questions never reach `schema_agent`.
- **Two independent retry budgets.** `retry_count_sql` (SQL generation failures) and `retry_count_analysis` (analysis quality failures) are tracked separately — each caps at 3. A question can fail SQL twice and analysis twice and still succeed, because the counters don't share a budget.
- **`_rows_to_text` caps at 50 rows.** The analytics LLM prompt only sees the first 50 rows even if `execute_sql` returned up to 500. The full result set is stored in `query_results` and displayed in the UI expander.
- **Validation failure re-routes to `analytics_agent`, not `clarification_agent`.** When `validation_node` rejects an analysis, it sends the feedback via `validation_error` back to `analytics_agent` (not to `clarification_agent`). The SQL is not re-run — only the interpretation step is retried.
- **`emptyDir` postgres.** If the postgres pod restarts, `schema_agent` will return an empty schema (no tables), and `sql_planner` will generate a query against tables that don't exist, which `sql_validator` will immediately reject.
