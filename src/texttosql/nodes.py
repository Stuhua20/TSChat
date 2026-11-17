"""
LangGraph 节点 — 每个节点都是一个普通的 async 函数，读取 GraphState
并返回一个部分状态更新字典。

MCP 工具通过 mcp2cli 子进程调用，而不是 langchain-mcp-adapters。
这样避免向 LLM 上下文注入工具 schema，并彻底移除
MultiServerMCPClient 的额外开销。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from .config import (
    DB_DIALECT,
    JSON_COLUMN_HINTS,
    LLM_TIMEOUT,
    MCP_SERVER_URL,
    MCP_TIMEOUT,
)
from .llm_factory import get_llm
from .state import GraphState

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# mcp2cli 辅助函数
# ---------------------------------------------------------------------------

async def _mcp2cli_async(tool_name: str, args: dict[str, str] | None = None) -> dict[str, Any]:
    """
    通过 mcp2cli 调用一个 MCP 工具，并返回解析后的 JSON 结果。

    使用 streamable 传输方式，并在超时或取消时结束子进程。

    如果 mcp2cli 未安装或调用失败，抛出 RuntimeError。
    """
    if shutil.which("mcp2cli") is None:
        raise RuntimeError(
            "mcp2cli not found. Install it with: pip install mcp2cli"
        )

    cmd = [
        "mcp2cli",
        "--mcp", MCP_SERVER_URL,
        "--transport", "streamable",
        tool_name.replace("_", "-"),  # load_schema → load-schema
    ]

    stdin_data = None
    if args:
        for key, value in args.items():
            kebab_key = key.replace("_", "-")  # sql_query → sql-query
            # 对于较长/多行的值（例如 SQL），通过 --stdin 以 JSON 传递，
            # 避免shell 引号转义问题
            if "\n" in value or len(value) > 200:
                import json as _json
                stdin_data = _json.dumps({key: value})
                cmd.append("--stdin")
            else:
                cmd.extend([f"--{kebab_key}", value])

    logger.debug("[mcp2cli] 执行: %s", " ".join(cmd))
    process = await asyncio.create_subprocess_exec(
        *cmd, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(stdin_data.encode() if stdin_data else None), MCP_TIMEOUT
        )
    except (asyncio.CancelledError, asyncio.TimeoutError):
        if process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
        await process.communicate()
        raise

    if process.returncode != 0:
        raise RuntimeError(
            f"mcp2cli 调用 '{tool_name}' 失败（退出码 {process.returncode}）：\n"
            f"stdout: {stdout.decode(errors='replace')}\n"
            f"stderr: {stderr.decode(errors='replace')}"
        )

    raw = stdout.decode(errors="replace").strip()
    if not raw:
        raise RuntimeError(f"mcp2cli 对工具 '{tool_name}' 返回了空输出")

    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"mcp2cli 对 '{tool_name}' 的输出不是有效的 JSON: {raw[:200]}"
        ) from exc


# ---------------------------------------------------------------------------
# SQL 清洗辅助函数
# ---------------------------------------------------------------------------

def _clean_sql(raw: str) -> str:
    """去除 markdown 代码块围栏并规范化空白字符。"""
    match = re.search(r"```(?:sql)?\s*(.*?)\s*```", raw, re.DOTALL | re.IGNORECASE)
    text = match.group(1) if match else raw
    cleaned = text.strip()
    if cleaned:
        words = cleaned.split()
        if words and words[0].upper() == words[0] and len(words[0]) > 1:
            if not cleaned.endswith(";"):
                cleaned += ";"
    return cleaned


# ---------------------------------------------------------------------------
# 供 LLM 提示词使用的 JSON 列提示
# ---------------------------------------------------------------------------

def _build_json_hints_block(schema: dict[str, Any], dialect: str) -> str:
    """构建 JSON 字段访问提示块（内容保持英文，随提示词发送给 LLM）。"""
    matching_hints = {
        table: columns for table, columns in JSON_COLUMN_HINTS.items() if table in schema
    }
    if not matching_hints:
        return ""

    lines = ["", "## JSON stored as TEXT", ""]
    if dialect == "sqlite":
        lines.extend([
            "Use json_extract(col, '$.KEY') for JSON values; do not use ::jsonb.",
            "JSON dates are DD/MM/YYYY: convert them to YYYY-MM-DD using substr()",
            "before comparing them with ISO dates. JSON keys are case-sensitive.",
            "",
        ])
    else:
        lines.extend([
            "Cast TEXT columns to JSONB before using -> or ->>: col::jsonb->>'KEY'.",
            "Parse DD/MM/YYYY dates with TO_DATE(value, 'DD/MM/YYYY').",
            "",
        ])

    for table, cols in matching_hints.items():
        for col, keys in cols.items():
            lines.append(f"### Table: {table}  \u2192  Column: {col}  (TEXT containing JSON)")
            access = f"json_extract({col}, '$.KEY')" if dialect == "sqlite" else f"{col}::jsonb->>'KEY'"
            lines.append(f"Access with: {access}")
            lines.append("Available keys:")
            for key, description in keys.items():
                lines.append(f"  \u2022 {key:<22} \u2014 {description}")
            lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 节点：extract_schema
# ---------------------------------------------------------------------------

async def extract_schema(state: GraphState) -> dict[str, Any]:
    """通过 mcp2cli 调用 load_schema MCP 工具。"""
    logger.info("[extract_schema] 通过 mcp2cli 加载 schema。")
    try:
        payload = await _mcp2cli_async("load_schema")
        if payload.get("status") == "error":
            logger.error("[extract_schema] %s", payload.get("error"))
            return {"schema_ddl": "", "sqlglot_schema": {}, "error": payload.get("error")}
        return {
            "schema_ddl": payload["ddl"],
            "sqlglot_schema": payload["sqlglot_schema"],
            "error": None,
        }
    except Exception as exc:
        logger.exception("[extract_schema] MCP call failed")
        return {"schema_ddl": "", "sqlglot_schema": {}, "error": "MCP schema 服务超时。" if isinstance(exc, TimeoutError) else str(exc)}


# ---------------------------------------------------------------------------
# 节点：generate_sql
# ---------------------------------------------------------------------------

async def generate_sql(state: GraphState) -> dict[str, Any]:
    """LLM 根据用户问题生成第一次 SQL 尝试。"""
    logger.info("[generate_sql] question=%s", state.get("message"))
    dialect_name = "SQLite" if DB_DIALECT == "sqlite" else "PostgreSQL"
    json_hints = _build_json_hints_block(state.get("sqlglot_schema") or {}, DB_DIALECT)

    system = SystemMessage(content=f"""You are an expert {dialect_name} query writer.
