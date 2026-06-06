# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A Streamlit chatbot UI connected to a locally-served LLM (Ollama) via streaming REST, containerized with Docker, orchestrated with Kubernetes (local cluster via Docker Desktop), deployed via Helm, and automated through GitHub Actions CI/CD.

## Tech Stack

| Layer | Choice |
|---|---|
| Frontend UI | Streamlit (multi-page: `app.py` + `pages/`) |
| LLM Model | `qwen3:1.7b` (default in Helm `values.yaml`); `gemma4:e2b` (default local) |
| LLM Serving | Ollama (runs on Windows host, NOT in the cluster) |
| Agent Framework | LangGraph (`agents/graph.py`) + LangChain-Ollama |
| Containerization | Docker |
| Container Orchestration | Kubernetes (Docker Desktop local cluster) |
| Kubernetes Package Management | Helm |
| CI/CD | GitHub Actions → Docker Hub |
| Image Registry | Docker Hub (`vamsidv2010/chatbot-simple`) |
| Observability | Arize Phoenix (in-cluster) — OpenTelemetry + OpenInference conventions |
| Database | PostgreSQL |
| OS | Windows — use `helm.exe` not `helm` (name conflict) |

## Architecture

### FastAPI Backend (`api/`)

Streamlit pages communicate with a FastAPI service (`api/main.py`) rather than calling LangGraph or Ollama directly. This gives the app a proper HTTP API contract with JWT middleware enforcement and structured error handling.

```
Streamlit pages  →  FastAPI (http://localhost:8000)  →  LangGraph / Ollama / PostgreSQL
```

Endpoints:
| Method | Path | Auth | Purpose |
|---|---|---|---|
| `POST` | `/auth/register` | No | Create a new user account |
| `POST` | `/auth/login` | No (OAuth2 form) | Return `access_token` + `refresh_token` |
| `POST` | `/chat/` | Bearer JWT | Stream chat tokens as SSE (`text/event-stream`) |
| `POST` | `/analytics/` | Bearer JWT | Run LangGraph pipeline, return JSON result |
| `GET` | `/health` | No | Liveness check |
| `GET` | `/docs` | No | Swagger UI |

Key design choices:
- Route handlers use `def` (not `async def`) so LangGraph and psycopg2 (both sync) run in Uvicorn's thread pool without blocking the event loop.
- `api/deps.py` provides `get_current_user()` — a FastAPI `Depends` that decodes the Bearer token and raises `401` on failure.
- `api/routers/analytics.py` calls `graph.invoke()` (full run) and JSON-serialises the result, normalising non-serialisable types (dates, etc.) via a `json.dumps(default=str)` round-trip.
- `api/routers/chat.py` returns a `StreamingResponse` with `media_type="text/event-stream"`; each Ollama token is emitted as `data: <token>\n\n`. Streamlit consumes this with `httpx.Client().stream()`, stripping the `data: ` prefix before yielding to `st.write_stream`.

### Streamlit Multi-Page App

Four pages auto-discovered by Streamlit (plus `pages/login.py` for auth):
- `app.py` — main chat page; streams responses token-by-token via `st.write_stream` by calling `POST /chat/` on the FastAPI service; calls `generate_suggestions` after each reply by making a second streaming call; stores history in `st.session_state.messages`
- `pages/login.py` — login / registration page; calls `auth.users` and `auth.tokens` directly (not through FastAPI); sets `auth_access_token`, `auth_refresh_token`, `auth_user` in session state
- `pages/upload.py` — Excel ingestion page; calls `ingestion.db` / `ingestion.parser` directly (not through FastAPI); validates → shows row count + date range preview → user clicks "Insert into DB" → inserts into PostgreSQL
- `pages/analytics.py` — F&O analysis page; calls `POST /analytics/` on FastAPI; shows a spinner while waiting; renders the response with `_word_stream` (word-reveal at 25 ms/word) if results exist, `st.markdown` otherwise; SQL and raw results shown in expanders

`utils/state.py` defines `init_session_state()` — called at the top of every page to ensure all `st.session_state` keys (`messages`, `suggestions`, `analysis_history`) survive Streamlit page navigation without resetting.

