"""FastAPI endpoints for the independent web client."""

from __future__ import annotations

import json
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import text

from texttosql.graph import run_pipeline
from texttosql.viz import wants_visualization

from . import storage
from .auth import get_current_user
from .description import describe_result
from .evaluation import router as evaluation_router

log = logging.getLogger(__name__)
User = Annotated[dict, Depends(get_current_user)]


@asynccontextmanager
async def lifespan(app: FastAPI):
    storage.initialize()
    yield


app = FastAPI(title="TSChat API", lifespan=lifespan)
app.include_router(evaluation_router)


@app.middleware("http")
async def check_origin(request: Request, call_next):
    # Cookie-authenticated browser writes must originate from this app.
    origin = request.headers.get("origin")
    if request.method in {"POST", "PUT", "PATCH", "DELETE"} and origin:
        parsed = urlparse(origin)
        if parsed.netloc != request.headers.get("host"):
            return JSONResponse({"detail": "请求来源不受信任"}, status_code=403)
    return await call_next(request)


class Login(BaseModel):
    username: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=1, max_length=200)


class Question(BaseModel):
    content: str = Field(min_length=1, max_length=10000)


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.post("/api/login")
def login(body: Login, response: Response):
    user = storage.verify_user(body.username, body.password)
    if not user:
        raise HTTPException(401, "用户名或密码不正确")
    token = storage.new_session(user["username"])
    response.set_cookie(
        "textsql_session",
        token,
        httponly=True,
        secure=os.getenv("APP_SECURE_COOKIE", "").lower() == "true",
        samesite="lax",
        max_age=7 * 86400,
        path="/",
    )
    return {k: user[k] for k in ("username", "role", "display_name")}


@app.post("/api/logout")
def logout(request: Request, response: Response):
    storage.delete_session(request.cookies.get("textsql_session"))
    response.delete_cookie("textsql_session", path="/")
    return {"ok": True}


@app.get("/api/me")
def me(user: User):
    return user


@app.get("/api/conversations")
def conversations(user: User):
    with storage.db() as conn:
        return [
            dict(row)
            for row in conn.execute(
                text(
                    "SELECT id,title,created_at,updated_at FROM conversations WHERE owner=:owner ORDER BY updated_at DESC"
                ),
                {"owner": user["username"]},
            ).mappings()
        ]


def owned(conn, conversation_id, user):
    result = (
        conn.execute(
            text(
                "SELECT id,title,created_at,updated_at FROM conversations WHERE id=:id AND owner=:owner"
            ),
            {"id": conversation_id, "owner": user["username"]},
        )
        .mappings()
        .first()
    )
    if not result:
        raise HTTPException(404, "会话不存在")
    return dict(result)


@app.post("/api/conversations")
def create_conversation(user: User):
    identifier, timestamp = storage.uid(), storage.now()
    with storage.db() as conn:
        conn.execute(
            text(
                "INSERT INTO conversations VALUES (:id,:owner,:title,:created,:updated)"
            ),
            {
                "id": identifier,
                "owner": user["username"],
                "title": "新查询",
                "created": timestamp,
                "updated": timestamp,
            },
        )
    return {
        "id": identifier,
        "title": "新查询",
        "created_at": timestamp,
        "updated_at": timestamp,
    }


@app.get("/api/conversations/{conversation_id}")
def get_conversation(conversation_id: str, user: User):
    with storage.db() as conn:
        conversation = owned(conn, conversation_id, user)
        rows = conn.execute(
            text(
                "SELECT id,role,content,result_json,created_at FROM messages WHERE conversation_id=:id ORDER BY created_at,id"
            ),
            {"id": conversation_id},
        ).mappings()
        conversation["messages"] = [
            {
                **dict(row),
                "result": json.loads(row["result_json"])
                if row["result_json"]
                else None,
            }
            for row in rows
        ]
    return conversation


@app.delete("/api/conversations/{conversation_id}")
def delete_conversation(conversation_id: str, user: User):
    with storage.db() as conn:
        owned(conn, conversation_id, user)
        conn.execute(
            text("DELETE FROM messages WHERE conversation_id=:id"),
            {"id": conversation_id},
        )
        conn.execute(
            text("DELETE FROM conversations WHERE id=:id"), {"id": conversation_id}
        )
    return {"ok": True}


@app.post("/api/conversations/{conversation_id}/messages")
async def ask(conversation_id: str, body: Question, user: User):
    question = body.content.strip()
    if not question:
        raise HTTPException(422, "请输入问题")
    with storage.db() as conn:
        conversation = owned(conn, conversation_id, user)
        timestamp = storage.now()
        conn.execute(
            text(
                "INSERT INTO messages (id,conversation_id,role,content,created_at) VALUES (:id,:conversation,:role,:content,:created)"
            ),
            {
                "id": storage.uid(),
                "conversation": conversation_id,
                "role": "user",
                "content": question,
                "created": timestamp,
            },
        )
        conn.execute(
            text(
                "UPDATE conversations SET title=:title,updated_at=:updated WHERE id=:id"
            ),
            {
                "title": question[:50]
                if conversation["title"] == "新查询"
                else conversation["title"],
                "updated": timestamp,
                "id": conversation_id,
            },
        )
    try:
        state = await run_pipeline(question)
        execution = state.get("execution_result") or {}
        success = bool(
            state.get("final_sql_query") and execution.get("status") == "success"
        )
        errors = (state.get("validation_result") or {}).get("errors", [])
        description, described_rows = None, 0
        if success:
            description, described_rows = await describe_result(
                question,
                state.get("result_columns") or execution.get("columns") or [],
                execution.get("rows") or [],
                execution.get("row_count", 0),
            )
        result = {
            "success": success,
            "sql": state.get("final_sql_query") or state.get("sql_query"),
            "columns": state.get("result_columns") or execution.get("columns") or [],
            "rows": (execution.get("rows") or [])[:50],
            "row_count": execution.get("row_count", 0),
            "description": description,
            "described_rows": described_rows,
            "chart": success and wants_visualization(question),
            "error": None
            if success
            else execution.get("error_message")
            or state.get("error")
            or (errors[0] if errors else "查询未完成"),
            "validation_errors": errors,
        }
        content = "查询完成" if success else "查询未完成"
    except Exception:
        log.exception("Query failed")
        result = {
            "success": False,
            "error": "查询服务暂时不可用，请检查服务日志后重试。",
        }
        content = "查询未完成"
    timestamp = storage.now()
    message = {
        "id": storage.uid(),
        "role": "assistant",
        "content": content,
        "result": result,
        "created_at": timestamp,
    }
    with storage.db() as conn:
        # A conversation may have been deleted while its query was running.
        owned(conn, conversation_id, user)
        conn.execute(
            text(
                "INSERT INTO messages (id,conversation_id,role,content,result_json,created_at) VALUES (:id,:conversation,:role,:content,:result,:created)"
            ),
            {
                "id": message["id"],
                "conversation": conversation_id,
                "role": "assistant",
                "content": content,
                "result": json.dumps(result, ensure_ascii=False, default=str),
                "created": timestamp,
            },
        )
        conn.execute(
            text("UPDATE conversations SET updated_at=:updated WHERE id=:id"),
            {"updated": timestamp, "id": conversation_id},
        )
    return message


# In production FastAPI can serve the Vite build from the same origin.
dist = Path(__file__).resolve().parents[3] / "frontend" / "dist"
if (dist / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def frontend(path: str):
        if path.startswith(("api/", "evaluation/")):
            raise HTTPException(404)
        return FileResponse(dist / "index.html")
