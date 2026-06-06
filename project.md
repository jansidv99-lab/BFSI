# chatbot simple

## what we are preparing 

A application with interface:
to provide the chat interface to connect with LLM and chat

## stack

| Layer                         | Choice                             |
| ----------------------------- | ---------------------------------- |
| Frontend UI                   | Streamlit                          |
| LLM Model                     | Gemma4:e2b (7.2 GB)                |
| LLM Serving                   | Ollama local setup not in Kubernetes(local cluster)|
| Containerization              | Docker                             |
| Container Orchestration       | Kubernetes (Local Cluster)         |
| Kubernetes Package Management | Helm                               |
| CI/CD Source Control          | GitHub                             |
| CI/CD Pipeline                | GitHub Actions                     |
| Image Registry                | Docker Hub        |
| Operating System              | windows      |


## Planning
- Save all plans to `.agent/plans/` folder
- Naming convention: `{sequence}.{plan-name}.md` (e.g., `1.auth-setup.md`, `2.document-ingestion.md`)
- Plans should be detailed enough to execute without ambiguity
- Each task in the plan must include at least one validation test to verify it works
- Assess complexity and single-pass feasibility - can an agent realistically complete this in one go?
- Include a complexity indicator at the top of each plan:
  - ✅ **Simple** - Single-pass executable, low risk
  - ⚠️ **Medium** - May need iteration, some complexity
  - 🔴 **Complex** - Break into sub-plans before executing


## Development Flow
1. **Plan** - Create a detailed plan and save it to `.agent/plans/`
2. **Build** - Execute the plan to implement the feature
3. **Validate** - Test and verify the implementation works correctly. Use browser testing where applicable via an appropriate MCP
4. **Iterate** - Fix any issues found during validation  

## Progress
Check PROGRESS.md for current module status. Update it as you complete tasks.

## Phase 1

###  Module 1 - chatbot UI implementaion 


### Module 2 - git hub and CI pipeline


### MOdule 3 - helm charts and kubernetes local server deployment


### Module 4 - ArgoCD for automatic deployment

### Module 5 - CI pipeline improvements


### Module 6 - Suggested Follow-up Questions - significantly increases user interaction

### Module 7 - Observibity using 

## Phase 1 completed

## Phase 2 - Data Ingestion flow

## Module 8 -  user upload the excel files from UI to DB
     1.Basic ingestion (validate → parse → store) , we have multiple tables to be created
     2.client_id details are not required in any table
     3.create these belwo tables
     Table 1 : daily_positions(raw_data_files\daily_poistions\positions.xlsx)
     tradeday + symbol as a key for the daily_positions table
     Table 2 : daily_pl(raw_data_files\daily_pl\pnl.xlsx)
     tradeday + symbol as a key for the daily_pl table
     Table 3 : daily_trades(raw_data_files\trade_book\tradebook.xlsx)
     consider only this columns for table creation - Symbol,Trade Date,Trade Type,Quantity,Price,Order Execution Time
     Data loading logic - take symbol and trade date as key and aggrgate the other fields as follows
                     trade type - get first value
                     Quantity - sum of values
                     price -  average of values
                     Order Execution Time - max of values

     Table 4 : daily_charges(raw_data_files\daily_pl\pnl.xlsx)
     table columns present in pnl.xlsx file:
     date (key)
     Brokerage - Z
     Exchange Transaction Charges - Z
     Clearing Charges - Z
     Central GST - Z
     State GST - Z
     Integrated GST - Z
     Securities Transaction Tax - Z
     SEBI Turnover Fees - Z
     Stamp Duty - Z
     IPFT
     loading loagic - fetch data for all fields from the pnl files

     3.schema for the each table will be provided with reference excel file in @raw_data_files , eg : for daily_positions table check the raw_data_files and daily_positions folder
     4. do not allow duplicate records and tables
     5. provide a button in ui "create tables in DB" , button selection should create the tables in DB
     6. table session for showing the table list avaialble in the db



### Module 9 - lets analyse the data with multi agents uisng Langraph

START
  │
  ▼
Supervisor
  │
  ▼
