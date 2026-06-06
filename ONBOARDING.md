# Developer Onboarding Guide

Welcome to the chatbot-simple repo. This guide gets you from a fresh clone to a fully running app in one sitting.

---

## What You're Setting Up

Three things run together to make the app work:

| Thing | Where it runs | Your job |
|---|---|---|
| Streamlit app | Your machine (dev) or Kubernetes (prod) | `streamlit run app.py` or `helm.exe install` |
| Ollama (LLM server) | Your Windows machine — always | `ollama serve` + pull a model |
| PostgreSQL | Docker container (dev) or Kubernetes (prod) | `docker run postgres` or included in Helm chart |

The app has three pages: a general-purpose **Chat**, an **Upload** page for Zerodha Excel exports, and an **Analytics** page that queries those uploads using natural language.

---

## Prerequisites

Install these before anything else:

| Tool | Version | Notes |
|---|---|---|
| Python | 3.11+ | |
| Git | any | |
| Docker Desktop | any | Enable Kubernetes in Settings if you plan to deploy |
| Ollama | latest | [ollama.com](https://ollama.com) — installs as a Windows service |
| `helm.exe` | 3.x | Download the Windows binary; rename to `helm.exe` to avoid conflicts |
| `kubectl` | any | Comes with Docker Desktop |

---

## Local Development Setup

### 1. Clone and create a virtual environment

```powershell
git clone <repo-url>
cd chatbot_simple

python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 2. Pull an Ollama model

The app defaults to `gemma4:e2b` locally. Pull it before starting the app — Ollama must be running first.

```powershell
ollama serve           # start the Ollama server (keep this terminal open)
ollama pull gemma4:e2b # ~5 GB download, do this once
```

To use a different model, set `MODEL_NAME` in a `.env` file (see Configuration below).

### 3. Start the app

```powershell
# In a new terminal, with .venv activated:
streamlit run app.py
```

Open http://localhost:8501. Type a message in the chat box. If you see "Ollama is not running", make sure `ollama serve` is running in another terminal.

**That's it for the Chat page.** The Upload and Analytics pages additionally need PostgreSQL (next step).

---

## Setting Up PostgreSQL (for Upload and Analytics Pages)

```powershell
docker run -d --name pg \
  -e POSTGRES_DB=chatbot \
  -e POSTGRES_USER=chatbot \
  -e POSTGRES_PASSWORD=chatbot123 \
  -p 5432:5432 \
  postgres:16-alpine
```

Then in the app:
1. Go to the **Upload** page
2. Click **"Create Tables in DB"** — this creates the four tables (`daily_positions`, `daily_pl`, `daily_trades`, `daily_charges`)
3. Upload your Zerodha F&O Excel files

The Analytics page will now be able to query that data.

---

## Configuration

Create a `.env` file in the project root to override defaults:

```env
OLLAMA_HOST=http://localhost:11434   # default; change if Ollama is on another machine
MODEL_NAME=gemma4:e2b               # model used for chat, suggestions, and agent nodes

# PostgreSQL (only needed for Upload and Analytics pages)
PG_HOST=localhost
PG_PORT=5432
PG_DB=chatbot
PG_USER=chatbot
PG_PASSWORD=chatbot123

# Phoenix observability (optional — leave empty to disable tracing)
PHOENIX_ENDPOINT=
PHOENIX_PROJECT=chatbot
```

The app loads `.env` automatically via `python-dotenv`.

---

## Project Layout

```
app.py              Chat page — Ollama streaming, follow-up suggestions
pages/
  upload.py         Excel upload → PostgreSQL ingestion
  analytics.py      Natural-language F&O analytics (calls LangGraph)
agents/
  graph.py          LangGraph pipeline: supervisor → SQL planner → executor → analyzer
ingestion/
  parser.py         Excel parsers for Zerodha positions, P&L, and tradebook formats
  db.py             psycopg2 connection, table creation, insert functions
helm/chatbot/       Kubernetes deployment (Helm chart)
argocd/             ArgoCD GitOps manifest
.github/workflows/  CI: lint + test + Docker build + push + auto-tag
tests/              pytest suite for chat utilities and ingestion parsers
```

---

## Running Tests

```powershell
.venv\Scripts\Activate.ps1

# All tests
pytest tests/ -v

# Just the chat tests (no fixtures needed)
pytest tests/test_chat.py -v

# Just the ingestion tests (requires real Excel fixture files — see below)
pytest tests/test_ingestion.py -v
```

The ingestion tests need three real Excel files that are not committed to the repo:

| Expected path | File |
|---|---|
| `raw_data_files/daily_poistions/positions.xlsx` | Zerodha positions export (note: "poistions" typo in directory name is intentional — don't rename it) |
| `raw_data_files/daily_pl/pnl.xlsx` | Zerodha P&L export |
| `raw_data_files/trade_book/tradebook.xlsx` | Zerodha tradebook export |

Without these files, `test_ingestion.py` will fail to open them. The chat tests (`test_chat.py`) have no external dependencies and always pass.

---

## Linting

```powershell
ruff check app.py pages/ ingestion/
```

CI runs the same command (note: `agents/` is intentionally excluded from CI lint). Fix all warnings before pushing.

---

## Kubernetes Deployment (Optional)

You need Docker Desktop with Kubernetes enabled.

### One-time setup
```powershell
# Verify cluster is up
kubectl cluster-info

# Verify helm is working (use helm.exe, not helm — name conflict on Windows)
helm.exe version
```

### Deploy
```powershell
helm.exe lint helm/chatbot
helm.exe install chatbot helm/chatbot

# Watch pods come up
kubectl get pods -w

# App is at http://localhost:8501 once chatbot pod is Running
```

The Helm chart deploys all three components (chatbot, postgres, phoenix) together. PostgreSQL data does not persist across pod restarts — recreate tables via the Upload page after any restart.

For the in-cluster model, the chart defaults to a different model than local dev:

```yaml
# helm/chatbot/values.yaml
ollama:
  model: qwen3:1.7b   # pull this on your host before deploying
```

```powershell
ollama pull qwen3:1.7b
```

---

## How CI/CD Works

Every push to `main`:
1. GitHub Actions runs lint, helm lint, and pytest
2. If all pass, builds and pushes a Docker image to Docker Hub tagged with the commit SHA
3. Updates `helm/chatbot/values.yaml` with the new SHA tag and commits back to `main` with `[skip ci]`
4. ArgoCD (if running) detects the `values.yaml` change and upgrades the Helm release automatically

You don't need to manually build or push Docker images — just push code to `main`.

---

## Observability (Optional)

Phoenix is included in the Helm chart and is enabled by default (`phoenix.enabled: true` in `values.yaml`). To view traces:

```powershell
kubectl port-forward svc/phoenix 6006:6006
# Open http://localhost:6006
```

Every LLM call (chat messages, analytics pipeline nodes) appears as a trace. This is useful for debugging why the analytics agent generated wrong SQL or why validation failed.

For local dev, Phoenix is disabled by default (no `PHOENIX_ENDPOINT` set in `.env`).

---

## Common First-Day Issues

| Symptom | Fix |
|---|---|
| "Ollama is not running" in chat | Run `ollama serve` in a separate terminal |
| "Model not found" in chat | Run `ollama pull gemma4:e2b` (or whichever model is set) |
| Upload page — "Create Tables in DB" fails | Start the postgres container first (`docker run ...` above) |
| Analytics page returns no results | Tables exist but are empty — upload Excel files first |
| `helm.exe` not found | Download the Windows binary from helm.sh, name it `helm.exe`, add to PATH |
| `ruff check` fails in CI but not locally | Activate the venv (`ruff` must come from `.venv`, not a global install) |
| Ingestion tests fail with FileNotFoundError | Place real Zerodha Excel exports in the `raw_data_files/` paths listed above |

---

## Key Files to Read Next

| File | What to read it for |
|---|---|
| `agents/graph.py` | How the F&O analytics agent pipeline works |
| `ingestion/parser.py` | How Excel files are parsed (hardcoded cell positions) |
| `ARCHITECTURE.md` | Component map, data flows, and key design decisions |
| `RUNBOOK.md` | How to deploy, upgrade, rollback, and troubleshoot |
| `CLAUDE.md` | Instructions for Claude Code when working in this repo |
