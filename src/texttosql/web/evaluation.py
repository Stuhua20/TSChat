"""Authenticated local evaluation jobs."""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import tempfile
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from texttosql.config import (
    APP_DB_URI,
    DB_DIALECT,
    MAX_CORRECTION_ITERATIONS,
    OPENAI_MODEL,
    app_sqlite_path,
)
from texttosql.evaluation.compare import compare_results
from texttosql.evaluation.fixture import (
    SEED_PATH,
    FixtureTools,
    create_fixtures,
    fixture_metadata,
)
from texttosql.graph import run_pipeline
from texttosql.nodes import _mcp2cli_async, evaluation_tools

from .auth import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter()
CASES_PATH = Path(__file__).resolve().parents[1] / "evaluation/cases.json"
_jobs: dict[str, asyncio.Task] = {}
_init_done = False


class Request(BaseModel):
    action: str
    run_id: str | None = None


def _user(user: Any) -> str:
    if not user or not user.get("username"):
        raise HTTPException(401, "请先登录")
    return user["username"]


@contextmanager
def _db():
    if not APP_DB_URI.startswith("sqlite:///") or DB_DIALECT != "sqlite":
        raise HTTPException(400, "评测功能目前仅支持 SQLite 业务库和 SQLite 应用库")
    path = app_sqlite_path()
    if not path.is_file():
        raise HTTPException(503, "请先初始化应用数据库")
    conn = sqlite3.connect(f"{path.as_uri()}?mode=rw", uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def _init() -> None:
    global _init_done
    if _init_done:
        return
    with _db() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS evaluation_runs (
            id TEXT PRIMARY KEY, owner TEXT NOT NULL, status TEXT NOT NULL,
            created_at REAL NOT NULL, finished_at REAL, name TEXT NOT NULL,
            version TEXT NOT NULL, model TEXT NOT NULL, total INTEGER NOT NULL,
            completed INTEGER NOT NULL DEFAULT 0, database_before TEXT,
            database_after TEXT, warning TEXT
        );
        CREATE TABLE IF NOT EXISTS evaluation_results (
            run_id TEXT NOT NULL, position INTEGER NOT NULL, case_id TEXT NOT NULL,
            category TEXT NOT NULL, payload TEXT NOT NULL,
            PRIMARY KEY (run_id, position),
            FOREIGN KEY (run_id) REFERENCES evaluation_runs(id) ON DELETE CASCADE
        );
        """)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(evaluation_runs)")}
        if "metadata" not in columns:
            conn.execute("ALTER TABLE evaluation_runs ADD COLUMN metadata TEXT")
        # This process keeps its tasks in memory. A process restart interrupts them.
        conn.execute(
            "UPDATE evaluation_runs SET status='interrupted', finished_at=? WHERE status IN ('running', 'stopping')",
            (time.time(),),
        )
    _init_done = True


def _cases() -> dict:
    return json.loads(CASES_PATH.read_text(encoding="utf-8"))


def _save_result(run_id: str, position: int, case: dict, payload: dict) -> None:
    with _db() as conn:
        conn.execute(
            "INSERT INTO evaluation_results VALUES (?, ?, ?, ?, ?)",
            (
                run_id,
                position,
                case["id"],
                case["category"],
                json.dumps(payload, ensure_ascii=False),
            ),
        )
        conn.execute(
            "UPDATE evaluation_runs SET completed=completed+1 WHERE id=?", (run_id,)
        )


def _display_result(result: dict) -> dict:
    """Keep reports compact without affecting the full-result comparison."""
    rows = result.get("rows", [])
    return {**result, "rows": rows[:50], "truncated": len(rows) > 50}


async def _evaluate(case: dict, baseline: FixtureTools, boundary: FixtureTools) -> dict:
    result = {
        "id": case["id"],
        "category": case["category"],
        "question": case["question"],
        "reference_sql": case["reference_sql"],
        "status": "unknown",
        "reason": "",
        "attempts": [],
    }
    started = time.monotonic()
    token = evaluation_tools.set(baseline)
    try:
        expected = await _mcp2cli_async(
            "execute_sql", {"sql_query": case["reference_sql"]}
        )
        if expected.get("status") != "success":
            result["reason"] = "参考 SQL 执行失败：" + expected.get(
                "error_message", "未知错误"
            )
            return result
        result["expected"] = _display_result(expected)
        state = await run_pipeline(case["question"])
        result["attempts"] = state.get("attempts", [])
        result["corrections"] = state.get("iteration_count", 0)
        result["sql"] = state.get("final_sql_query") or state.get("sql_query")
        actual = state.get("execution_result") or {}
        if actual.get("status") == "success" and state.get("final_sql_query"):
            result["actual"] = _display_result(actual)
            result["status"], result["reason"] = compare_results(
                expected, actual, case.get("ordered", False)
            )
            if result["status"] != "unknown":
                edge_expected = await boundary(
                    "execute_sql", {"sql_query": case["reference_sql"]}
                )
                edge_actual = await boundary(
                    "execute_sql", {"sql_query": result["sql"]}
                )
                if (
                    edge_expected.get("status")
                    == edge_actual.get("status")
                    == "success"
                ):
                    result["boundary_status"], result["boundary_reason"] = (
                        compare_results(
                            edge_expected, edge_actual, case.get("ordered", False)
                        )
                    )
                else:
                    result["boundary_status"] = "unknown"
                    result["boundary_reason"] = "边界数据上的 SQL 执行失败"
        else:
            validation = state.get("validation_result") or {}
            error_type = actual.get("error_type") or validation.get("error_type")
            result["status"] = (
                "query_failed"
                if not state.get("error") and error_type in ("sql", "policy")
                else "unknown"
            )
            result["reason"] = (
                state.get("error")
                or actual.get("error_message")
                or "; ".join(validation.get("errors", []))
                or "查询未完成"
            )
        attempts = result["attempts"]
        if attempts:
            first = attempts[0]
            if first.get("execution", {}).get("status") == "success":
                result["first_status"] = (
                    result["status"] if len(attempts) == 1 else "unknown"
                )
            elif first.get("validation", {}).get("error_type") in (
                "sql",
                "policy",
            ) or first.get("execution", {}).get("error_type") in ("sql", "policy"):
                result["first_status"] = "query_failed"
            else:
                result["first_status"] = "unknown"
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Evaluation case failed")
        result["reason"] = "评测服务异常，请检查服务日志。"
    finally:
        result["elapsed_seconds"] = round(time.monotonic() - started, 3)
        evaluation_tools.reset(token)
    return result


async def _run(run_id: str, cases: list[dict], seed_data: bytes) -> None:
    status, warning = "completed", None
    try:
        with tempfile.TemporaryDirectory(prefix="texttosql-eval-") as directory:
            baseline_path, boundary_path = create_fixtures(Path(directory), seed_data)
            baseline, boundary = (
                FixtureTools(baseline_path),
                FixtureTools(boundary_path),
            )
            for position, case in enumerate(cases):
                payload = await _evaluate(case, baseline, boundary)
                _save_result(run_id, position, case, payload)
    except asyncio.CancelledError:
        status = "stopped"
    except Exception:
        logger.exception("Evaluation job failed")
        status, warning = "interrupted", "评测任务异常中断，请检查服务日志。"
    finally:
        try:
            with _db() as conn:
                conn.execute(
                    "UPDATE evaluation_runs SET status=?, finished_at=?, warning=? WHERE id=?",
                    (status, time.time(), warning, run_id),
                )
        finally:
            _jobs.pop(run_id, None)


def _report(conn: sqlite3.Connection, run: sqlite3.Row) -> dict:
    data = dict(run)
    data["metadata"] = json.loads(data["metadata"]) if data.get("metadata") else None
    data["results"] = [
        json.loads(row[0])
        for row in conn.execute(
            "SELECT payload FROM evaluation_results WHERE run_id=? ORDER BY position",
            (run["id"],),
        )
    ]
    return data


@router.post("/evaluation/api")
async def evaluation_api(
    body: Request, current_user: Annotated[Any, Depends(get_current_user)]
) -> dict:
    owner = _user(current_user)
    _init()
    if body.action == "list":
        suite = _cases()
        with _db() as conn:
            runs = [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM evaluation_runs WHERE owner=? ORDER BY created_at DESC LIMIT 50",
                    (owner,),
                )
            ]
        return {
            "suite": {k: suite[k] for k in ("name", "version", "database")},
            "count": len(suite["cases"]),
            "runs": runs,
        }
    if body.action == "start":
        suite = _cases()
        try:
            seed_data = SEED_PATH.read_bytes()
            metadata = fixture_metadata(suite, seed_data)
        except OSError as exc:
            raise HTTPException(
                503, "评测数据缺失，请检查 seed_data.sql 和题集文件"
            ) from exc
        metadata["max_corrections"] = MAX_CORRECTION_ITERATIONS
        with _db() as conn:
            existing = conn.execute(
                "SELECT id FROM evaluation_runs WHERE owner=? AND status IN ('running','stopping')",
                (owner,),
            ).fetchone()
            if existing:
                raise HTTPException(409, "已有评测正在运行")
            run_id = uuid.uuid4().hex
            conn.execute(
                "INSERT INTO evaluation_runs (id,owner,status,created_at,name,version,model,total,metadata) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    run_id,
                    owner,
                    "running",
                    time.time(),
                    suite["name"],
                    suite["version"],
                    OPENAI_MODEL,
                    len(suite["cases"]),
                    json.dumps(metadata),
                ),
            )
        _jobs[run_id] = asyncio.create_task(_run(run_id, suite["cases"], seed_data))
        return {"run_id": run_id}
    if not body.run_id:
        raise HTTPException(400, "缺少评测编号")
    with _db() as conn:
        run = conn.execute(
            "SELECT * FROM evaluation_runs WHERE id=? AND owner=?", (body.run_id, owner)
        ).fetchone()
        if not run:
            raise HTTPException(404, "评测记录不存在")
        if body.action == "report":
            return _report(conn, run)
        if body.action == "stop":
            task = _jobs.get(body.run_id)
            if task and not task.done():
                conn.execute(
                    "UPDATE evaluation_runs SET status='stopping' WHERE id=?",
                    (body.run_id,),
                )
    if body.action == "stop":
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            # A task cancelled before its first instruction cannot run its finally block.
            with _db() as conn:
                conn.execute(
                    "UPDATE evaluation_runs SET status='stopped', finished_at=? WHERE id=? AND status='stopping'",
                    (time.time(), body.run_id),
                )
            _jobs.pop(body.run_id, None)
        return {"ok": True}
    raise HTTPException(400, "未知操作")
