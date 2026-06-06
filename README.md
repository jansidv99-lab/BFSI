# Chatbot Simple

A Streamlit chat UI backed by a locally-running LLM (Ollama), fully containerized, deployed to Kubernetes via Helm, with GitOps automation through ArgoCD, a CI pipeline on GitHub Actions, LLM observability via Arize Phoenix, and a data ingestion pipeline for Zerodha F&O trading data.

---

## Quick Start (local, < 5 minutes)

**Prerequisites:** Python 3.11+, [Ollama](https://ollama.com) installed and running.

```powershell
# 1. Pull a model
ollama pull gemma4:e2b

# 2. Clone and set up
git clone https://github.com/jansidv99-lab/chatbot_simple.git
cd chatbot_simple
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# 3. Run
streamlit run app.py
```

Open `http://localhost:8501`. Start chatting.

---

## Features

- **Streaming responses** — tokens appear as they're generated
- **Conversation history** — full multi-turn context sent to the model each time
- **Follow-up suggestions** — 3 questions automatically suggested after each reply
- **Clear conversation** — sidebar button resets the session
- **Graceful errors** — clear messages if Ollama isn't running or the model isn't pulled
- **LLM observability** — every chat turn traced in Arize Phoenix (spans for streaming + suggestions)
- **Data ingestion** — upload Zerodha F&O Excel files to PostgreSQL from the browser

---

## Configuration

All config is via environment variables:

| Variable | Default | Description |
|---|---|---|
| `OLLAMA_HOST` | `http://localhost:11434` | URL of the Ollama server |
| `MODEL_NAME` | `gemma4:e2b` | Model to use for chat and suggestions |
| `PHOENIX_ENDPOINT` | _(empty — tracing off)_ | OTLP endpoint, e.g. `http://phoenix:6006/v1/traces` |
| `PHOENIX_PROJECT` | `chatbot` | Phoenix project name for grouping traces |
| `PG_HOST` | `localhost` | PostgreSQL host (`postgres` in-cluster) |
| `PG_PORT` | `5432` | PostgreSQL port |
| `PG_DB` | `chatbot` | PostgreSQL database name |
| `PG_USER` | `chatbot` | PostgreSQL user |
| `PG_PASSWORD` | `chatbot123` | PostgreSQL password (dev-only) |

---

## Stack

| Layer | Choice |
|---|---|
| UI | Streamlit (multi-page: `app.py` + `pages/`) |
| LLM serving | Ollama (runs on host, not in cluster) |
| Container | Docker (`python:3.11-slim`) |
| Orchestration | Kubernetes — Docker Desktop local cluster |
| Helm chart | `helm/chatbot/` |
| GitOps | ArgoCD (`argocd/application.yaml`) |
| CI/CD | GitHub Actions → Docker Hub |
| Image registry | Docker Hub (`vamsidv2010/chatbot-simple`) |
| Observability | Arize Phoenix — OpenTelemetry + OpenInference |
| Database | PostgreSQL 16 |

---

## Running with Docker

```powershell
docker build -t chatbot-simple .
docker run -p 8501:8501 -e OLLAMA_HOST=http://host.docker.internal:11434 chatbot-simple
```

To also enable PostgreSQL locally:

```powershell
docker run -d --name pg \
  -e POSTGRES_DB=chatbot -e POSTGRES_USER=chatbot -e POSTGRES_PASSWORD=chatbot123 \
  -p 5432:5432 postgres:16-alpine
```

---

## Kubernetes Deployment (Helm)

> Requires Docker Desktop with Kubernetes enabled and `helm.exe` on PATH.

```powershell
# First deploy
helm.exe install chatbot helm/chatbot

# Upgrade after changes
helm.exe upgrade chatbot helm/chatbot

# Remove
helm.exe uninstall chatbot
```

App at `http://localhost:8501`. Ollama must be running on the Windows host (`ollama serve`).

**Key toggles in `helm/chatbot/values.yaml`:**

```yaml
ollama:
  host: http://host.docker.internal:11434
  model: qwen3:1.7b

phoenix:
  enabled: true   # set false to disable tracing

postgres:
  enabled: true   # set false to disable the DB pod
```

**Verify all 3 pods are Running:**

```powershell
kubectl get pods
# chatbot-chatbot-<hash>    1/1   Running
# chatbot-phoenix-<hash>    1/1   Running
# chatbot-postgres-<hash>   1/1   Running
```

---

## LLM Observability (Arize Phoenix)

Phoenix collects an OpenTelemetry trace for every chat turn — one outer `AGENT` span covering the full turn, with two nested `CHAIN` spans for the streaming call and the follow-up suggestion generation.

```powershell
# Port-forward the Phoenix UI
kubectl port-forward svc/phoenix 6006:6006
# UI at http://localhost:6006
```

Tracing is only active when `PHOENIX_ENDPOINT` is set. In-cluster it is automatically set to `http://phoenix:6006/v1/traces` by the Helm chart when `phoenix.enabled: true`.

---

## Data Ingestion (Upload Page)

Navigate to **Upload Data** (`http://localhost:8501/upload`) to load Zerodha F&O Excel files into PostgreSQL.

### Tables

| Table | Source file | Key |
|---|---|---|
| `daily_positions` | `positions*.xlsx` (sheet `F&O`, header row 15) | `trade_date + symbol` |
| `daily_pl` | `pnl*.xlsx` (sheet `F&O`, data from row 39) | `trade_date + symbol` |
| `daily_charges` | `pnl*.xlsx` (sheet `F&O`, charges rows 24–33) | `date` |
| `daily_trades` | `tradebook*.xlsx` (sheet `F&O`, header row 15) | `trade_date + symbol` |

`daily_trades` aggregates multiple raw trade rows per symbol+date: first `trade_type`, summed `quantity`, averaged `price`, max `order_execution_time`.

Duplicate rows are silently skipped (`ON CONFLICT DO NOTHING`).

### Usage

1. Click **Create Tables in DB** to create all 4 tables (safe to run repeatedly)
2. **Bulk Upload from Folder** — select multiple `.xlsx` files at once; each is routed automatically by filename (`positions` / `pnl` / `trade`)
3. Or use the individual section uploaders below for per-table control

---

## GitOps with ArgoCD

ArgoCD watches `helm/chatbot/` on `main` and auto-syncs on every change.

### One-time install

```powershell
kubectl create namespace argocd
kubectl apply -n argocd -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml
kubectl wait --for=condition=Ready pods --all -n argocd --timeout=300s

# Uninstall existing Helm release first (ArgoCD takes ownership)
helm.exe uninstall chatbot

# Deploy the ArgoCD Application
kubectl apply -f argocd/application.yaml
```

### Access the UI

```powershell
# Run in a dedicated terminal
kubectl port-forward svc/argocd-server -n argocd 8080:443
```

UI at `https://localhost:8080`. Get the initial admin password:

```powershell
$b64 = kubectl get secret argocd-initial-admin-secret -n argocd -o jsonpath="{.data.password}"
[System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String($b64))
```

### Day-to-day

```powershell
argocd.exe login localhost:8080 --username admin --insecure
argocd.exe app get chatbot       # sync + health status
argocd.exe app sync chatbot      # force sync without waiting for poll
argocd.exe app diff chatbot      # diff: Git vs cluster
```

### GitOps deploy flow

```
git push → GitHub Actions builds image → stamps image.tag SHA in values.yaml
         → commits [skip ci] → ArgoCD polls Git (every ~3 min)
         → detects values.yaml change → helm upgrade → new pod starts
```

---

## CI Pipeline

Defined in `.github/workflows/ci.yml`. Two jobs:

| Job | Trigger | Steps |
|---|---|---|
| `validate` | push + PR to `main` | install deps → `ruff` lint → `helm lint` → `pytest` |
| `build-and-push` | push to `main` only | docker build + push → update `image.tag` in `values.yaml` → commit `[skip ci]` |

**Required GitHub secrets:**

| Secret | Purpose |
|---|---|
| `DOCKERHUB_USERNAME` | Docker Hub login |
| `DOCKERHUB_TOKEN` | Docker Hub access token (not password) |

---

## Tests

```powershell
.venv\Scripts\Activate.ps1
pytest tests/ -v
```

| Test file | Covers |
|---|---|
| `tests/test_chat.py` | `stream_response()` and `generate_suggestions()` — Ollama mocked |
| `tests/test_graph.py` | `_check_analysis()` validation heuristics — no external deps |
| `tests/test_ingestion.py` | Parser validation, Excel parsing, DB insert logic — requires real fixture files |

---

## Project Structure

```
app.py                        # main chat page (streaming, suggestions, tracing)
pages/
  upload.py                   # data ingestion UI (bulk upload + per-table sections)
  analytics.py                # F&O analysis UI — calls LangGraph, word-stream rendering
agents/
  graph.py                    # LangGraph pipeline: supervisor → SQL planner → executor → analyzer
ingestion/
  parser.py                   # validate + parse positions, pnl, tradebook Excel files
  db.py                       # schema creation, insert functions, list_tables
utils/
  state.py                    # init_session_state() — shared Streamlit session defaults
requirements.txt
Dockerfile                    # python:3.11-slim
helm/chatbot/
  values.yaml                 # image tag, ollama, phoenix, postgres config
  templates/
    deployment.yaml           # chatbot pod + env vars
    service.yaml              # LoadBalancer on 8501
    postgres-deployment.yaml  # PostgreSQL pod (emptyDir volume)
    postgres-service.yaml     # ClusterIP named 'postgres' on 5432
    phoenix-deployment.yaml   # Arize Phoenix pod
    phoenix-service.yaml      # ClusterIP on 6006 (UI) + 4317 (OTLP)
argocd/
  application.yaml            # ArgoCD Application CR
.github/workflows/ci.yml      # GitHub Actions CI
tests/
  test_chat.py
  test_ingestion.py
  test_graph.py               # unit tests for _check_analysis validation logic
raw_data_files/               # sample Zerodha Excel files (gitignored in production)
  daily_poistions/positions.xlsx
  daily_pl/pnl.xlsx
  trade_book/tradebook.xlsx
.agents/plans/                # per-module implementation plans
```

---

## Module History

| Module | What was built |
|---|---|
| 1 | Streamlit chat UI with streaming and conversation history |
| 2 | Dockerfile + GitHub Actions CI (build + push to Docker Hub) |
| 3 | Helm chart, Kubernetes deployment, LoadBalancer service |
| 4 | ArgoCD GitOps — auto-deploy on every push to `main` |
| 5 | CI validate job: ruff lint + helm lint + pytest gating builds |
| 6 | Auto follow-up question suggestions after each response |
| 7 | LLM observability: Arize Phoenix in-cluster, OpenTelemetry spans |
| 8 | Data ingestion: Excel upload → PostgreSQL (4 tables, bulk auto-routing) |
