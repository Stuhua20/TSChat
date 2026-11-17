import asyncio
import json
import sqlite3
import sys
from pathlib import Path

import pytest
from langgraph.graph import END

from texttosql import mcp_server, nodes
from texttosql.dialects.sqlite import SQLiteDialect
from texttosql.graph import _route_after_execute, _route_after_validate, run_pipeline


@pytest.fixture
def business_db(tmp_path: Path, monkeypatch):
    path = tmp_path / "business.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE items (id INTEGER)")
        conn.execute("INSERT INTO items VALUES (1)")
    monkeypatch.setattr(mcp_server, "DB_URI", f"sqlite:///{path}")
    monkeypatch.setattr(mcp_server, "get_dialect", SQLiteDialect)
    return path


@pytest.mark.parametrize("query", [
    "DELETE FROM items RETURNING id",
    "SELECT id FROM items; DELETE FROM items RETURNING id",
    "WITH x AS (DELETE FROM items RETURNING id) SELECT * FROM x",
    "SELECT id INTO copy FROM items",
])
def test_execution_entry_rejects_writes(business_db, query):
    result = json.loads(mcp_server.execute_sql(query))
    assert result["status"] == "error"
    assert result["error_type"] in ("policy", "sql")
    with sqlite3.connect(business_db) as conn:
        assert conn.execute("SELECT id FROM items").fetchall() == [(1,)]


def test_execution_entry_allows_read_query(business_db):
    result = json.loads(mcp_server.execute_sql("SELECT id FROM items"))
    assert result["status"] == "success"
    assert result["rows"] == [[1]]


def test_sqlite_query_timeout(business_db, monkeypatch):
    monkeypatch.setattr(mcp_server, "DB_QUERY_TIMEOUT", 0)
    result = json.loads(mcp_server.execute_sql(
        "SELECT COUNT(*) FROM items a CROSS JOIN items b"
    ))
    if result["status"] == "success":
        result = json.loads(mcp_server.execute_sql(
            "WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM n WHERE x < 1000000) SELECT SUM(x) FROM n"
        ))
    assert result["error_type"] == "timeout"


def test_postgres_connection_sets_readonly_and_deadline(monkeypatch):
    psycopg2 = pytest.importorskip("psycopg2")
    from texttosql.dialects.postgres import PostgreSQLDialect

    class Cursor:
        def __init__(self):
            self.calls = []
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def execute(self, *args):
            self.calls.append(args)

    class Connection:
        def __init__(self):
            self.cur = Cursor()
            self.readonly = False
            self.closed = False
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def set_session(self, *, readonly):
            self.readonly = readonly
        def cursor(self):
            return self.cur
        def close(self):
            self.closed = True

    conn = Connection()
    monkeypatch.setattr(psycopg2, "connect", lambda uri, connect_timeout: conn)
    with PostgreSQLDialect().get_connection("postgresql://localhost/test") as actual:
        assert actual is conn
        assert conn.readonly
    assert conn.cur.calls[0] == ("SET LOCAL statement_timeout = %s", (15000,))
    assert conn.closed


@pytest.mark.parametrize("error_type", ["service", "policy", "timeout"])
def test_non_sql_errors_never_route_to_model(error_type):
    state = {"iteration_count": 0, "validation_result": {"status": "error", "error_type": error_type},
             "execution_result": {"status": "error", "error_type": error_type}}
    assert _route_after_validate(state) == END
    assert _route_after_execute(state) == END


def test_sql_errors_still_route_to_correction():
    state = {"iteration_count": 0, "validation_result": {"status": "error", "error_type": "sql"},
             "execution_result": {"status": "error", "error_type": "sql"}}
    assert _route_after_validate(state) == "correct_sql"
    assert _route_after_execute(state) == "correct_sql"


@pytest.mark.asyncio
async def test_model_timeout_stops_pipeline(monkeypatch):
    async def schema(*args):
        return {"status": "success", "ddl": "CREATE TABLE items (id INT)", "sqlglot_schema": {"items": {"id": "INT"}}}
    class Model:
        async def ainvoke(self, messages):
            await asyncio.sleep(10)
    monkeypatch.setattr(nodes, "_mcp2cli_async", schema)
    monkeypatch.setattr(nodes, "get_llm", Model)
    monkeypatch.setattr(nodes, "LLM_TIMEOUT", 0.01)
    state = await run_pipeline("Count items")
    assert "超时" in state["error"]
    assert state["iteration_count"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_mcp_subprocess_is_stopped_on_timeout_or_cancel(monkeypatch, cancel):
    original = asyncio.create_subprocess_exec
    processes = []
    async def spawn(*args, **kwargs):
        process = await original(sys.executable, "-c", "import time; time.sleep(30)", **kwargs)
        processes.append(process)
        return process
    monkeypatch.setattr(nodes.asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(nodes.shutil, "which", lambda name: sys.executable)
    monkeypatch.setattr(nodes, "MCP_TIMEOUT", 0.2)
    task = asyncio.create_task(nodes._mcp2cli_async("load_schema"))
    while not processes:
        await asyncio.sleep(0.001)
    if cancel:
        task.cancel()
    with pytest.raises(asyncio.CancelledError if cancel else TimeoutError):
        await task
    assert processes[0].returncode is not None
