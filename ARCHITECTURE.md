# Architecture

## Context and Goals

This project is a personal Zerodha F&O trading analytics platform built in iterative learning phases. Each phase adds a production-grade concern: containerization, CI/CD, GitOps, observability, data ingestion, and finally an agentic analysis layer.

The primary goals are:
1. A usable chatbot (general LLM chat + F&O-specific analytics over uploaded trade data)
2. A fully automated deploy pipeline from `git push` to running pod
3. Observability into every LLM call

---

## Component Map

```
┌─────────────────────────────────────────────────────────────────┐
│  Windows Host                                                   │
│                                                                 │
│   Ollama (port 11434)        Docker Desktop                     │
│   └── LLM models               └── Kubernetes cluster          │
│                                     (namespace: default)        │
│                                                                 │
│                                     ┌─────────────────────┐    │
│                                     │ chatbot pod (8501)   │    │
│                                     │  Streamlit app       │    │
│                                     │  LangGraph agents    │    │
│   ◄──── http.docker.internal ───────┤                      │    │
│                                     └──────┬───────────────┘    │
│                                            │                    │
│                                     ┌──────▼───────────────┐    │
│                                     │ postgres pod (5432)   │    │
│                                     │  F&O trading data    │    │
│                                     └──────────────────────┘    │
│                                                                 │
│                                     ┌────────────────────── ┐   │
│                                     │ phoenix pod (6006)    │   │
│                                     │  LLM trace collector  │   │
│                                     └──────────────────────┘    │
└─────────────────────────────────────────────────────────────────┘
```

---

## Data Flows

### 1. General Chat

```
User types message
  → app.py chat_input
  → ollama.Client.chat(stream=True)      # direct SDK, not LangChain
  → token-by-token via st.write_stream
  → response stored in session_state.messages
  → generate_suggestions() called (non-streaming, same client)
  → 3 follow-up questions in sidebar
```

The `ollama.Client` is created once at module load and shared for both `stream_response` and `generate_suggestions`. Session history (`st.session_state.messages`) is passed as the full message list on every call — there is no server-side memory.

### 2. F&O Analytics (LangGraph Pipeline)

```
User question (pages/analytics.py)
  → graph.invoke(initial_state)
  │
  ├─ supervisor        LLM call — classifies question (routing currently disabled)
  ├─ schema_agent      DB call  — queries information_schema for live column definitions
  │                              + annotates each table with a business description
  ├─ sql_planner       LLM call — generates PostgreSQL SELECT from schema + question
  ├─ sql_validator     DB call  — runs EXPLAIN on the generated SQL (read-only safety check)
  │
  ├── [valid] ──────► execute_sql      DB call — runs the SELECT, returns rows as list[dict]
  │                       │
  │          [data] ──► analytics_agent  LLM call — interprets rows, writes markdown answer
  │                       │
  │                    validation_node   checks answer has content + numbers
  │                       │
  │          [pass] ──► response_formatter → final_response
  │
  └── [invalid/empty] ──► clarification_agent  LLM call — rewrites SQL with error context
                              └── retries sql_validator → execute_sql (max 3 retries)
                              └── [retry exhausted] → response_formatter (failure message)
```

`state['schema_context']` built by `schema_agent` is passed verbatim into `sql_planner` and `clarification_agent` prompts — it is the single source of truth about the DB for all LLM nodes.

### 3. Excel Ingestion

```
User uploads .xlsx (pages/upload.py)
  → filename routed by substring:
      "pnl"      → parse_pnl_excel()       → insert_pl() + insert_charges()
      "position" → parse_positions_excel() → insert_positions()
      "trade"    → parse_tradebook_excel() → insert_trades()
  → psycopg2 execute_values with ON CONFLICT DO NOTHING
  → returns (inserted, skipped) counts
```

All parsers expect sheet name `F&O`. Tradebook rows are aggregated by `(symbol, trade_date)` before insert: quantity summed, price averaged, `order_execution_time` takes the max.

### 4. CI/CD Pipeline

```
git push → main
  → GitHub Actions (ci.yml)
      validate job:
        pip install → ruff check → helm lint → pytest
      build-and-push job (only on push, skips bot commits):
        docker build → docker push (latest + SHA tag)
        sed image.tag in helm/chatbot/values.yaml
        git commit "[skip ci]" → git push
  → ArgoCD detects values.yaml change on main (polls ~3 min)
  → ArgoCD syncs: helm upgrade chatbot helm/chatbot
  → kubectl rolling update of chatbot pod
```

The auto-commit back to `main` is why recent git history shows `ci: update image tag` commits from `github-actions[bot]`. They carry `[skip ci]` to prevent an infinite loop.

### 5. Observability

```
PHOENIX_ENDPOINT set?
  YES:
    app startup → register(endpoint, project) → OllamaInstrumentor().instrument()
    each LLM call → automatic OTLP span → Phoenix pod (http://phoenix:6006/v1/traces)

  Manual spans in app.py:
    chat_turn (AGENT)
      ├─ stream_response (CHAIN)
      └─ generate_suggestions (CHAIN)

  Manual spans in agents/graph.py (one per node):
    fo_analysis (AGENT) — set in pages/analytics.py
      ├─ supervisor (LLM)
      ├─ schema_agent (TOOL)
      ├─ sql_planner (LLM)
      ├─ sql_validator (TOOL)
      ├─ execute_sql (TOOL)
      ├─ clarification_agent (LLM)
      ├─ analytics_agent (LLM)
      ├─ validation_node (CHAIN)
      └─ response_formatter (CHAIN)
```

