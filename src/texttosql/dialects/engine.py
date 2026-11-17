import logging
from typing import Any

from sqlglot import exp, parse
from sqlglot.errors import SqlglotError
from sqlglot.optimizer import optimize as sqlglot_optimize
from sqlglot.schema import MappingSchema

from .dialect import DatabaseDialect

logger = logging.getLogger(__name__)


class SQLValidator:
    """使用 SQLGlot 校验 SQL。"""

    def validate(
        self,
        sql_query: str,
        dialect: DatabaseDialect,
        sqlglot_schema_dict: dict[str, dict[str, str]],
    ) -> dict[str, Any]:
        """校验 SQL 查询的语法与语义。"""
        logger.info("--- Starting SQL Validation ---")
        logger.info(f"Query to validate: {sql_query}")
        logger.info(f"Schema being used for validation: {sqlglot_schema_dict}")
        try:
            sqlglot_dialect = dialect.get_sqlglot_dialect()
            logger.info(f"Using SQLGlot dialect: {sqlglot_dialect}")

            # 1. 解析（语法检查）
            logger.info("Parsing SQL query...")
            statements = parse(sql_query, read=sqlglot_dialect)
            if len(statements) != 1 or not isinstance(statements[0], exp.Query):
                return {"status": "error", "error_type": "policy", "errors": ["Only one read-only SELECT query is allowed"]}
            expression = statements[0]
            if any(isinstance(node, (exp.DML, exp.DDL, exp.Into, exp.Lock, exp.Command)) for node in expression.walk()):
                return {"status": "error", "error_type": "policy", "errors": ["Query contains a write or locking operation"]}
            logger.info(f"Parse successful. Expression type: {type(expression)}")

            # 2. 用给定的字典构建 Schema 对象
            logger.info("Building SQLGlot schema object...")
            schema_obj = MappingSchema(sqlglot_schema_dict, dialect=sqlglot_dialect)
            logger.info(
                f"Schema object built. Tables: {list(sqlglot_schema_dict.keys())}"
            )

            # 3. 校验
            logger.info("Running SQLGlot optimization and validation...")
            sqlglot_optimize(
                expression,
                schema=schema_obj,
                dialect=sqlglot_dialect,
                validate_qualify_columns=True,
            )
            logger.info("--- SQL Validation Successful ---")
            return {"status": "success"}

        except SqlglotError as e:
            logger.exception("SQL validation failed")
            return {"status": "error", "error_type": "sql", "errors": [str(e)]}
        except Exception:
            logger.exception("SQL validation service failed")
            return {"status": "error", "error_type": "service", "errors": ["SQL 校验服务发生错误。"]}


class SQLExecutor:
    """在数据库上执行 SQL 查询。"""

    def __init__(self, db_uri: str, dialect: DatabaseDialect):
        self.db_uri = db_uri
        self.dialect = dialect

    def execute(self, sql_query: str) -> tuple[list[tuple] | None, str | None]:
        """执行一条 SQL 查询。"""
        try:
            logger.info(f"Executing SQL query: {sql_query}")
            # 连接管理完全委托给方言层
            with self.dialect.get_connection(self.db_uri) as conn:
                logger.info("Database connection established")
                cursor = conn.cursor()
                logger.info("Executing query...")
                cursor.execute(sql_query)
                logger.info("Query executed successfully")
                result = cursor.fetchall()
                logger.info(f"Query returned {len(result)} rows")
                # cursor.close() 和 conn.close() 会被自动调用！
                return result, None
        except Exception as e:
            logger.error(f"SQL execution failed: {e}", exc_info=True)
            return None, str(e)
