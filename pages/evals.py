import os

import pandas as pd
import streamlit as st

from auth.session import require_auth
from evals.dataset import load_dataset
from evals.db import ensure_eval_schema, get_run_results, list_runs, save_eval_run
from evals.runner import run_evals
from utils.state import init_session_state

st.set_page_config(page_title="Eval Results", page_icon="🧪")
st.title("LLM Evaluation Results")

init_session_state()
require_auth()

try:
    ensure_eval_schema()
except Exception as e:
    st.error(f"Cannot connect to database: {e}")
    st.stop()

# ── Recent runs ───────────────────────────────────────────────────────────────

st.subheader("Recent Eval Runs")
try:
    runs = list_runs()
except Exception as e:
    st.error(f"Failed to load runs: {e}")
    runs = []

if runs:
    df_runs = pd.DataFrame(runs)
    df_runs["run_at"] = pd.to_datetime(df_runs["run_at"]).dt.strftime("%Y-%m-%d %H:%M")
    for col in ["pass_rate", "data_found_rate", "sql_valid_rate"]:
        df_runs[col] = (df_runs[col] * 100).round(1).astype(str) + "%"
    df_runs["avg_latency_s"] = df_runs["avg_latency_s"].round(1)
    st.dataframe(
        df_runs[["run_at", "model_name", "total_questions", "pass_rate",
                 "avg_relevance", "avg_completeness", "avg_groundedness", "avg_latency_s"]],
        use_container_width=True, hide_index=True,
    )
else:
    st.info("No eval runs yet. Click 'Run Evals' below to start.")

st.divider()

# ── Run evals ─────────────────────────────────────────────────────────────────

st.subheader("Run Evaluation")
model_input = st.text_input(
    "Model name",
    value=os.environ.get("MODEL_NAME", "gemma4:e2b"),
    help="Ollama model tag — must be pulled locally (e.g. qwen3:1.7b, gemma4:e2b)",
)
if st.button("Run Evals", type="primary"):
    with st.spinner("Running evaluations — this may take several minutes..."):
        try:
            dataset = load_dataset()
            run = run_evals(dataset, model_name=model_input or None)
            save_eval_run(run)
            st.success(
                f"Done. Pass rate: {run.pass_rate:.1%} over {run.total_questions} questions. "
                f"Avg latency: {run.avg_latency_s:.1f}s"
            )
            st.rerun()
        except Exception as e:
            st.error(f"Eval run failed: {e}")

st.divider()

# ── Run detail ────────────────────────────────────────────────────────────────

st.subheader("Run Detail")
if runs:
    run_options = {
        f"{r['run_at']} — {r['model_name']} (pass: {r['pass_rate']})": r["run_id"]
        for r in runs
    }
    selected_run_id = run_options[st.selectbox("Select a run", list(run_options.keys()))]
    try:
        results = get_run_results(selected_run_id)
    except Exception as e:
        st.error(f"Failed to load run results: {e}")
        results = []

    if results:
        df = pd.DataFrame(results)
        styled = (
            df[["question_id", "question", "data_found", "sql_valid",
                "judge_relevance", "judge_completeness", "judge_groundedness",
                "judge_passed", "latency_s", "error"]]
            .style.map(
                lambda v: "background-color: #1a3a1a" if v else "background-color: #3a1a1a",
                subset=["judge_passed"],
            )
        )
        st.dataframe(styled, use_container_width=True, hide_index=True)
    else:
        st.info("No results found for this run.")
