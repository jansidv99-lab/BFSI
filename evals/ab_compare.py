from dataclasses import dataclass, field

from evals.db import get_run_results


@dataclass
class QuestionComparison:
    question_id: str
    question: str
    relevance_a: int
    completeness_a: int
    groundedness_a: int
    passed_a: bool
    latency_a: float
    sql_valid_a: bool
    relevance_b: int
    completeness_b: int
    groundedness_b: int
    passed_b: bool
    latency_b: float
    sql_valid_b: bool

    @property
    def winner(self) -> str:
        score_a = self.relevance_a + self.completeness_a + self.groundedness_a
        score_b = self.relevance_b + self.completeness_b + self.groundedness_b
        if score_a > score_b:
            return "A"
        if score_b > score_a:
            return "B"
        return "tie"


@dataclass
class ABComparison:
    run_id_a: str
    model_a: str
    run_id_b: str
    model_b: str
    questions: list[QuestionComparison] = field(default_factory=list)

    def _rate(self, attr: str) -> float:
        if not self.questions:
            return 0.0
        return sum(1 for q in self.questions if getattr(q, attr)) / len(self.questions)

    def _avg(self, attr: str) -> float:
        if not self.questions:
            return 0.0
        return sum(getattr(q, attr) for q in self.questions) / len(self.questions)

    @property
    def pass_rate_a(self): return self._rate("passed_a")
    @property
    def pass_rate_b(self): return self._rate("passed_b")
    @property
    def avg_relevance_a(self): return self._avg("relevance_a")
    @property
    def avg_relevance_b(self): return self._avg("relevance_b")
    @property
    def avg_completeness_a(self): return self._avg("completeness_a")
    @property
    def avg_completeness_b(self): return self._avg("completeness_b")
    @property
    def avg_groundedness_a(self): return self._avg("groundedness_a")
    @property
    def avg_groundedness_b(self): return self._avg("groundedness_b")
    @property
    def avg_latency_a(self): return self._avg("latency_a")
    @property
    def avg_latency_b(self): return self._avg("latency_b")
    @property
    def a_wins(self): return sum(1 for q in self.questions if q.winner == "A")
    @property
    def b_wins(self): return sum(1 for q in self.questions if q.winner == "B")
    @property
    def ties(self): return sum(1 for q in self.questions if q.winner == "tie")


def compare_runs(run_id_a: str, model_a: str, run_id_b: str, model_b: str) -> ABComparison:
    results_a = {r["question_id"]: r for r in get_run_results(run_id_a)}
    results_b = {r["question_id"]: r for r in get_run_results(run_id_b)}
    common_ids = sorted(set(results_a) & set(results_b))
    questions = []
    for qid in common_ids:
        a, b = results_a[qid], results_b[qid]
        questions.append(QuestionComparison(
            question_id=qid,
            question=a["question"],
            relevance_a=a["judge_relevance"], completeness_a=a["judge_completeness"],
            groundedness_a=a["judge_groundedness"], passed_a=bool(a["judge_passed"]),
            latency_a=float(a["latency_s"] or 0), sql_valid_a=bool(a["sql_valid"]),
            relevance_b=b["judge_relevance"], completeness_b=b["judge_completeness"],
            groundedness_b=b["judge_groundedness"], passed_b=bool(b["judge_passed"]),
            latency_b=float(b["latency_s"] or 0), sql_valid_b=bool(b["sql_valid"]),
        ))
    return ABComparison(run_id_a=run_id_a, model_a=model_a, run_id_b=run_id_b, model_b=model_b, questions=questions)
