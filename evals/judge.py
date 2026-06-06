import functools
import json
import os
import re
from dataclasses import dataclass

from langchain_core.messages import HumanMessage
from langchain_ollama import ChatOllama

_JUDGE_PROMPT = """\
You are an evaluation judge for an F&O trading analytics assistant.

Question asked: {question}

Assistant response: {response}

SQL query results (first 10 rows): {results_snippet}

Score the response on each dimension from 1 to 5:
- relevance: Does the response directly answer the question asked?
- completeness: Does the answer include specific numbers and sufficient context?
- groundedness: Are all claims supported by the SQL results, with no hallucination?

Reply with ONLY valid JSON in this exact format:
{{"relevance": N, "completeness": N, "groundedness": N}}

Where N is an integer 1 to 5. No other text."""

_SCORE_RE = re.compile(r'\{[^}]+\}', re.DOTALL)


@dataclass
class JudgeScore:
    relevance: int
    completeness: int
    groundedness: int
    passed: bool


def _extract_scores(text: str) -> dict:
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        pass
    m = _SCORE_RE.search(text)
    if m:
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            pass
    return {"relevance": 3, "completeness": 3, "groundedness": 3}


@functools.lru_cache(maxsize=4)
def _get_judge_llm(model: str) -> ChatOllama:
    return ChatOllama(
        base_url=os.environ.get("OLLAMA_HOST", "http://localhost:11434"),
        model=model,
    )


def score(question: str, response: str, query_results: list[dict]) -> JudgeScore:
    judge_model = os.environ.get("JUDGE_MODEL") or os.environ.get("MODEL_NAME", "gemma4:e2b")
    snippet = str(query_results[:10]) if query_results else "(no results)"
    prompt = _JUDGE_PROMPT.format(
        question=question,
        response=response[:1000],
        results_snippet=snippet[:500],
    )
    try:
        raw = _get_judge_llm(judge_model).invoke([HumanMessage(content=prompt)]).content
        scores = _extract_scores(raw)
    except Exception:
        scores = {"relevance": 3, "completeness": 3, "groundedness": 3}
    r = int(scores.get("relevance", 3))
    c = int(scores.get("completeness", 3))
    g = int(scores.get("groundedness", 3))
    return JudgeScore(relevance=r, completeness=c, groundedness=g, passed=(r >= 3 and c >= 3 and g >= 3))
