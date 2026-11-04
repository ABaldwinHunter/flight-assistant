"""Local-only HTTP API and static web interface (no authentication)."""
from pathlib import Path
import sqlite3
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from .config import Settings
from .service import ChatService, MAX_USER_CHARS
from .store import ConversationBusy, ConversationNotFound


class EmptyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MessageBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str = Field(strict=True, min_length=1, max_length=MAX_USER_CHARS)


class RatingBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rating: Literal["up", "down"] | None


def create_app(settings: Settings | None = None, *, provider=None) -> FastAPI:
    service = ChatService(settings or Settings.from_env(), provider=provider)
    application = FastAPI(title="Simulated Flight Assistant", version="1.0.0")
    application.state.service = service

    @application.exception_handler(RequestValidationError)
    async def validation_error(_request, _error):
        return JSONResponse(status_code=422, content={"detail": "Invalid request. Use only the expected fields. Messages require nonblank text of at most 4000 characters; ratings must be 'up', 'down', or null."})

    @application.exception_handler(ConversationNotFound)
    async def missing_conversation(_request, _error):
        return JSONResponse(status_code=404, content={"detail": "Conversation not found."})

    @application.exception_handler(ConversationBusy)
    async def busy_conversation(_request, _error):
        return JSONResponse(status_code=409, content={"detail": "This conversation is already processing a message. Wait for it to finish before sending another."})

    @application.exception_handler(sqlite3.Error)
    async def storage_error(_request, _error):
        return JSONResponse(status_code=503, content={"detail": "Chat storage is temporarily unavailable. Please try again."})

    @application.exception_handler(Exception)
    async def unexpected_error(_request, _error):
        return JSONResponse(status_code=500, content={"detail": "The server could not complete this request. Please try again."})

    @application.get("/api/config")
    def config():
        return service.config()

    @application.get("/api/conversations")
    def conversations():
        return {"conversations": service.store.list_conversations()}

    @application.post("/api/conversations", status_code=201)
    def create_conversation(_body: EmptyBody):
        return service.store.create_conversation()

    @application.get("/api/conversations/{conversation_id}")
    def conversation(conversation_id: str):
        return service.store.get_conversation(conversation_id)

    @application.put("/api/conversations/{conversation_id}/rating")
    def rate_conversation(conversation_id: str, body: RatingBody):
        return service.store.set_rating(conversation_id, body.rating)

    @application.post("/api/conversations/{conversation_id}/messages")
    def message(conversation_id: str, body: MessageBody):
        if not body.content.strip():
            raise HTTPException(status_code=422, detail="Message cannot be blank.")
        return service.send_message(conversation_id, body.content)

    static = Path(__file__).parent / "static"
    application.mount("/static", StaticFiles(directory=static, check_dir=False), name="static")

    @application.get("/", include_in_schema=False)
    def index():
        return FileResponse(static / "index.html")

    return application


app = create_app()