Ollama always runs on the **Windows host**, never inside Kubernetes:
- Local dev: `http://localhost:11434` (default)
- In-cluster: `http://host.docker.internal:11434` (set in `helm/chatbot/values.yaml`)

### LangGraph Agent Pipeline (`agents/graph.py`)

`pages/analytics.py` → `agents/graph.py` → PostgreSQL

A LangGraph `StateGraph` with `AnalysisState` TypedDict drives a multi-step F&O data analysis pipeline:

```
supervisor ──────────► schema_agent ──► sql_planner ──► sql_validator
                                                              │
                                                   (valid)──► execute_sql
                                                   (invalid)─► clarification_agent ─► (retry ≤3)
                                                                                          │
                                               analytics_agent ◄──(data found)────────────┘
                                                      │
                                               validation_node
                                                      │
                                            response_formatter ──► END
```

Key behaviors:
- `supervisor` asks the LLM whether the question is answerable from Zerodha F&O tables; if the response does not contain "YES", `final_response` is set immediately and `_route_supervisor` routes to `END`, short-circuiting the rest of the pipeline
- `schema_agent` fetches live column schema via `get_table_schemas`; falls back to static `_TABLE_DESCRIPTIONS` strings in `graph.py` if the DB is unreachable
- `sql_planner` adds `LIMIT 500` for row-level queries; aggregations (GROUP BY) may omit it; SQL is extracted from fenced code blocks via regex
- `sql_validator` runs `EXPLAIN` (read-only) against PostgreSQL to catch syntax errors; only SELECT queries are permitted
- `clarification_agent` retries up to 3 times on invalid SQL or empty results
- `analytics_agent` receives at most 50 rows of query results (truncated by `_rows_to_text` in `graph.py`) to keep LLM context bounded
- `validation_node` rejects analyses shorter than 30 chars (`_MIN_CHARS`), lacking numbers, or matching refusal phrases (`_REFUSAL_RE`); sends back to `clarification_agent`
- The LLM client (`ChatOllama`) is lazily initialized with `lru_cache` — env vars are read at first call, not at import

### Authentication (`auth/`)

All Streamlit pages call `auth.session.require_auth()` at the top, which redirects unauthenticated users to `pages/login.py`.

| Module | Purpose |
|---|---|
| `auth/passwords.py` | `hash_password` / `verify_password` via bcrypt; includes timing-attack protection with `_DUMMY_HASH` |
| `auth/tokens.py` | `create_access_token` (30 min, HS256) / `create_refresh_token` (7 days) / `decode_token`; reads `JWT_SECRET_KEY` at call time |
| `auth/users.py` | `ensure_users_table`, `create_user`, `authenticate_user`, `get_user_by_id` — all using psycopg2 |
| `auth/session.py` | `login_user` / `logout_user` / `get_current_user` / `require_auth` — manage `auth_access_token`, `auth_refresh_token`, `auth_user` in `st.session_state`; auto-refreshes expired access tokens using the refresh token |

`utils/state.py`'s `init_session_state()` initialises all auth keys to `None` so they survive Streamlit page navigation.

### Ingestion Pipeline

`pages/upload.py` → `ingestion/parser.py` → `ingestion/db.py`

Three parsers handle three Zerodha F&O Excel report types, all expecting sheet `F&O`:

| Parser function | Validator | Target table | Key |
|---|---|---|---|
| `parse_positions_excel` | `validate_file` | `daily_positions` | `(trade_date, symbol)` |
| `parse_pnl_excel` | `validate_pnl_file` | `daily_pl` + `daily_charges` | `(trade_date, symbol)` / `date` |
| `parse_tradebook_excel` | `validate_tradebook_file` | `daily_trades` | `(trade_date, symbol)` |

Tradebook rows are aggregated by `(symbol, trade_date)` before insert: `quantity` is summed, `price` is averaged, `order_execution_time` takes the max. The DB layer uses `psycopg2` with `execute_values` and `ON CONFLICT … DO NOTHING` — all tables silently skip duplicate rows.