Given the user's question and the database schema, write a single syntactically
correct {dialect_name} SELECT query that fully answers the question.

Rules:
1. Respond ONLY with the SQL — no markdown, no explanation, no preamble.
2. Use ONLY tables and columns that exist in the schema below.
3. Follow the JSON reference below when accessing JSON stored as text.
4. Add clear column aliases in SELECT so results are readable.
5. Use SELECT DISTINCT when fetching IDs or names to avoid duplicate rows.

## Database Schema (source of truth)
{state['schema_ddl']}
{json_hints}""")

    human = HumanMessage(content=state["message"])
    try:
        llm = get_llm()
        response = await asyncio.wait_for(llm.ainvoke([system, human]), LLM_TIMEOUT)
    except Exception as exc:
        logger.exception("模型生成 SQL 失败")
        return {"error": "模型请求超时。" if isinstance(exc, TimeoutError) else "模型服务调用失败，请检查服务配置和连接。"}
    usage = response.response_metadata.get("token_usage", {})
    logger.info("[generate_sql] tokens — prompt=%s completion=%s",
                usage.get("prompt_tokens"), usage.get("completion_tokens"))
    raw = response.content if isinstance(response.content, str) else str(response.content)
    cleaned = _clean_sql(raw)
    logger.info("[generate_sql] -> %s", cleaned)

    return {
        "sql_query": cleaned,
        "attempts": [{"sql": cleaned}],
        "iteration_count": 0,
        "validation_result": None,
        "execution_result": None,
        "result_columns": None,
        "final_sql_query": None,
    }


# ---------------------------------------------------------------------------
# 节点：validate_sql
# ---------------------------------------------------------------------------

async def validate_sql(state: GraphState) -> dict[str, Any]:
    """通过 mcp2cli 调用 validate_sql MCP 工具。"""
    sql = state.get("sql_query", "")
    logger.info("[validate_sql] %s", sql)
    try:
        result = await _mcp2cli_async("validate_sql", {"sql_query": sql})
    except Exception as exc:
        logger.exception("[validate_sql] MCP call failed")
        result = {"status": "error", "error_type": "timeout" if isinstance(exc, TimeoutError) else "service", "errors": ["MCP 校验服务超时。" if isinstance(exc, TimeoutError) else "MCP 校验服务不可用。"]}
    logger.info("[validate_sql] result=%s", result)
    attempts = [dict(item) for item in state.get("attempts", [])]
    if attempts:
        attempts[-1]["validation"] = result
    return {"validation_result": result, "attempts": attempts}


# ---------------------------------------------------------------------------
# 节点：execute_sql
# ---------------------------------------------------------------------------

async def execute_sql(state: GraphState) -> dict[str, Any]:
    """通过 mcp2cli 调用 execute_sql MCP 工具；成功时记录 final_sql_query。"""
    sql = state.get("sql_query", "")
    logger.info("[execute_sql] %s", sql)
    try:
        result = await _mcp2cli_async("execute_sql", {"sql_query": sql})
    except Exception as exc:
        logger.exception("[execute_sql] MCP call failed")
        result = {"status": "error", "error_type": "timeout" if isinstance(exc, TimeoutError) else "service", "error_message": "MCP 查询服务超时。" if isinstance(exc, TimeoutError) else "MCP 查询服务不可用。"}
    logger.info("[execute_sql] status=%s", result.get("status"))

    attempts = [dict(item) for item in state.get("attempts", [])]
    if attempts:
        attempts[-1]["execution"] = {k: v for k, v in result.items() if k not in ("rows", "columns")}
    update: dict[str, Any] = {"execution_result": result, "attempts": attempts}
    if result.get("status") == "success":
        update["final_sql_query"] = sql
        update["result_columns"] = result.get("columns", [])
    return update


# ---------------------------------------------------------------------------
# 节点：correct_sql
# ---------------------------------------------------------------------------

async def correct_sql(state: GraphState) -> dict[str, Any]:
    """LLM 根据错误反馈修正失败的 SQL 查询。"""
    iteration = state.get("iteration_count", 0) + 1
    logger.info("[correct_sql] attempt=%d", iteration)
    dialect_name = "SQLite" if DB_DIALECT == "sqlite" else "PostgreSQL"
    json_hints = _build_json_hints_block(state.get("sqlglot_schema") or {}, DB_DIALECT)

    system = SystemMessage(content=f"""You are a {dialect_name} expert correcting a failed SQL query.

