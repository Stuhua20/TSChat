"""Application users, sessions and conversation persistence."""

from __future__ import annotations

import hashlib
import os
import secrets
import time
import uuid
from contextlib import contextmanager

import bcrypt
from sqlalchemy import create_engine, inspect, text

from texttosql.config import APP_DB_URI, app_sqlite_path


def engine():
    if APP_DB_URI.startswith("sqlite:///"):
        path = app_sqlite_path()
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            try:
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                os.close(fd)
            except FileExistsError:
                pass
        return create_engine(f"sqlite:///{path}", connect_args={"timeout": 10})
    return create_engine(APP_DB_URI)


@contextmanager
def db():
    e = engine()
    try:
        with e.begin() as conn:
            yield conn
    finally:
        e.dispose()


def initialize():
    with db() as conn:
        for sql in [
            "CREATE TABLE IF NOT EXISTS app_users (username TEXT PRIMARY KEY, password_hash TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'scientist', display_name TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)",
            "CREATE TABLE IF NOT EXISTS web_sessions (token_hash TEXT PRIMARY KEY, username TEXT NOT NULL, expires_at BIGINT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS conversations (id TEXT PRIMARY KEY, owner TEXT NOT NULL, title TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS messages (id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL, result_json TEXT, created_at TEXT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS imported_conversations (id TEXT PRIMARY KEY)",
            "CREATE INDEX IF NOT EXISTS idx_conversations_owner ON conversations(owner, updated_at)",
            "CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id, created_at)",
        ]:
            conn.execute(text(sql))
        # Copy old chat data idempotently; leave original tables untouched.
        tables = set(inspect(conn).get_table_names())
        if {"threads", "steps"} <= tables:
            threads = (
                conn.execute(
                    text(
                        'SELECT "id", "name", "userIdentifier", "createdAt" FROM threads WHERE "userIdentifier" IS NOT NULL'
                    )
                )
                .mappings()
                .all()
            )
            for thread in threads:
                thread_id = str(thread["id"])
                exists = conn.execute(
                    text("SELECT id FROM imported_conversations WHERE id=:id"),
                    {"id": thread_id},
                ).first()
                if exists:
                    continue
                existing = conn.execute(
                    text("SELECT id FROM conversations WHERE id=:id"), {"id": thread_id}
                ).first()
                conn.execute(
                    text("INSERT INTO imported_conversations VALUES (:id)"),
                    {"id": thread_id},
                )
                if existing:
                    continue
                created = thread["createdAt"] or "1970-01-01T00:00:00Z"
                conn.execute(
                    text(
                        "INSERT INTO conversations (id,owner,title,created_at,updated_at) VALUES (:id,:owner,:title,:created,:created)"
                    ),
                    {
                        "id": thread_id,
                        "owner": thread["userIdentifier"],
                        "title": thread["name"] or "历史对话",
                        "created": created,
                    },
                )
                steps = conn.execute(
                    text(
                        'SELECT "id", "type", "output", "createdAt" FROM steps WHERE "threadId"=:id ORDER BY "createdAt", "id"'
                    ),
                    {"id": thread["id"]},
                ).mappings()
                for step in steps:
                    role = {
                        "user_message": "user",
                        "assistant_message": "assistant",
                    }.get(step["type"])
                    if role and step["output"]:
                        conn.execute(
                            text(
                                "INSERT INTO messages (id,conversation_id,role,content,created_at) VALUES (:id,:conversation,:role,:content,:created)"
                            ),
                            {
                                "id": str(step["id"]),
                                "conversation": thread_id,
                                "role": role,
                                "content": step["output"],
                                "created": step["createdAt"] or created,
                            },
                        )


def verify_user(username, password):
    with db() as conn:
        user = (
            conn.execute(
                text(
                    "SELECT username,password_hash,role,display_name FROM app_users WHERE username=:username"
                ),
                {"username": username},
            )
            .mappings()
            .first()
        )
    try:
        valid = user and bcrypt.checkpw(
            password.encode(), user["password_hash"].encode()
        )
    except (ValueError, TypeError):
        valid = False
    return dict(user) if valid else None


def new_session(username):
    token = secrets.token_urlsafe(32)
    with db() as conn:
        conn.execute(
            text("DELETE FROM web_sessions WHERE expires_at < :now"),
            {"now": int(time.time())},
        )
        conn.execute(
            text("INSERT INTO web_sessions VALUES (:hash,:username,:expires)"),
            {
                "hash": hashlib.sha256(token.encode()).hexdigest(),
                "username": username,
                "expires": int(time.time()) + 7 * 86400,
            },
        )
    return token


def session_user(token):
    if not token:
        return None
    with db() as conn:
        user = (
            conn.execute(
                text(
                    "SELECT u.username,u.role,u.display_name FROM web_sessions s JOIN app_users u ON u.username=s.username WHERE s.token_hash=:hash AND s.expires_at>:now"
                ),
                {
                    "hash": hashlib.sha256(token.encode()).hexdigest(),
                    "now": int(time.time()),
                },
            )
            .mappings()
            .first()
        )
        return dict(user) if user else None


def delete_session(token):
    if token:
        with db() as conn:
            conn.execute(
                text("DELETE FROM web_sessions WHERE token_hash=:hash"),
                {"hash": hashlib.sha256(token.encode()).hexdigest()},
            )


def now():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def uid():
    return str(uuid.uuid4())