---

## Key Decisions and Trade-offs

### Ollama runs on the Windows host, not in a pod

**Decision:** Ollama is a host process; pods reach it via `host.docker.internal:11434`.

**Why:** Model weights (multi-GB) live on the host filesystem and persist across cluster changes. Running Ollama in a pod would require a volume mount for model storage and rebuilding the image whenever models change. GPU access (if used) also requires host-level configuration not supported in Docker Desktop's Kubernetes easily.

**Trade-off:** The app has a hard external dependency that isn't visible in `kubectl get pods`. If Docker Desktop restarts, `host.docker.internal` stops resolving until Docker is back up.

---

### PostgreSQL uses `emptyDir` (no persistent volume)

**Decision:** The postgres deployment mounts `/var/lib/postgresql/data` as `emptyDir`.

**Why:** Simplicity for a local dev environment. Persistent volumes in Docker Desktop require StorageClass configuration and the data survives only as long as the node, anyway.

**Trade-off:** Every postgres pod restart wipes all data. The Upload page provides a "Create Tables in DB" button (`ensure_schema()`) as the recovery path — tables must be recreated and data re-uploaded after any restart. This is the most operationally painful known limitation.

---

### Two separate LLM clients (ollama SDK vs. LangChain-Ollama)

**Decision:** `app.py` uses `ollama.Client` directly. `agents/graph.py` uses `ChatOllama` from LangChain.

**Why:** The chat page was built first using the native SDK for simplicity. The analytics agent pipeline was built later using LangGraph, which integrates cleanly with LangChain's model abstractions (`ChatOllama.invoke()` returns a structured message object vs. a raw dict).

**Trade-off:** Two different client codepaths that must both be pointed at the same `OLLAMA_HOST` and `MODEL_NAME`. The LangChain client is initialized lazily with `lru_cache` so env vars are read at first call — consistent with how the Streamlit page sets env vars at startup.

---

### Schema context is built dynamically from `information_schema`

**Decision:** `schema_agent` queries PostgreSQL `information_schema.columns` at runtime to build the schema string passed to LLM nodes, rather than using a hardcoded string.

**Why:** A static schema string requires manual synchronisation with the actual DB. The live query ensures `sql_planner` always sees the real column names and types, even if the schema changes.

**Trade-off:** `schema_agent` makes a DB roundtrip on every analytics query. If PostgreSQL is unreachable, `schema_agent` falls back to a static `_TABLE_DESCRIPTIONS` dict (no column detail). The fallback degrades SQL quality but does not crash the pipeline.

---

### ArgoCD with `selfHeal: true` and `prune: true`

**Decision:** ArgoCD is configured to automatically revert any manual `kubectl` changes and remove resources deleted from Git.

**Why:** GitOps — `helm/chatbot/values.yaml` in `main` is the single source of truth for the running state. Manual `kubectl apply` changes would create invisible drift.

**Trade-off:** You cannot make a one-off emergency change with `kubectl` — it will be reverted within the next sync cycle. For emergencies, commit to `main` or temporarily pause ArgoCD auto-sync (`argocd app patch chatbot --patch '{"spec":{"syncPolicy":null}}'`).

---

### Supervisor routing is currently disabled

**Decision:** The `supervisor` node always routes to `schema_agent` regardless of its YES/NO LLM output. The denial path is commented out.

**Why:** Small local models (e.g., `gemma4:e2b`, `qwen3:1.7b`) are overly conservative and frequently answer NO to valid F&O questions, blocking legitimate queries. The supervisor still runs so its classification appears in Phoenix traces.

**Trade-off:** Off-topic questions (e.g., "write me a poem") will run the full pipeline and get a generic failure response instead of a clean upfront denial.

---

## Integration Points

| From | To | Protocol | Notes |
|---|---|---|---|
| chatbot pod | Ollama host | HTTP REST | `http://host.docker.internal:11434` |
| chatbot pod | postgres pod | TCP/psycopg2 | `postgres:5432` (ClusterIP DNS) |
| chatbot pod | phoenix pod | OTLP/HTTP | `http://phoenix:6006/v1/traces` |
| GitHub Actions | Docker Hub | HTTPS | push on every `main` commit |
| ArgoCD | GitHub | HTTPS (poll) | watches `helm/chatbot/` on `main` |
| ArgoCD | Kubernetes API | in-cluster | `https://kubernetes.default.svc` |

---

## Source Layout

```
app.py                  — Streamlit chat page (general LLM chat)
pages/
  upload.py             — Excel ingestion page
  analytics.py          — F&O analytics page (invokes LangGraph)
agents/
  graph.py              — LangGraph StateGraph (supervisor → … → response_formatter)
ingestion/
  parser.py             — Excel parsers for 3 Zerodha report formats
  db.py                 — psycopg2 connection, schema creation, insert functions
helm/chatbot/           — Helm chart (chatbot + postgres + phoenix deployments)
argocd/application.yaml — ArgoCD Application manifest
.github/workflows/ci.yml — GitHub Actions: lint + test + build + push + auto-tag
```
