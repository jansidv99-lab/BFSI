import json

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from agents.sql_agent import DataChatState, data_chat_graph
from api.deps import get_current_user
from api.rate_limit import data_chat_limiter

router = APIRouter(tags=["data-chat"])


class DataChatRequest(BaseModel):
    question: str
    history: list[dict] = []


class DataChatResponse(BaseModel):
    answer: str
    sql_query: str
    query_results: list[dict]
    data_found: bool


@router.post("/", response_model=DataChatResponse)
def run_data_chat(body: DataChatRequest, _user: dict = Depends(get_current_user)):
    data_chat_limiter.check(f"data_chat:{_user['sub']}")

    initial_state: DataChatState = {
        "question": body.question,
        "history": body.history,
        "schema_context": "",
        "sql_query": "",
        "sql_error": "",
        "query_results": [],
        "data_found": False,
        "answer": "",
        "retry_count": 0,
    }
    try:
        result = data_chat_graph.invoke(initial_state)
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

    raw_results = result.get("query_results", [])
    try:
        safe_results = json.loads(json.dumps(raw_results, default=str))
    except Exception:
        safe_results = []

    return DataChatResponse(
        answer=result.get("answer", ""),
        sql_query=result.get("sql_query", ""),
        query_results=safe_results,
        data_found=result.get("data_found", False),
    )
