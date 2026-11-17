import asyncio
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from texttosql.evaluation.compare import compare_results
from texttosql.seed_sqlite import seed_database
from texttosql.web import evaluation


def result(rows, columns=None):
    return {"status": "success", "columns": columns or ["value"], "rows": rows}


def test_comparison_respects_duplicates_order_nulls_and_tolerance():
    expected = result([[1], [1], [None], [2.0]])
    assert (
        compare_results(expected, result([[None], [1], [2.0000000001], [1]]))[0]
        == "correct"
    )
    assert (
        compare_results(expected, result([[None], [1], [2.0], [2.0]]))[0] == "incorrect"
    )
    assert (
        compare_results(result([[1], [2]]), result([[2], [1]]), ordered=True)[0]
        == "incorrect"
    )
    assert (
        compare_results(result([[1]], ["a"]), result([[1, 2]], ["a", "b"]))[0]
        == "incorrect"
    )
    assert compare_results(result([[1]] * 201), result([[1]] * 201))[0] == "unknown"


def test_reference_queries_run_read_only_on_seed_database(tmp_path):
    path = tmp_path / "business.db"
    seed_database(path, Path(__file__).resolve().parents[1] / "seed_data.sql")
    conn = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    try:
        suite = evaluation._cases()
        assert len(suite["cases"]) == 20
        for case in suite["cases"]:
            rows = conn.execute(case["reference_sql"]).fetchall()
            assert len(rows) <= 200, case["id"]
        assert conn.execute(suite["cases"][0]["reference_sql"]).fetchone()[0] == 218
    finally:
        conn.close()