**Bulk upload** (`pages/upload.py`) routes by filename substring: `"pnl"` → `daily_pl` + `daily_charges`; `"position"` → `daily_positions`; `"trade"` → `daily_trades`. Files that match none are skipped with a warning.

**Schema creation** is not automatic — the user must click the "Create Tables in DB" button (`ensure_schema()`) before the first upload. `list_tables()` is called on every page load to show which tables exist.

### Observability / Tracing

When `PHOENIX_ENDPOINT` is set, the app instruments itself via `OllamaInstrumentor` and wraps each chat turn in nested OpenTelemetry spans using OpenInference `span.kind` attributes.

`app.py` span tree:
- `chat_turn` (kind=`AGENT`) — outer span covering the full user turn
  - `stream_response` (kind=`CHAIN`) — spans the streaming LLM call
  - `generate_suggestions` (kind=`CHAIN`) — spans the follow-up question generation

`agents/graph.py` span tree (per analytics query):
- `fo_analysis` (kind=`AGENT`) — set in `pages/analytics.py`
  - One span per graph node: `supervisor`, `schema_agent`, `sql_planner`, `sql_validator`, `execute_sql`, `clarification_agent`, `analytics_agent`, `validation_node`, `response_formatter`

Node span kinds follow OpenInference conventions: LLM calls → `LLM`, DB/tool calls → `TOOL`, routing/formatting → `CHAIN`.

In-cluster, Phoenix receives traces at `http://phoenix:6006/v1/traces`. Phoenix is toggled via `values.yaml: phoenix.enabled`.

### Rate Limiting (`api/rate_limit.py`)

Redis-backed rate limiters are applied per-user (keyed by `"{endpoint}:{user_sub}"` from the JWT `sub` claim) on three routes:

| Instance | Route | Limit |
|---|---|---|
| `chat_limiter` | `POST /chat/` | 20 req / 60 s |
| `analytics_limiter` | `POST /analytics/` | 5 req / 60 s |
| `login_limiter` | `POST /auth/login` | 10 req / 60 s |

Two implementations exist: `SlidingWindowRateLimiter` (wired to routes; ZSET-based) and `TokenBucket` (educational only, not wired). Both **fail-open** — a `redis.RedisError` logs a warning and lets the request through rather than blocking it. The TOCTOU non-atomicity is intentional (documented in the class docstring) and acceptable for this scope.

The `/metrics` endpoint is auto-exposed by `prometheus_fastapi_instrumentator` in `api/main.py`. `api/metrics.py` adds three application-level Prometheus counters:
- `chatbot_chat_requests_total` — incremented after rate-limit passes in `/chat/`
- `chatbot_analytics_requests_total` — incremented after rate-limit passes in `/analytics/`
- `chatbot_rate_limit_hits_total{endpoint}` — incremented on every HTTP 429

### CI Auto-Commit Behavior

After a successful Docker push, CI (`ci.yml`) automatically commits back to `main` updating `helm/chatbot/values.yaml` with the new image SHA tag. Commits from `github-actions[bot]` with `[skip ci]` in the message are this auto-update — not human changes.

## Environment Variables

| Variable | Default | Purpose |
|---|---|---|
| `OLLAMA_HOST` | `http://localhost:11434` | Ollama server URL |
| `MODEL_NAME` | `gemma4:e2b` | Model to use for chat, suggestions, and agent nodes |
| `PHOENIX_ENDPOINT` | _(empty — disables tracing)_ | OTLP endpoint for Phoenix, e.g. `http://phoenix:6006/v1/traces` |
| `PHOENIX_PROJECT` | `chatbot` | Phoenix project name for trace grouping |
| `PG_HOST` | `localhost` | PostgreSQL host (`postgres` in-cluster) |
| `PG_PORT` | `5432` | PostgreSQL port |
| `PG_DB` | `chatbot` | PostgreSQL database name |
| `PG_USER` | `chatbot` | PostgreSQL user |
| `PG_PASSWORD` | `chatbot123` | PostgreSQL password (dev-only) |
| `JWT_SECRET_KEY` | _(required — no default)_ | HS256 signing secret; generate with `openssl rand -hex 32` |
| `API_BASE_URL` | `http://localhost:8000` | FastAPI server URL used by Streamlit pages |
| `REDIS_HOST` | `localhost` | Redis server hostname (`redis` in-cluster) |
| `REDIS_PORT` | `6379` | Redis server port |

