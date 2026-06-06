from unittest.mock import patch

from evals.ab_compare import compare_runs


def _make_results(question_ids: list[str]) -> list[dict]:
    return [
        {
            "question_id": qid,
            "question": f"Question {qid}",
            "judge_relevance": 4, "judge_completeness": 3, "judge_groundedness": 4,
            "judge_passed": True, "latency_s": 2.0, "sql_valid": True,
        }
        for qid in question_ids
    ]


def test_compare_aligns_by_question_id():
    results_a = _make_results(["q001", "q002"])
    results_b = _make_results(["q001", "q002"])
    with patch("evals.ab_compare.get_run_results", side_effect=[results_a, results_b]):
        ab = compare_runs("run-a", "model-a", "run-b", "model-b")
    assert len(ab.questions) == 2
    assert ab.questions[0].question_id == "q001"


def test_compare_skips_unmatched_questions():
    results_a = _make_results(["q001", "q002"])
    results_b = _make_results(["q001"])
    with patch("evals.ab_compare.get_run_results", side_effect=[results_a, results_b]):
        ab = compare_runs("run-a", "model-a", "run-b", "model-b")
    assert len(ab.questions) == 1
    assert ab.questions[0].question_id == "q001"


def test_compare_pass_rate_properties():
    results_a = [
        {"question_id": "q001", "question": "Q1", "judge_relevance": 5, "judge_completeness": 5,
         "judge_groundedness": 5, "judge_passed": True, "latency_s": 1.0, "sql_valid": True},
        {"question_id": "q002", "question": "Q2", "judge_relevance": 1, "judge_completeness": 1,
         "judge_groundedness": 1, "judge_passed": False, "latency_s": 3.0, "sql_valid": False},
    ]
    results_b = [
        {"question_id": "q001", "question": "Q1", "judge_relevance": 4, "judge_completeness": 4,
         "judge_groundedness": 4, "judge_passed": True, "latency_s": 2.0, "sql_valid": True},
        {"question_id": "q002", "question": "Q2", "judge_relevance": 4, "judge_completeness": 4,
         "judge_groundedness": 4, "judge_passed": True, "latency_s": 2.0, "sql_valid": True},
    ]
    with patch("evals.ab_compare.get_run_results", side_effect=[results_a, results_b]):
        ab = compare_runs("run-a", "model-a", "run-b", "model-b")
    assert ab.pass_rate_a == 0.5
    assert ab.pass_rate_b == 1.0
    assert ab.a_wins == 1  # wins q001 (score 15 vs 12)
    assert ab.b_wins == 1  # wins q002 (score 12 vs 3)


def test_compare_winner_tie():
    results = [
        {"question_id": "q001", "question": "Q1", "judge_relevance": 4, "judge_completeness": 4,
         "judge_groundedness": 4, "judge_passed": True, "latency_s": 1.0, "sql_valid": True},
    ]
    with patch("evals.ab_compare.get_run_results", side_effect=[results, list(results)]):
        ab = compare_runs("run-a", "model-a", "run-b", "model-b")
    assert ab.questions[0].winner == "tie"
    assert ab.ties == 1