@pytest.fixture
async def client(tmp_path, monkeypatch):
    app_path = tmp_path / "app.db"
    sqlite3.connect(app_path).close()
    biz_path = tmp_path / "business.db"
    sqlite3.connect(biz_path).close()
    monkeypatch.setattr(evaluation, "APP_DB_URI", f"sqlite:///{app_path}")
    monkeypatch.setattr(evaluation, "DB_URI", f"sqlite:///{biz_path}")
    monkeypatch.setattr(evaluation, "DB_DIALECT", "sqlite")
    monkeypatch.setattr(evaluation, "app_sqlite_path", lambda: app_path)
    monkeypatch.setattr(evaluation, "_init_done", False)
    monkeypatch.setattr(
        evaluation,
        "_cases",
        lambda: {
            "name": "Test",
            "version": "1",
            "database": "SQLite",
            "cases": [
                {
                    "id": "1",
                    "category": "count",
                    "question": "Count?",
                    "reference_sql": "SELECT 2",
                }
            ],
        },
    )
    monkeypatch.setattr(evaluation, "_jobs", {})

    async def fake_mcp(name, args):
        return result([[2]])

    async def fake_pipeline(question):
        return {
            "sql_query": "SELECT 2",
            "final_sql_query": "SELECT 2",
            "iteration_count": 0,
            "execution_result": result([[2]]),
            "attempts": [{"sql": "SELECT 2"}],
        }

    monkeypatch.setattr(evaluation, "_mcp2cli_async", fake_mcp)
    monkeypatch.setattr(evaluation, "run_pipeline", fake_pipeline)
    app = FastAPI()
    app.include_router(evaluation.router)
    app.dependency_overrides[evaluation.get_current_user] = lambda: {
        "username": "alice"
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client, app


@pytest.mark.asyncio
async def test_run_report_history_and_user_isolation(client):
    http, app = client
    response = await http.post("/evaluation/api", json={"action": "start"})
    assert response.status_code == 200
    run_id = response.json()["run_id"]
    await asyncio.gather(*list(evaluation._jobs.values()))
    report = (
        await http.post("/evaluation/api", json={"action": "report", "run_id": run_id})
    ).json()
    assert report["status"] == "completed"
    assert report["completed"] == 1
    assert report["results"][0]["status"] == "correct"
    assert report["results"][0]["attempts"][0]["sql"] == "SELECT 2"
    app.dependency_overrides[evaluation.get_current_user] = lambda: {"username": "bob"}
    assert (
        await http.post("/evaluation/api", json={"action": "report", "run_id": run_id})
    ).status_code == 404
    app.dependency_overrides[evaluation.get_current_user] = lambda: None
    assert (
        await http.post("/evaluation/api", json={"action": "list"})
    ).status_code == 401


@pytest.mark.asyncio
async def test_cancel_and_non_sql_service_error(client, monkeypatch):
    http, _ = client
    started = asyncio.Event()

    async def slow_mcp(name, args):
        started.set()
        await asyncio.sleep(60)

    monkeypatch.setattr(evaluation, "_mcp2cli_async", slow_mcp)
    run_id = (await http.post("/evaluation/api", json={"action": "start"})).json()[
        "run_id"
    ]
    await started.wait()
    assert (
        await http.post("/evaluation/api", json={"action": "start"})
    ).status_code == 409
    task = evaluation._jobs[run_id]
    await http.post("/evaluation/api", json={"action": "stop", "run_id": run_id})
    await task
    report = (
        await http.post("/evaluation/api", json={"action": "report", "run_id": run_id})
    ).json()
    assert report["status"] == "stopped"
    assert report["completed"] == 0

    async def fail_mcp(name, args):
        return {"status": "error", "error_message": "unavailable"}

    monkeypatch.setattr(evaluation, "_mcp2cli_async", fail_mcp)
    run_id = (await http.post("/evaluation/api", json={"action": "start"})).json()[
        "run_id"
    ]
    await asyncio.gather(*list(evaluation._jobs.values()))
    report = (
        await http.post("/evaluation/api", json={"action": "report", "run_id": run_id})
    ).json()
    assert report["results"][0]["status"] == "unknown"


@pytest.mark.asyncio
async def test_workflow_records_initial_and_corrected_sql(monkeypatch):
    from texttosql import nodes
    from texttosql.graph import run_pipeline

    async def fake_mcp(name, args=None):
        if name == "load_schema":
            return {
                "status": "success",
                "ddl": "CREATE TABLE items (id INT)",
                "sqlglot_schema": {"items": {"id": "INT"}},
            }
        if name == "validate_sql":
            if "missing" in args["sql_query"]:
                return {
                    "status": "error",
                    "error_type": "sql",
                    "errors": ["unknown column"],
                }
            return {"status": "success"}
        return {"status": "success", "columns": ["id"], "rows": [[1]], "row_count": 1}

    class FakeModel:
        def __init__(self):
            self.calls = 0

        async def ainvoke(self, messages):
            self.calls += 1
            return SimpleNamespace(
                content="SELECT missing FROM items"
                if self.calls == 1
                else "SELECT id FROM items",
                response_metadata={},
            )

    model = FakeModel()
    monkeypatch.setattr(nodes, "_mcp2cli_async", fake_mcp)
    monkeypatch.setattr(nodes, "get_llm", lambda: model)
    state = await run_pipeline("查询编号")
    assert state["iteration_count"] == 1
    assert len(state["attempts"]) == 2
    assert state["attempts"][0]["validation"]["error_type"] == "sql"
    assert state["attempts"][1]["execution"]["status"] == "success"
    assert state["final_sql_query"] == "SELECT id FROM items;"


@pytest.mark.asyncio
async def test_stop_immediately_after_start(client, monkeypatch):
    async def slow_mcp(name, args):
        await asyncio.sleep(60)

    monkeypatch.setattr(evaluation, "_mcp2cli_async", slow_mcp)
    http, _ = client
    run_id = (await http.post("/evaluation/api", json={"action": "start"})).json()[
        "run_id"
    ]
    assert (
        await http.post("/evaluation/api", json={"action": "stop", "run_id": run_id})
    ).status_code == 200
    report = (
        await http.post("/evaluation/api", json={"action": "report", "run_id": run_id})
    ).json()
    assert report["status"] == "stopped"
    assert run_id not in evaluation._jobs
