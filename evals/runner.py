import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from agents.graph import AnalysisState, graph
from evals import judge


@dataclass
class EvalResult:
    question_id: str
    question: str
    final_response: str
    sql_query: str
    data_found: bool
    sql_valid: bool
    retry_count_sql: int
    retry_count_analysis: int
    latency_s: float
    judge_relevance: int
    judge_completeness: int
    judge_groundedness: int
    judge_passed: bool
    error: str = ""


@dataclass
class EvalRun:
    run_id: str
    run_at: datetime
    model_name: str
    results: list[EvalResult] = field(default_factory=list)

    @property
    def total_questions(self): return len(self.results)

    @property
    def pass_rate(self): return sum(1 for r in self.results if r.judge_passed) / len(self.results) if self.results else 0.0

    @property
    def data_found_rate(self): return sum(1 for r in self.results if r.data_found) / len(self.results) if self.results else 0.0

    @property
    def sql_valid_rate(self): return sum(1 for r in self.results if r.sql_valid) / len(self.results) if self.results else 0.0

    @property
    def avg_latency_s(self): return sum(r.latency_s for r in self.results) / len(self.results) if self.results else 0.0

    @property
    def avg_relevance(self): return sum(r.judge_relevance for r in self.results) / len(self.results) if self.results else 0.0

    @property
    def avg_completeness(self): return sum(r.judge_completeness for r in self.results) / len(self.results) if self.results else 0.0

    @property
    def avg_groundedness(self): return sum(r.judge_groundedness for r in self.results) / len(self.results) if self.results else 0.0


_EMPTY_STATE: AnalysisState = {
    "question": "", "schema_context": "", "sql_query": "", "sql_valid": False,
    "analytics_valid": False, "validation_error": "", "query_results": [],
    "data_found": False, "analysis": "", "final_response": "",
    "retry_count_sql": 0, "retry_count_analysis": 0,
}


def run_evals(dataset: list[dict], model_name: str | None = None) -> EvalRun:
    if model_name is not None:
        os.environ["MODEL_NAME"] = model_name
        from agents.graph import _get_llm
        _get_llm.cache_clear()

    run = EvalRun(
        run_id=str(uuid.uuid4()),
        run_at=datetime.now(timezone.utc),
        model_name=os.environ.get("MODEL_NAME", "gemma4:e2b"),
    )
    for entry in dataset:
        result = EvalResult(
            question_id=entry["id"], question=entry["question"],
            final_response="", sql_query="", data_found=False, sql_valid=False,
            retry_count_sql=0, retry_count_analysis=0, latency_s=0.0,
            judge_relevance=3, judge_completeness=3, judge_groundedness=3, judge_passed=False,
        )
        t0 = time.monotonic()
        try:
            state = graph.invoke({**_EMPTY_STATE, "question": entry["question"]})
            result.latency_s = time.monotonic() - t0
            result.final_response = state.get("final_response", "")
            result.sql_query = state.get("sql_query", "")
            result.data_found = bool(state.get("data_found"))
            result.sql_valid = bool(state.get("sql_valid"))
            result.retry_count_sql = int(state.get("retry_count_sql", 0))
            result.retry_count_analysis = int(state.get("retry_count_analysis", 0))
            js = judge.score(entry["question"], result.final_response, state.get("query_results", []))
            result.judge_relevance = js.relevance
            result.judge_completeness = js.completeness
            result.judge_groundedness = js.groundedness
            result.judge_passed = js.passed
        except Exception as e:
            result.error = str(e)
            result.latency_s = time.monotonic() - t0
        run.results.append(result)
    return run


if __name__ == "__main__":
    import sys
    from evals.dataset import load_dataset
    from evals.db import ensure_eval_schema, save_eval_run
    cli_model = sys.argv[1] if len(sys.argv) > 1 else None
    dataset = load_dataset()
    print(f"Running {len(dataset)} questions..." + (f" [{cli_model}]" if cli_model else ""))
    run = run_evals(dataset, model_name=cli_model)
    print(f"Pass rate: {run.pass_rate:.1%} | Data found: {run.data_found_rate:.1%} | Avg latency: {run.avg_latency_s:.1f}s")
    ensure_eval_schema()
    save_eval_run(run)
    print(f"Saved run {run.run_id}")
