from ingestion.db import get_connection

_CREATE_EVAL_RUNS = """
CREATE TABLE IF NOT EXISTS eval_runs (
    id               SERIAL PRIMARY KEY,
    run_id           TEXT UNIQUE NOT NULL,
    run_at           TIMESTAMP NOT NULL,
    model_name       TEXT,
    total_questions  INT,
    pass_rate        FLOAT,
    data_found_rate  FLOAT,
    sql_valid_rate   FLOAT,
    avg_latency_s    FLOAT,
    avg_relevance    FLOAT,
    avg_completeness FLOAT,
    avg_groundedness FLOAT
);
"""

_CREATE_EVAL_RESULTS = """
CREATE TABLE IF NOT EXISTS eval_results (
    id                   SERIAL PRIMARY KEY,
    run_id               TEXT REFERENCES eval_runs(run_id),
    question_id          TEXT,
    question             TEXT,
    final_response       TEXT,
    sql_query            TEXT,
    data_found           BOOL,
    sql_valid            BOOL,
    retry_count_sql      INT,
    retry_count_analysis INT,
    latency_s            FLOAT,
    judge_relevance      INT,
    judge_completeness   INT,
    judge_groundedness   INT,
    judge_passed         BOOL,
    error                TEXT,
    evaluated_at         TIMESTAMP DEFAULT NOW()
);
"""

_INSERT_RUN = """
INSERT INTO eval_runs (
    run_id, run_at, model_name, total_questions,
    pass_rate, data_found_rate, sql_valid_rate, avg_latency_s,
    avg_relevance, avg_completeness, avg_groundedness
) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
ON CONFLICT (run_id) DO NOTHING
"""

_INSERT_RESULT = """
INSERT INTO eval_results (
    run_id, question_id, question, final_response, sql_query,
    data_found, sql_valid, retry_count_sql, retry_count_analysis,
    latency_s, judge_relevance, judge_completeness, judge_groundedness,
    judge_passed, error
) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
"""

_LIST_RUNS = """
SELECT run_id, run_at, model_name, total_questions,
       pass_rate, data_found_rate, sql_valid_rate, avg_latency_s,
       avg_relevance, avg_completeness, avg_groundedness
FROM eval_runs ORDER BY run_at DESC LIMIT 50
"""

_GET_RUN_RESULTS = """
SELECT question_id, question, final_response, sql_query,
       data_found, sql_valid, retry_count_sql, retry_count_analysis,
       latency_s, judge_relevance, judge_completeness, judge_groundedness,
       judge_passed, error, evaluated_at
FROM eval_results WHERE run_id = %s ORDER BY id
"""


def ensure_eval_schema() -> None:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(_CREATE_EVAL_RUNS)
            cur.execute(_CREATE_EVAL_RESULTS)
        conn.commit()
    finally:
        conn.close()


def save_eval_run(run) -> None:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(_INSERT_RUN, (
                run.run_id, run.run_at, run.model_name, run.total_questions,
                run.pass_rate, run.data_found_rate, run.sql_valid_rate, run.avg_latency_s,
                run.avg_relevance, run.avg_completeness, run.avg_groundedness,
            ))
            for r in run.results:
                cur.execute(_INSERT_RESULT, (
                    run.run_id, r.question_id, r.question, r.final_response, r.sql_query,
                    r.data_found, r.sql_valid, r.retry_count_sql, r.retry_count_analysis,
                    r.latency_s, r.judge_relevance, r.judge_completeness, r.judge_groundedness,
                    r.judge_passed, r.error or None,
                ))
        conn.commit()
    finally:
        conn.close()


def list_runs() -> list[dict]:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(_LIST_RUNS)
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        conn.close()


def get_run_results(run_id: str) -> list[dict]:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(_GET_RUN_RESULTS, (run_id,))
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        conn.close()
