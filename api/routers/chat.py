import os
from typing import Generator

import ollama
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from api.cache import cache_get, cache_set
from api.deps import get_current_user
from api.metrics import CHAT_REQUESTS
from api.rate_limit import chat_limiter

router = APIRouter(tags=["chat"])

_CHAT_TTL = 300


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    message: str
    history: list[ChatMessage] = []


def _token_stream(messages: list[dict]) -> Generator[str, None, None]:
    client = ollama.Client(host=os.environ.get("OLLAMA_HOST", "http://localhost:11434"))
    model = os.environ.get("MODEL_NAME", "gemma4:e2b")
    try:
        stream = client.chat(model=model, messages=messages, stream=True)
        for chunk in stream:
            token = chunk["message"]["content"]
            if token:
                yield f"data: {token}\n\n"
    except Exception as e:
        yield f"data: [ERROR] {e}\n\n"


def _caching_stream(messages: list[dict], cache_key_parts: list[str]) -> Generator[str, None, None]:
    accumulated: list[str] = []
    try:
        for event in _token_stream(messages):
            accumulated.append(event)
            yield event
    finally:
        full_text = "".join(accumulated)
        if full_text and "[ERROR]" not in full_text:
            cache_set("chat", cache_key_parts, full_text, _CHAT_TTL)


@router.post("/")
def chat(body: ChatRequest, _user: dict = Depends(get_current_user)):
    chat_limiter.check(f"chat:{_user['sub']}")
    CHAT_REQUESTS.inc()

    if not body.history:
        cached = cache_get("chat", [body.message])
        if cached is not None:
            return StreamingResponse(
                iter([cached]),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )

    messages = [{"role": m.role, "content": m.content} for m in body.history]
    messages.append({"role": "user", "content": body.message})

    stream = _caching_stream(messages, [body.message]) if not body.history else _token_stream(messages)
    return StreamingResponse(
        stream,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
