"""
MCP 服务 — Text-to-SQL 工具
============================
在一个终端启动：  python -m texttosql.mcp_server
在另一个终端运行智能体： python -m texttosql.main "有多少个实验？"
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import sys
import time
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from fastmcp import FastMCP

from texttosql.config import DB_QUERY_TIMEOUT, DB_URI
from texttosql.dialects.engine import SQLExecutor, SQLValidator
from texttosql.dialects.factory import get_dialect

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(levelname)s [mcp_server] %(message)s",
)
logger = logging.getLogger(__name__)

MCP_HOST = os.environ.get("MCP_HOST", "127.0.0.1")
MCP_PORT = int(os.environ.get("MCP_SERVER_PORT", "3001"))


def _json_serial(obj: Any) -> str:
    """json.dumps 无法原生序列化的类型的回退序列化函数。"""
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return float(obj)
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")

# ---------------------------------------------------------------------------
# FastMCP 应用
# ---------------------------------------------------------------------------
mcp = FastMCP(
    name="texttosql-tools",
    instructions="SQL schema introspection, validation and execution for SQLite or PostgreSQL.",
)

# ---------------------------------------------------------------------------
# 工具：load_schema
# ---------------------------------------------------------------------------

@mcp.tool()
def load_schema() -> str:
    """
    检查已配置的数据库并返回其 schema。

    返回一个 JSON 对象：
      - ddl:            展示给 LLM 的 CREATE TABLE DDL
      - sqlglot_schema: {表: {列: 类型}}，用于校验
    """
    try:
        dialect = get_dialect()
        logger.info("load_schema: dialect=%s", dialect.name)
        ddl = dialect.get_ddl(DB_URI)
        sqlglot_schema: dict[str, Any] = dialect.get_sqlglot_schema(DB_URI)
        return json.dumps({"status": "success", "ddl": ddl, "sqlglot_schema": sqlglot_schema})
    except Exception:
        logger.exception("load_schema failed")
        return json.dumps({"status": "error", "error": "无法读取数据库结构，请检查数据库连接和权限。"})


# ---------------------------------------------------------------------------
# 工具：validate_sql
# ---------------------------------------------------------------------------

@mcp.tool()
def validate_sql(sql_query: str) -> str:
    """
    校验 SQL 查询的语法与语义正确性。

    参数：
        sql_query: 待校验的 SQL 语句。

    返回 {"status": "success"} 或 {"status": "error", "errors": [...]}
    """
    try:
        dialect = get_dialect()
        sqlglot_schema = dialect.get_sqlglot_schema(DB_URI)
        validator = SQLValidator()
        result = validator.validate(sql_query, dialect, sqlglot_schema)
        return json.dumps(result)
    except Exception:
        logger.exception("validate_sql failed")
        return json.dumps({"status": "error", "error_type": "service", "errors": ["SQL 校验服务不可用，请检查数据库连接。"]})


# ---------------------------------------------------------------------------
# 工具：execute_sql
# ---------------------------------------------------------------------------

@mcp.tool()
def execute_sql(sql_query: str) -> str:
    """
    在已配置的数据库上执行 SQL 查询。

    参数：
        sql_query: 一条有效的 SQL 语句。

    返回 {"status": "success", "columns": [...], "rows": [...], "row_count": N}
      或 {"status": "error", "error_message": "..."}
    """
    try:
        dialect = get_dialect()
        schema = dialect.get_sqlglot_schema(DB_URI)
        validation = SQLValidator().validate(sql_query, dialect, schema)
        if validation.get("status") != "success":
            return json.dumps({
                "status": "error", "error_type": validation.get("error_type", "service"),
                "error_message": "; ".join(validation.get("errors", [])),
            })
        with dialect.get_connection(DB_URI) as conn:
            if dialect.name == "sqlite":
                deadline = time.monotonic() + DB_QUERY_TIMEOUT
                conn.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
            cursor = conn.cursor()
            cursor.execute(sql_query)
            columns = [desc[0] for desc in cursor.description] if cursor.description else []
            rows = cursor.fetchall()
            serialisable = [list(row) for row in rows]
            return json.dumps({
                "status": "success",
                "columns": columns,
                "rows": serialisable,
                "row_count": len(serialisable),
            }, default=_json_serial)
    except Exception as exc:
        logger.exception("execute_sql failed")
        error_type = "service"
        code = getattr(exc, "pgcode", None)
        sqlite_code = getattr(exc, "sqlite_errorcode", None)
        if code == "57014" or sqlite_code == sqlite3.SQLITE_INTERRUPT:
            error_type = "timeout"
        elif code == "42501" or sqlite_code in (sqlite3.SQLITE_AUTH, sqlite3.SQLITE_READONLY, sqlite3.SQLITE_PERM):
            error_type = "policy"
        elif (code and code[:2] in ("22", "42")) or sqlite_code == sqlite3.SQLITE_ERROR:
            error_type = "sql"
        messages = {
            "service": "数据库服务不可用，请检查连接和配置。",
            "timeout": "数据库查询超时，请缩小查询范围后重试。",
            "policy": "数据库拒绝了查询，请检查只读策略和账号权限。",
        }
        return json.dumps({"status": "error", "error_type": error_type, "error_message": messages.get(error_type, str(exc))})


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logger.info("TextToSQL MCP server → http://%s:%s/mcp", MCP_HOST, MCP_PORT)
    mcp.run(transport="streamable-http", host=MCP_HOST, port=MCP_PORT)
