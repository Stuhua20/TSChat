"""
Text-to-SQL 工作流的 LangGraph 状态定义。
"""
from typing import Any, Optional
from typing_extensions import TypedDict


class GraphState(TypedDict):
    """在所有 LangGraph 节点之间传递的共享状态。"""

    # 输入
    message: str

    # Schema（由 extract_schema 节点填充）
    schema_ddl: str
    sqlglot_schema: dict[str, Any]

    # SQL 生命周期
    sql_query: Optional[str]
    validation_result: Optional[dict[str, Any]]
    execution_result: Optional[dict[str, Any]]
    result_columns: Optional[list[str]]   # 最近一次成功执行返回的列名
    final_sql_query: Optional[str]

    # 记录每次 SQL 尝试，供评测报告使用。
    attempts: list[dict[str, Any]]

    # 循环控制（对应 LoopAgent max_iterations=3）
    iteration_count: int

    # 顶层错误传递
    error: Optional[str]