Correction priority:
1. Execution error from the database is the most reliable signal — fix it first.
2. Follow the schema strictly; never guess table or column names.
3. Follow the JSON reference below when accessing JSON stored as text.
4. Respond ONLY with the corrected SQL — no markdown, no explanation.

## Database Schema (source of truth)
{state['schema_ddl']}
{json_hints}""")

    human = HumanMessage(content=(
        f"Original question: {state.get('message')}\n\n"
        f"Faulty SQL:\n{state.get('sql_query')}\n\n"
        f"Validation errors: {json.dumps(state.get('validation_result'))}\n\n"
        f"Execution error:   {json.dumps(state.get('execution_result'))}"
    ))

    try:
        llm = get_llm()
        response = await asyncio.wait_for(llm.ainvoke([system, human]), LLM_TIMEOUT)
    except Exception as exc:
        logger.exception("模型修正 SQL 失败")
        return {"error": "模型请求超时。" if isinstance(exc, TimeoutError) else "模型服务调用失败，请检查服务配置和连接。"}
    raw = response.content if isinstance(response.content, str) else str(response.content)
    cleaned = _clean_sql(raw)
    logger.info("[correct_sql] -> %s", cleaned)

    return {
        "sql_query": cleaned,
        "iteration_count": iteration,
        "attempts": [*state.get("attempts", []), {"sql": cleaned}],
        "validation_result": None,
        "execution_result": None,
    }
