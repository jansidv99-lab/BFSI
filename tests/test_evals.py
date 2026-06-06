from unittest.mock import MagicMock, patch


def _llm_mock(content):
    resp = MagicMock()
    resp.content = content
    llm = MagicMock()
    llm.invoke.return_value = resp
    return llm


# ── judge ─────────────────────────────────────────────────────────────────────

def test_judge_passes_good_response():
    with patch("evals.judge._get_judge_llm", return_value=_llm_mock('{"relevance":5,"completeness":4,"groundedness":5}')):
        from evals.judge import score
        r = score("What is total P&L?", "Total P&L is 12500.", [{"realized_pnl": 12500}])
    assert r.passed is True and r.relevance == 5


def test_judge_fails_bad_response():
    with patch("evals.judge._get_judge_llm", return_value=_llm_mock('{"relevance":1,"completeness":2,"groundedness":1}')):
        from evals.judge import score
        r = score("What is total P&L?", "I don't know.", [])
    assert r.passed is False and r.relevance == 1


def test_judge_fails_open_on_parse_error():
    with patch("evals.judge._get_judge_llm", return_value=_llm_mock("sorry i cannot score this")):
        from evals.judge import score
        r = score("What is total P&L?", "Some answer.", [])
    assert r.relevance == 3 and r.passed is True


# ── runner ────────────────────────────────────────────────────────────────────

def test_runner_builds_eval_result():
    from evals.judge import JudgeScore
    fake_state = {
        "final_response": "Total P&L is 5000.", "sql_query": "SELECT SUM(realized_pnl) FROM daily_pl",
        "data_found": True, "sql_valid": True, "retry_count_sql": 0, "retry_count_analysis": 0,
        "query_results": [{"realized_pnl": 5000}], "analysis": "", "schema_context": "",
        "validation_error": "", "analytics_valid": True, "question": "What is total P&L?",
    }
    with patch("evals.runner.graph") as mock_graph, patch("evals.runner.judge") as mock_judge:
        mock_graph.invoke.return_value = fake_state
        mock_judge.score.return_value = JudgeScore(relevance=4, completeness=4, groundedness=4, passed=True)
        from evals.runner import run_evals
        run = run_evals([{"id": "q001", "question": "What is total P&L?"}])
    r = run.results[0]
    assert r.question_id == "q001" and r.data_found is True and r.judge_passed is True and r.error == ""


def test_runner_handles_graph_exception():
    with patch("evals.runner.graph") as mock_graph, patch("evals.runner.judge"):
        mock_graph.invoke.side_effect = RuntimeError("Ollama unreachable")
        from evals.runner import run_evals
        run = run_evals([{"id": "q002", "question": "How many trades?"}])
    r = run.results[0]
    assert "Ollama unreachable" in r.error and r.judge_passed is False
