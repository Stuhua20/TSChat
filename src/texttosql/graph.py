"""
LangGraph 图结构
================
MCP 工具通过 mcp2cli 子进程调用 — 不使用 MultiServerMCPClient，
不注入工具 schema，不依赖 langchain-mcp-adapters。

图拓扑
------

  START
    |
  extract_schema           (mcp2cli: load_schema)
    |
  generate_sql             (LLM)
    |
  validate_sql <--------+  (mcp2cli: validate_sql)
    |                   |
    +- 校验通过 -> execute_sql   (mcp2cli: execute_sql)
    |               |
    |               +- 成功 -> END
    |               +- 出错   -> correct_sql -> (循环)
    |
    +- 校验失败 -> correct_sql -> (循环，最多 3 次)

用法
-----
# 终端 1 — 启动 MCP 服务
python -m texttosql.mcp_server

# 终端 2 — 运行查询
python -m texttosql.main "r.shetty 完成了多少个实验？"
"""
from __future__ import annotations

import logging
from typing import Any

from langgraph.graph import END, StateGraph

from .config import MAX_CORRECTION_ITERATIONS
from .nodes import correct_sql, execute_sql, extract_schema, generate_sql, validate_sql
from .state import GraphState

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 路由函数
# ---------------------------------------------------------------------------

def _route_after_schema(state: GraphState) -> str:
    if state.get("error"):
        return END
    return "generate_sql"


def _route_after_model(state: GraphState) -> str:
    return END if state.get("error") else "validate_sql"


def _route_after_validate(state: GraphState) -> str:
    result = state.get("validation_result") or {}
    if result.get("status") == "success":
        return "execute_sql"
    if result.get("error_type") != "sql":
        return END
    if state.get("iteration_count", 0) >= MAX_CORRECTION_ITERATIONS:
        logger.warning("Max correction iterations reached after validation failure.")
        return END
    return "correct_sql"


def _route_after_execute(state: GraphState) -> str:
    result = state.get("execution_result") or {}
    if result.get("status") == "success":
        return END
    if result.get("error_type") != "sql":
        return END
    if state.get("iteration_count", 0) >= MAX_CORRECTION_ITERATIONS:
        logger.warning("Max correction iterations reached after execution failure.")
        return END
    return "correct_sql"


# ---------------------------------------------------------------------------
# 图构建
# ---------------------------------------------------------------------------

def _build_graph() -> Any:
    builder = StateGraph(GraphState)

    builder.add_node("extract_schema", extract_schema)
    builder.add_node("generate_sql",   generate_sql)
    builder.add_node("validate_sql",   validate_sql)
    builder.add_node("execute_sql",    execute_sql)
    builder.add_node("correct_sql",    correct_sql)

    builder.set_entry_point("extract_schema")
    builder.add_conditional_edges(
        "extract_schema", _route_after_schema,
        {"generate_sql": "generate_sql", END: END},
    )
    builder.add_conditional_edges("generate_sql", _route_after_model, {"validate_sql": "validate_sql", END: END})

    builder.add_conditional_edges(
        "validate_sql",
        _route_after_validate,
        {"execute_sql": "execute_sql", "correct_sql": "correct_sql", END: END},
    )
    builder.add_conditional_edges(
        "execute_sql",
        _route_after_execute,
        {END: END, "correct_sql": "correct_sql"},
    )
    builder.add_conditional_edges("correct_sql", _route_after_model, {"validate_sql": "validate_sql", END: END})

    return builder.compile()


# ---------------------------------------------------------------------------
# 公开入口
# ---------------------------------------------------------------------------

# 在导入时编译一次 — 无需客户端配置
_graph = _build_graph()


async def run_pipeline(user_question: str) -> dict[str, Any]:
    """
    为用户问题运行完整的 Text-to-SQL 流程。

    MCP 服务必须已在运行：
        python -m texttosql.mcp_server
    """
    initial_state: GraphState = {
        "message": user_question,
        "schema_ddl": "",
        "sqlglot_schema": {},
        "sql_query": None,
        "validation_result": None,
        "execution_result": None,
        "result_columns": None,
        "final_sql_query": None,
        "iteration_count": 0,
        "attempts": [],
        "error": None,
    }

    logger.info("Invoking graph for: %s", user_question)
    return await _graph.ainvoke(initial_state)
