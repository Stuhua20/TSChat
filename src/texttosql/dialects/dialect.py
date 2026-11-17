import logging
from abc import ABC, abstractmethod
from typing import Any

from sqlglot import exp, parse

logger = logging.getLogger(__name__)


class DatabaseDialect(ABC):
    """定义数据库特定行为的抽象基类。"""

    def __init__(self) -> None:
        self._schema_cache: dict[str, dict[str, Any]] = {}

    # --- 公开 API（带缓存）---

    def get_ddl(self, db_uri: str) -> str:
        """
        将数据库 schema 提取为 DDL 字符串，使用缓存。
        """
        if db_uri not in self._schema_cache or "ddl" not in self._schema_cache[db_uri]:
            self._ensure_schema_cached(db_uri)
        return self._schema_cache[db_uri]["ddl"]

    def get_sqlglot_schema(self, db_uri: str) -> dict[str, dict[str, str]]:
        """
        通过解析数据库 DDL 构建 SQLGlot MappingSchema，使用缓存。
        """
        if (
            db_uri not in self._schema_cache
            or "sqlglot_schema" not in self._schema_cache[db_uri]
        ):
            self._ensure_schema_cached(db_uri)
        return self._schema_cache[db_uri]["sqlglot_schema"]

    def _ensure_schema_cached(self, db_uri: str) -> None:
        """
        确保给定 db_uri 的 DDL 和 SQLGlot schema 都已缓存。
        这是填充缓存的唯一入口。
        """
        if (
            db_uri in self._schema_cache
            and "ddl" in self._schema_cache[db_uri]
            and "sqlglot_schema" in self._schema_cache[db_uri]
        ):
            return  # 已完整缓存

        ddl_string = self._get_ddl_from_db(db_uri)
        sqlglot_schema = self._parse_ddl_to_sqlglot_schema(ddl_string)

        self._schema_cache[db_uri] = {
            "ddl": ddl_string,
            "sqlglot_schema": sqlglot_schema,
        }

    # --- 由子类实现的抽象方法 ---

    @property
    @abstractmethod
    def name(self) -> str:
        """返回数据库名称（例如 'sqlite'）。"""
        pass

    @abstractmethod
    def get_connection(self, db_uri: str) -> Any:
        """创建数据库连接。"""
        pass

    @abstractmethod
    def get_sqlglot_dialect(self) -> str:
        """返回与 SQLGlot 兼容的方言名称。"""
        pass

    @abstractmethod
    def _get_ddl_from_db(self, db_uri: str) -> str:
        """
        从数据库提取 DDL 的具体实现。
        由公开的 get_ddl 方法调用。
        """
        pass

    @abstractmethod
    def map_type_to_ddl(self, sql_type: str) -> str:
        """将通用类型映射为特定数据库的 DDL 类型。"""
        pass

    # --- 私有/受保护的辅助方法 ---

    def _parse_ddl_to_sqlglot_schema(
        self, ddl_string: str
    ) -> dict[str, dict[str, str]]:
        """
        将 DDL 字符串解析为 SQLGlot 的 schema 字典格式。
        会把 DDL 拆分成单条 CREATE TABLE 语句逐条解析，
        以便对单条语句中的错误更具容错性。
        """
        sqlglot_schema: dict[str, dict[str, str]] = {}
        # 按通常结束 CREATE TABLE 块的分号拆分语句。
        # 对某些 DDL 格式来说，这比按 "\n\n" 拆分更稳健。
        statements = [s.strip() for s in ddl_string.split(";") if s.strip()]

        for statement in statements:
            try:
                # parse 函数可以处理表达式列表，但这里逐条解析以隔离错误。
                create_expr_list = parse(statement, read=self.get_sqlglot_dialect())
                if not create_expr_list:
                    continue

                create_expr = create_expr_list[0]

                if isinstance(create_expr, exp.Create) and create_expr.kind == "TABLE":
                    # 对于带引号的表名，需要从嵌套的 Table 对象中取名称
                    table_name = create_expr.this.this.name
                    columns: dict[str, str] = {}
                    schema = create_expr.this
                    if isinstance(schema, exp.Schema):
                        for col_def in schema.expressions:
                            if isinstance(col_def, exp.ColumnDef):
                                column_name = col_def.this.name
                                if col_def.kind:
                                    column_type = col_def.kind.sql(
                                        dialect=self.get_sqlglot_dialect()
                                    )
                                else:
                                    column_type = "UNKNOWN"
                                columns[column_name] = column_type
                        sqlglot_schema[table_name] = columns
            except Exception as e:
                logger.warning(
                    f"Could not parse DDL statement: {statement[:100]}... Error: {e}"
                )
                continue  # 处理下一条语句

        return sqlglot_schema