## Common Commands

### Local development
```powershell
# Terminal 1 — FastAPI backend (required before starting Streamlit)
.venv\Scripts\Activate.ps1
uvicorn api.main:app --reload --port 8000
# Swagger UI at http://localhost:8000/docs

# Terminal 2 — Streamlit frontend
.venv\Scripts\Activate.ps1
streamlit run app.py
# App is at http://localhost:8501
```

`app.py` calls `load_dotenv()` at startup, so a `.env` file in the project root is the easiest way to set env vars for local dev. `JWT_SECRET_KEY` is required — the app will raise a `RuntimeError` on first login/register if it is missing.

### Lint and test
```powershell
.venv\Scripts\Activate.ps1
ruff check app.py pages/ ingestion/   # lint (matches CI — agents/ and api/ are NOT linted in CI)
pytest tests/ -v                       # all tests
pytest tests/test_chat.py::test_yields_tokens -v      # single chat test
pytest tests/test_ingestion.py -v                     # ingestion tests (requires real fixture file)
pytest tests/test_graph.py -v                         # unit tests for _check_analysis validation logic
```

> **Test isolation:**
> - `test_chat.py`, `test_graph.py` — fully isolated; mock all I/O; no real services needed
> - `test_auth.py` — fully isolated; `os.environ.setdefault("JWT_SECRET_KEY", ...)` at module top means the env var is not required when running tests
> - `test_rate_limit.py`, `test_metrics.py` — fully isolated; use `fakeredis` instead of real Redis; no external services needed
> - `test_ingestion.py` — requires three real fixture files (paths hardcoded):
>   - `raw_data_files/daily_poistions/positions.xlsx` — "poistions" is an intentional typo; don't rename
>   - `raw_data_files/daily_pl/pnl.xlsx`
>   - `raw_data_files/trade_book/tradebook.xlsx`

### Local Redis (for rate limiting)
```powershell
docker run -d --name redis -p 6379:6379 redis:7-alpine
```

### Local PostgreSQL (for upload and analytics pages)
```powershell
docker run -d --name pg -e POSTGRES_DB=chatbot -e POSTGRES_USER=chatbot -e POSTGRES_PASSWORD=chatbot123 -p 5432:5432 postgres:16-alpine
```

### Docker
```powershell
docker build -t chatbot-simple .
docker run -p 8501:8501 -e OLLAMA_HOST=http://host.docker.internal:11434 chatbot-simple
```

### Helm / Kubernetes (use helm.exe, not helm)
```powershell
helm.exe lint helm/chatbot                              # Lint
helm.exe install chatbot helm/chatbot --dry-run --debug # Preview manifests
helm.exe install chatbot helm/chatbot                   # Deploy
helm.exe upgrade chatbot helm/chatbot                   # Upgrade after changes
helm.exe upgrade chatbot helm/chatbot --set auth.jwtSecretKey=<secret>  # Override JWT key (required for real deploys — values.yaml ships with a placeholder)
helm.exe uninstall chatbot                              # Remove

kubectl get pods                         # Verify pod is Running
kubectl get svc                          # EXTERNAL-IP should be localhost
kubectl logs deployment/chatbot-chatbot  # Check for errors
# App is at http://localhost:8501
```

### Ollama (must be running before the app starts)
```powershell
ollama serve
ollama pull gemma4:e2b    # for local dev
ollama pull qwen3:1.7b   # for k8s (matches helm/chatbot/values.yaml)
```

### Phoenix Observability
```powershell
# Port-forward UI (run in a dedicated terminal)
kubectl port-forward svc/phoenix 6006:6006
# UI at http://localhost:6006 — shows all LLM traces

# Check Phoenix pod
kubectl get pods -l app=phoenix
kubectl logs deployment/chatbot-phoenix

# Disable Phoenix (edit values.yaml: phoenix.enabled: false, then upgrade)
helm.exe upgrade chatbot helm/chatbot
```

