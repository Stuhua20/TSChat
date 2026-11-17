"""Isolated, reproducible SQLite fixtures and read-only evaluation tools."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import sqlite3
import time
from pathlib import Path

from texttosql.config import DB_QUERY_TIMEOUT, PROJECT_ROOT
from texttosql.dialects.engine import SQLValidator
from texttosql.dialects.sqlite import SQLiteDialect
from texttosql.evaluation.compare import MAX_ROWS
from texttosql.seed_sqlite import seed_database

FIXTURE_VERSION = "eln-seed-boundaries-v1"
SEED_PATH = PROJECT_ROOT / "seed_data.sql"
logger = logging.getLogger(__name__)


def fixture_metadata(suite: dict, seed_data: bytes) -> dict:
    return {
        "fixture_version": FIXTURE_VERSION,
        "seed_sha256": hashlib.sha256(seed_data).hexdigest(),
        "suite_sha256": hashlib.sha256(
            json.dumps(suite, ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest(),
    }


def create_fixtures(
    directory: Path, seed_data: bytes | None = None
) -> tuple[Path, Path]:
    """Build fixtures from versioned seed data, never from the live business DB."""
    baseline, boundary = directory / "baseline.db", directory / "boundary.db"
    seed_path = SEED_PATH
    if seed_data is not None:
        seed_path = directory / "seed.sql"
        seed_path.write_bytes(seed_data)
    seed_database(baseline, seed_path)
    with sqlite3.connect(baseline) as source, sqlite3.connect(boundary) as target:
        source.backup(target)
        target.executemany(
            "INSERT INTO eln_writeup_anonymized VALUES (?, ?, ?, ?, ?)",
            [
                (
                    "eval-boundary-2",
                    "AetherGen",
                    None,
                    '{"STATUS":"0","PDF_COMPLETE":"Y"}',
                    "2025-01-31",
                ),
                (
                    "eval-boundary-2",
                    "AetherGen",
                    "",
                    '{"STATUS":"1","PDF_COMPLETE":"N"}',
                    "2025-02-01",
                ),
                (
                    "eval-boundary-3",
                    "masks",
                    "boundary",
                    '{"STATUS":"0"}',
                    "2025-02-28",
                ),
                ("eval-boundary-3", "masks", "boundary", "{}", "2025-03-01"),
                ("eval-boundary-3", None, None, None, None),
            ],
        )
        target.commit()
    return baseline, boundary


class FixtureTools:
    def __init__(self, path: Path):
        self.uri = f"sqlite:///{path}"
        self.dialect = SQLiteDialect()

    async def __call__(self, name: str, args: dict) -> dict:
        return await asyncio.to_thread(self._call, name, args)

    def _call(self, name: str, args: dict) -> dict:
        try:
            schema = self.dialect.get_sqlglot_schema(self.uri)
            if name == "load_schema":
                return {
                    "status": "success",
                    "ddl": self.dialect.get_ddl(self.uri),
                    "sqlglot_schema": schema,
                }
            if name not in ("validate_sql", "execute_sql"):
                raise ValueError("Unknown evaluation tool")
            sql = args["sql_query"]
            validation = SQLValidator().validate(sql, self.dialect, schema)
            if name == "validate_sql":
                return validation
            if validation.get("status") != "success":
                return {
                    "status": "error",
                    "error_type": validation.get("error_type", "service"),
                    "error_message": "; ".join(validation.get("errors", [])),
                }
            with self.dialect.get_connection(self.uri) as conn:
                deadline = time.monotonic() + DB_QUERY_TIMEOUT
                conn.set_progress_handler(
                    lambda: int(time.monotonic() >= deadline), 1000
                )
                cursor = conn.execute(sql)
                rows = [list(row) for row in cursor.fetchmany(MAX_ROWS + 1)]
                return {
                    "status": "success",
                    "columns": [col[0] for col in cursor.description],
                    "rows": rows,
                    "row_count": len(rows),
                }
        except sqlite3.Error as exc:
            code = getattr(exc, "sqlite_errorcode", None)
            kind = (
                "timeout"
                if code == sqlite3.SQLITE_INTERRUPT
                else "sql"
                if code == sqlite3.SQLITE_ERROR
                else "service"
            )
            return {"status": "error", "error_type": kind, "error_message": str(exc)}
        except Exception:
            logger.exception("Evaluation fixture tool failed")
            return {
                "status": "error",
                "error_type": "service",
                "error_message": "评测数据库服务不可用。",
            }
