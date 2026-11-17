import sqlite3

import bcrypt
import httpx
import pytest
from sqlalchemy import text

from texttosql.web import storage
from texttosql.web.app import app


@pytest.fixture
async def client(tmp_path, monkeypatch):
    path = tmp_path / "app.db"
    monkeypatch.setattr(storage, "APP_DB_URI", f"sqlite:///{path}")
    monkeypatch.setattr(storage, "app_sqlite_path", lambda: path)
    storage.initialize()
    with storage.db() as conn:
        for name in ("alice", "bob"):
            hashed = bcrypt.hashpw(b"test-pass", bcrypt.gensalt()).decode()
            conn.execute(
                text(
                    "INSERT INTO app_users (username,password_hash,role,display_name) VALUES (:n,:p,:r,:d)"
                ),
                {"n": name, "p": hashed, "r": "scientist", "d": name.title()},
            )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as http:
        yield http, path


@pytest.mark.asyncio
async def test_login_query_history_isolation_and_logout(client, monkeypatch):
    http, path = client
    assert (await http.get("/api/conversations")).status_code == 401
    assert (
        await http.post("/api/login", json={"username": "alice", "password": "bad"})
    ).status_code == 401
    assert (
        await http.post(
            "/api/login", json={"username": "alice", "password": "test-pass"}
        )
    ).status_code == 200
    assert (
        "HttpOnly"
        in (
            await http.post(
                "/api/login", json={"username": "alice", "password": "test-pass"}
            )
        ).headers["set-cookie"]
    )
    session = (await http.post("/api/conversations")).json()

    async def fake_pipeline(question):
        return {
            "final_sql_query": "SELECT 1",
            "result_columns": ["value"],
            "execution_result": {"status": "success", "rows": [[1]], "row_count": 1},
        }

    monkeypatch.setattr("texttosql.web.app.run_pipeline", fake_pipeline)
    async def fake_description(question, columns, rows, row_count):
        return "一共有 1 条记录。", 1

    monkeypatch.setattr("texttosql.web.app.describe_result", fake_description)
    result = await http.post(
        f"/api/conversations/{session['id']}/messages", json={"content": "一共有多少？"}
    )
    assert result.json()["result"]["rows"] == [[1]]
    assert result.json()["result"]["description"] == "一共有 1 条记录。"
    history = (await http.get(f"/api/conversations/{session['id']}")).json()
    assert [m["role"] for m in history["messages"]] == ["user", "assistant"]
    assert history["messages"][1]["result"]["description"] == "一共有 1 条记录。"
    assert history["title"] == "一共有多少？"
    assert (
        await http.post(
            "/api/conversations", headers={"origin": "https://evil.example"}
        )
    ).status_code == 403
    await http.post("/api/logout")
    assert (await http.get("/api/me")).status_code == 401
    await http.post("/api/login", json={"username": "bob", "password": "test-pass"})
    assert (await http.get(f"/api/conversations/{session['id']}")).status_code == 404
    assert (await http.delete(f"/api/conversations/{session['id']}")).status_code == 404
    assert path.stat().st_mode & 0o777 == 0o600


@pytest.mark.asyncio
async def test_description_failure_keeps_successful_query_result(client, monkeypatch):
    http, _ = client
    await http.post("/api/login", json={"username": "alice", "password": "test-pass"})
    session = (await http.post("/api/conversations")).json()

    async def fake_pipeline(question):
        return {"final_sql_query": "SELECT 1", "result_columns": ["value"],
                "execution_result": {"status": "success", "rows": [[1]], "row_count": 1}}

    async def failed_description(*args):
        return None, 1

    monkeypatch.setattr("texttosql.web.app.run_pipeline", fake_pipeline)
    monkeypatch.setattr("texttosql.web.app.describe_result", failed_description)
    response = await http.post(f"/api/conversations/{session['id']}/messages", json={"content": "多少？"})
    result = response.json()["result"]
    assert result["success"] is True
    assert result["description"] is None
    assert result["sql"] == "SELECT 1"
    assert result["rows"] == [[1]]


def test_legacy_history_migration_is_idempotent_and_does_not_restore_deleted(
    tmp_path, monkeypatch
):
    path = tmp_path / "app.db"
    monkeypatch.setattr(storage, "APP_DB_URI", f"sqlite:///{path}")
    monkeypatch.setattr(storage, "app_sqlite_path", lambda: path)
    with sqlite3.connect(path) as db:
        db.executescript("""CREATE TABLE threads (id TEXT, name TEXT, "userIdentifier" TEXT, "createdAt" TEXT);
        CREATE TABLE steps (id TEXT, type TEXT, output TEXT, "createdAt" TEXT, "threadId" TEXT);
        INSERT INTO threads VALUES ('old','Old chat','alice','2026-01-01');
        INSERT INTO steps VALUES ('msg','user_message','Question','2026-01-01','old');""")
    storage.initialize()
    storage.initialize()
    with storage.db() as conn:
        assert conn.execute(text("SELECT count(*) FROM conversations")).scalar() == 1
        assert conn.execute(text("SELECT content FROM messages")).scalar() == "Question"
        conn.execute(text("DELETE FROM messages"))
        conn.execute(text("DELETE FROM conversations"))
    storage.initialize()
    with storage.db() as conn:
        assert conn.execute(text("SELECT count(*) FROM conversations")).scalar() == 0
