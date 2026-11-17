import asyncio
import os
import socket
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from texttosql import config, nodes
from texttosql.dialects import factory
from texttosql.dialects.engine import SQLValidator
from texttosql.dialects.sqlite import SQLiteDialect
from texttosql.graph import run_pipeline
from texttosql.seed_sqlite import seed_database


def sqlite_url(path: Path) -> str:
    return f"sqlite:///{path}"


@pytest.fixture
def sample_db(tmp_path: Path) -> Path:
    path = tmp_path / "business.db"
    assert seed_database(path, config.PROJECT_ROOT / "seed_data.sql") == 218
    return path


def test_sample_import_schema_validation_and_read_only(sample_db: Path) -> None:
    dialect = SQLiteDialect()
    url = sqlite_url(sample_db)
    schema = dialect.get_sqlglot_schema(url)
    assert list(schema) == ["eln_writeup_anonymized"]
    assert schema["eln_writeup_anonymized"]["summary_data"] == "TEXT"
    query = "SELECT COUNT(*) FROM eln_writeup_anonymized WHERE json_valid(summary_data)"
    assert SQLValidator().validate(query, dialect, schema)["status"] == "success"
    with dialect.get_connection(url) as conn:
        assert conn.execute(query).fetchone()[0] == 218
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("DELETE FROM eln_writeup_anonymized")
    with pytest.raises(sqlite3.ProgrammingError):
        conn.execute("SELECT 1")  # The adapter closes each connection.


def test_seed_refuses_to_overwrite_existing_file(sample_db: Path) -> None:
    original = sample_db.read_bytes()
    with pytest.raises(FileExistsError):
        seed_database(sample_db, config.PROJECT_ROOT / "seed_data.sql")
    assert sample_db.read_bytes() == original


def test_sqlite_schema_handles_unusual_identifiers_and_missing_file(
    tmp_path: Path,
) -> None:
    path = tmp_path / "custom.db"
    with sqlite3.connect(path) as conn:
        conn.execute('CREATE TABLE "a;table" ("text column" VARCHAR(20), n INTEGER)')
    dialect = SQLiteDialect()
    assert dialect.get_sqlglot_schema(sqlite_url(path))["a;table"] == {
        "text column": "TEXT",
        "n": "INTEGER",
    }
    missing = tmp_path / "missing.db"
    with pytest.raises(sqlite3.OperationalError):
        SQLiteDialect().get_ddl(sqlite_url(missing))
    assert not missing.exists()


def test_database_paths_and_prompt_follow_selected_dialect(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    assert (
        config.sqlite_path("sqlite:///data/business.db")
        == tmp_path / "data/business.db"
    )
    monkeypatch.setattr(config, "DB_DIALECT", "sqlite")
    monkeypatch.setattr(config, "DB_URI", "sqlite:///same.db")
    monkeypatch.setattr(config, "APP_DB_URI", "sqlite:///same.db")
    with pytest.raises(ValueError, match="different SQLite files"):
        config.app_sqlite_path()
    schema = {"eln_writeup_anonymized": {"summary_data": "TEXT"}}
    sqlite_hints = nodes._build_json_hints_block(schema, "sqlite")
    postgres_hints = nodes._build_json_hints_block(schema, "postgresql")
    assert "json_extract(summary_data, '$.KEY')" in sqlite_hints
    assert "summary_data::jsonb->>'KEY'" in postgres_hints
    assert nodes._build_json_hints_block({"other": {}}, "sqlite") == ""


def test_schema_failure_does_not_call_model(monkeypatch) -> None:
    async def fail_schema(*args, **kwargs):
        raise RuntimeError("business database is missing")

    def unexpected_model():
        raise AssertionError("the model must not be called without a schema")

    monkeypatch.setattr(nodes, "_mcp2cli_async", fail_schema)
    monkeypatch.setattr(nodes, "get_llm", unexpected_model)
    state = asyncio.run(run_pipeline("How many records?"))
    assert "business database is missing" in state["error"]
    assert state["sql_query"] is None


def test_postgresql_dialect_remains_selectable(monkeypatch) -> None:
    pytest.importorskip("psycopg2")
    monkeypatch.setattr(factory, "DB_DIALECT", "postgresql")
    dialect = factory.get_dialect()
    assert dialect.name == "postgresql"
    assert dialect.get_sqlglot_dialect() == "postgres"


@pytest.mark.skipif(
    os.getenv("RUN_MCP_INTEGRATION") != "1",
    reason="set RUN_MCP_INTEGRATION=1 to allow a localhost MCP server",
)
def test_real_mcp_graph_and_correction(
    sample_db: Path, tmp_path: Path, monkeypatch
) -> None:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = os.environ.copy()
    env.update(
        {
            "DB_DIALECT": "sqlite",
            "DB_URI": sqlite_url(sample_db),
            "APP_DB_URI": sqlite_url(tmp_path / "app.db"),
            "MCP_HOST": "127.0.0.1",
            "MCP_SERVER_PORT": str(port),
            "MCP2CLI_CACHE_DIR": str(tmp_path / "mcp-cache"),
            "PATH": f"{Path(sys.executable).parent}{os.pathsep}{env.get('PATH', '')}",
        }
    )
    process = subprocess.Popen(
        [sys.executable, "-m", "texttosql.mcp_server"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 20
        while True:
            if process.poll() is not None:
                pytest.fail("MCP server exited before accepting connections")
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    break
            except OSError:
                if time.monotonic() >= deadline:
                    pytest.fail("MCP server did not start")
                time.sleep(0.1)

        class FakeModel:
            calls = 0

            async def ainvoke(self, messages):
                self.calls += 1
                assert "SQLite" in messages[0].content
                if self.calls == 1:
                    sql = "SELECT unknown_column FROM eln_writeup_anonymized"
                else:
                    sql = (
                        "SELECT\n COUNT(*) AS total\nFROM eln_writeup_anonymized\n"
                        "WHERE json_valid(summary_data)"
                    )
                return SimpleNamespace(content=sql, response_metadata={})

        model = FakeModel()
        monkeypatch.setattr(nodes, "MCP_SERVER_URL", f"http://127.0.0.1:{port}/mcp")
        monkeypatch.setattr(nodes, "DB_DIALECT", "sqlite")
        monkeypatch.setattr(nodes, "get_llm", lambda: model)
        monkeypatch.setenv("MCP2CLI_CACHE_DIR", str(tmp_path / "mcp-cache"))
        monkeypatch.setenv("PATH", env["PATH"])
        state = asyncio.run(run_pipeline("Count experiments"))
        assert state["iteration_count"] == 1
        assert model.calls == 2
        assert state["execution_result"]["rows"] == [[218]]
    finally:
        process.terminate()
        process.wait(timeout=10)
