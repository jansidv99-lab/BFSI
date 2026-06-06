import pandas as pd
import streamlit as st

from auth.session import require_auth
from evals.ab_compare import compare_runs
from evals.db import list_runs
from utils.state import init_session_state

st.set_page_config(page_title="A/B Model Comparison", page_icon="⚖️")
st.title("A/B Model Comparison")

init_session_state()
require_auth()

try:
    runs = list_runs()
except Exception as e:
    st.error(f"Failed to load runs: {e}")
    st.stop()

if len(runs) < 2:
    st.info("Need at least 2 eval runs to compare. Go to Eval Results and run evals for two different models.")
    st.stop()

run_labels = {
    f"{r['run_at']} — {r['model_name']} ({int((r['pass_rate'] or 0) * 100)}% pass)": r
    for r in runs
}
labels = list(run_labels.keys())

col1, col2 = st.columns(2)
with col1:
    st.subheader("Model A")
    label_a = st.selectbox("Select run A", labels, index=0, key="run_a")
with col2:
    st.subheader("Model B")
    label_b = st.selectbox("Select run B", labels, index=min(1, len(labels) - 1), key="run_b")

if label_a == label_b:
    st.warning("Select two different runs to compare.")
    st.stop()

run_a = run_labels[label_a]
run_b = run_labels[label_b]

try:
    ab = compare_runs(run_a["run_id"], run_a["model_name"], run_b["run_id"], run_b["model_name"])
except Exception as e:
    st.error(f"Comparison failed: {e}")
    st.stop()

if not ab.questions:
    st.warning("No matching questions found between the two runs.")
    st.stop()

st.divider()

# ── Aggregate comparison ──────────────────────────────────────────────────────

st.subheader("Aggregate Metrics")
metrics = {
    "Metric": ["Pass Rate", "Avg Relevance", "Avg Completeness", "Avg Groundedness", "Avg Latency (s)", "Questions Won"],
    run_a["model_name"]: [
        f"{ab.pass_rate_a:.1%}", f"{ab.avg_relevance_a:.2f}", f"{ab.avg_completeness_a:.2f}",
        f"{ab.avg_groundedness_a:.2f}", f"{ab.avg_latency_a:.1f}", str(ab.a_wins),
    ],
    run_b["model_name"]: [
        f"{ab.pass_rate_b:.1%}", f"{ab.avg_relevance_b:.2f}", f"{ab.avg_completeness_b:.2f}",
        f"{ab.avg_groundedness_b:.2f}", f"{ab.avg_latency_b:.1f}", str(ab.b_wins),
    ],
}
st.dataframe(pd.DataFrame(metrics), use_container_width=True, hide_index=True)
st.caption(f"Ties: {ab.ties} | Questions compared: {len(ab.questions)}")

st.divider()

# ── Per-question breakdown ────────────────────────────────────────────────────

st.subheader("Per-Question Breakdown")
rows = []
for q in ab.questions:
    rows.append({
        "Q": q.question_id,
        "Question": q.question[:60] + "..." if len(q.question) > 60 else q.question,
        "Rel A": q.relevance_a, "Cmp A": q.completeness_a, "Gnd A": q.groundedness_a, "Pass A": q.passed_a,
        "Rel B": q.relevance_b, "Cmp B": q.completeness_b, "Gnd B": q.groundedness_b, "Pass B": q.passed_b,
        "Winner": q.winner,
        "Lat A": round(q.latency_a, 1), "Lat B": round(q.latency_b, 1),
    })

df = pd.DataFrame(rows)
styled = df.style.map(
    lambda v: "background-color: #1a2a3a" if v == "A" else ("background-color: #2a1a3a" if v == "B" else ""),
    subset=["Winner"],
)
st.dataframe(styled, use_container_width=True, hide_index=True)