Schema Agent
  │
  ▼
SQL Planner
  │
  ▼
SQL Validator
  │
  ├── Invalid ───────────────┐
  │                          │
  └── Valid                  │
        │                    │
        ▼                    │
    Execute SQL              │
        │                    │
        ▼                    │
    Data Found?              │
        │                    │
   ┌────┴─────┐              │
   │          │              │
  Yes        No              │
   │          │              │
   ▼          ▼              │
Analytics   Clarification ───┘
   │
   ▼
Validation
   │
 ┌─┴───────────┐
 │             │
Pass         Fail
 │             │
 ▼             │
Response       │
 │             │
 ▼             │
END      ◄─────┘



## phase 2 completed

## phase 3  - prodcution grade layers implementation - User Management,API Gateway,Rate Limiting,RBAC,Audit Logs

### Module 10 - Authentication Foundation

Goal:

User
 ↓
Login
 ↓
JWT Token
 ↓
Protected APIs

Topics:

Authentication vs Authorization
Password Hashing
JWT
Access Token
Refresh Token
Session Management
User Table Design

Deliverables:

users table
login API
register API
JWT generation
JWT validation


### Module 11 - FastAPI Backend Layer

FastAPI Backend Layer

Goal:

Streamlit
 ↓
FastAPI
 ↓
LangGraph

Topics:

API Design
Dependency Injection
Middleware
Exception Handling

## Module 12 — Rate Limiting

User
 ↓
Redis Counter
 ↓
Rate Limiter
 ↓
API

Topics:
Token Bucket
Sliding Window
Redis


## Module 13 - Observability

Application
 ↓
Phoenix

Infrastructure
 ↓
Prometheus

Dashboards
 ↓
Grafana

## Module 14 - Logging

App Logs
 ↓
Loki
 ↓
Grafana

## Module 15 -  Semantic Response Caching to Reduce LLM Load  


## Module 16 - LLM evaluation frameworks - golden datasets, automated evals

Goal:

LangGraph Pipeline
 ↓
Golden Dataset (20 F&O questions)
 ↓
Ollama-as-Judge (relevance, completeness, groundedness)
 ↓
PostgreSQL (eval_runs + eval_results tables)
 ↓
Streamlit Evals Page

Topics:
LLM-as-Judge evaluation pattern
Golden dataset design (relative questions, no fixed expected values)
Automated pipeline regression testing
Eval metrics: pass rate, data found rate, SQL valid rate, avg latency

Deliverables:
evals/golden_dataset.json — 20 relative F&O analytics questions
evals/judge.py — Ollama-as-judge scorer (relevance, completeness, groundedness 1-5)
evals/runner.py — end-to-end eval runner; runnable via python -m evals.runner
evals/db.py — eval_runs + eval_results schema; save/list/fetch
pages/evals.py — Streamlit page with run history, Run Evals button, per-question drill-down
tests/test_evals.py — 5 isolated unit tests (mocked graph + judge)

## Module 17 - A/B Testing — Model Comparison

Goal:

Golden Dataset (20 F&O questions)
 ↓
Run A (Model A) + Run B (Model B)  ← same dataset, different Ollama models
 ↓
Ollama-as-Judge (scores both independently)
 ↓
PostgreSQL (eval_runs stores model_name per run)
 ↓
A/B Comparison Page (side-by-side metrics + per-question winner)

Topics:
A/B evaluation methodology
LLM model comparison at the task level
Judge decoupling (JUDGE_MODEL vs MODEL_NAME)
Process-level env var override for model switching

Deliverables:
evals/judge.py — MODIFIED: _get_judge_llm(model) replaces shared _get_llm; uses JUDGE_MODEL env var
evals/runner.py — MODIFIED: run_evals(dataset, model_name=None); clears lru_cache on model switch
evals/ab_compare.py — compare_runs() aligns two runs by question_id; QuestionComparison.winner
pages/evals.py — MODIFIED: model text input added to Run Evals section
pages/ab_test.py — Streamlit A/B page with aggregate metrics table + per-question breakdown
tests/test_ab_compare.py — 4 isolated unit tests for compare_runs()