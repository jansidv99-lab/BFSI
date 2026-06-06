from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from prometheus_fastapi_instrumentator import Instrumentator

from api.routers import analytics, auth, chat, data_chat

load_dotenv()


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        from auth.users import ensure_users_table
        ensure_users_table()
    except Exception:
        pass
    yield


app = FastAPI(title="Chatbot API", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def _unhandled(request: Request, exc: Exception):
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


app.include_router(auth.router, prefix="/auth")
app.include_router(chat.router, prefix="/chat")
app.include_router(analytics.router, prefix="/analytics")
app.include_router(data_chat.router, prefix="/data-chat")

Instrumentator(excluded_handlers=["/metrics", "/health"]).instrument(app).expose(app)


@app.get("/health")
def health():
    return {"status": "ok"}