### Prometheus + Grafana (Module 13)
```powershell
# Port-forward (each in a dedicated terminal)
kubectl port-forward svc/prometheus 9090:9090   # UI at http://localhost:9090
kubectl port-forward svc/grafana    3000:3000   # UI at http://localhost:3000  login: admin / admin

# Local smoke test — verify /metrics endpoint (FastAPI must be running)
uvicorn api.main:app --reload --port 8000
# curl http://localhost:8000/metrics  → Prometheus text format
# After a few chat requests: chatbot_chat_requests_total should appear and increment

# Check pods
kubectl get pods -l app=prometheus
kubectl get pods -l app=grafana
```

### ArgoCD (one-time install)
```powershell
kubectl create namespace argocd
kubectl apply -n argocd -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml
kubectl wait --for=condition=Ready pods --all -n argocd --timeout=300s
kubectl apply -f argocd/application.yaml   # deploy the GitOps Application
```

### ArgoCD (day-to-day)
```powershell
# Port-forward UI — run in a dedicated terminal, keep it running
kubectl port-forward svc/argocd-server -n argocd 8080:443
# UI at https://localhost:8080 (accept self-signed cert)

# Get initial admin password
$b64 = kubectl get secret argocd-initial-admin-secret -n argocd -o jsonpath="{.data.password}"
[System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String($b64))

# CLI (download argocd-windows-amd64.exe, put on PATH)
argocd.exe login localhost:8080 --username admin --insecure
argocd.exe app get chatbot      # sync + health status
argocd.exe app list             # all apps at a glance
argocd.exe app sync chatbot     # force sync without waiting for poll
argocd.exe app diff chatbot     # diff: Git vs cluster
```

## Development Phases

Track overall status in `PROGRESS.md`. All 17 modules complete (chat UI → CI → Helm/K8s → ArgoCD → testing → suggestions → Phoenix → ingestion → LangGraph agents → hardening → date-aware analytics → JWT auth → FastAPI backend → rate limiting + Prometheus metrics → semantic response caching → LLM eval framework → A/B model comparison).

## Planning Conventions

- Save all plans to `.agents/plans/` using naming `{sequence}.{plan-name}.md` (e.g., `3.helm-k8s.md`)
- Each plan must include at least one validation test per task
- Mark complexity at the top: ✅ Simple | ⚠️ Medium | 🔴 Complex
- 🔴 Complex plans must be broken into sub-plans before executing
- Custom commands live in `.calude/commands/` (note: directory name is a typo — kept as-is): `/build` executes a plan file, `/onboarding` scans the repo and summarises project state

## Behavioral Guidelines

### Think Before Coding

- State assumptions explicitly; ask if uncertain
- Surface multiple interpretations rather than picking silently
- Name what's confusing and stop; don't guess

### Simplicity First

- Minimum code that solves the problem — nothing speculative
- No abstractions, flexibility, or configurability beyond what was asked
- If you write 200 lines and it could be 50, rewrite it

### Surgical Changes

- Touch only what the request requires; don't improve adjacent code
- Match existing style even if you'd do it differently
- Remove imports/variables made unused by YOUR changes only; leave pre-existing dead code alone

### Goal-Driven Execution

For multi-step tasks, state a brief plan before coding:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
```
Transform vague tasks into verifiable goals before starting.

## Claude Code Skills — When to Use

| Skill | When |
|---|---|
| `engineering:deploy-checklist` | Before every `helm.exe upgrade` |
| `engineering:debug` | LLM unreachable from pod; unexpected errors |
| `engineering:architecture` | ADR decisions (e.g. ArgoCD sync strategy changes) |
| `security-review` | Before pushing CI changes; before ArgoCD RBAC setup |
| `verify` | After any deploy — confirm app loads and chat works |
| `loop` | Polling a long-running deploy or CI run |
| `engineering:standup` | Daily — summarize commits and today's plan |
