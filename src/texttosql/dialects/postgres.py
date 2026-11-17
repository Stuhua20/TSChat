from collections.abc import Iterator
from contextlib import contextmanager

import psycopg2
from psycopg2 import sql
from psycopg2.extensions import connection

from ..config import DB_QUERY_TIMEOUT
from .dialect import DatabaseDialect


class PostgreSQLDialect(DatabaseDialect):
    """PostgreSQL 实现。"""

    @property
    def name(self) -> str:
        return "postgresql"

    @contextmanager
    def get_connection(self, db_uri: str) -> Iterator[connection]:
        conn = psycopg2.connect(db_uri, connect_timeout=5)
        try:
            conn.set_session(readonly=True)
            with conn:
                with conn.cursor() as cursor:
                    cursor.execute("SET LOCAL statement_timeout = %s", (DB_QUERY_TIMEOUT * 1000,))
                yield conn
        finally:
            conn.close()

    def get_sqlglot_dialect(self) -> str:
        return "postgres"

    def quote_identifier(self, name: str) -> str:
        return f'"{name}"'

    def _get_ddl_from_db(self, db_uri: str) -> str:
        """通过查询 information_schema 生成 schema 的 DDL。"""
        with self.get_connection(db_uri) as conn:
            with conn.cursor() as cursor:
                return self._build_ddl_from_info_schema(cursor)

    def _build_ddl_from_info_schema(self, cursor: psycopg2.extensions.cursor) -> str:
        """
        查询系统目录表并拼装 CREATE TABLE 语句。

        关键行为：JSON / JSONB 列在 DDL 中原样保留，
        让 LLM 知道应使用 ->> / -> 操作符，而不是把它们当作普通文本。
        """
        cursor.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_type = 'BASE TABLE' "
            "ORDER BY table_name;"
        )
        tables = [row[0] for row in cursor.fetchall()]
        if not tables:
            return ""

        ddl_parts: list[str] = []

        for table_name in tables:
            cols_for_table: list[str] = []

            # --- 1. 获取列信息（包含 data_type 以便识别 json/jsonb）---
            query = sql.SQL("""
                SELECT column_name, udt_name, data_type, is_nullable
                FROM information_schema.columns
                WHERE table_name = {tbl} AND table_schema = 'public'
                ORDER BY ordinal_position;
            """).format(tbl=sql.Literal(table_name))
            cursor.execute(query)
            columns_info = cursor.fetchall()

            for col_name, udt_name, data_type, is_nullable in columns_info:
                # JSON/JSONB 原样保留 — LLM 必须看到这一点才会使用 ->>
                if data_type in ("json", "jsonb") or udt_name in ("json", "jsonb"):
                    col_type_ddl = udt_name.upper()  # "JSONB" 或 "JSON"
                else:
                    generic_type = self._postgres_type_to_generic(udt_name)
                    col_type_ddl = self.map_type_to_ddl(generic_type)

                not_null_str = " NOT NULL" if is_nullable == "NO" else ""
                cols_for_table.append(
                    f"  {self.quote_identifier(col_name)} {col_type_ddl}{not_null_str}"
                )

            # --- 2. 获取主键 ---
            query = sql.SQL("""
                SELECT kcu.column_name
                FROM information_schema.table_constraints AS tc
                JOIN information_schema.key_column_usage AS kcu
                  ON tc.constraint_name = kcu.constraint_name
                 AND tc.table_schema   = kcu.table_schema
                WHERE tc.constraint_type = 'PRIMARY KEY'
                  AND tc.table_name   = {tbl}
                  AND tc.table_schema = 'public';
            """).format(tbl=sql.Literal(table_name))
            cursor.execute(query)
            primary_keys = [row[0] for row in cursor.fetchall()]
            if primary_keys:
                pk_cols = [self.quote_identifier(pk) for pk in primary_keys]
                cols_for_table.append(f"  PRIMARY KEY ({', '.join(pk_cols)})")

            # --- 3. 获取外键 ---
            query = sql.SQL("""
                SELECT
                    kcu.column_name,
                    ccu.table_name  AS foreign_table_name,
                    ccu.column_name AS foreign_column_name
                FROM information_schema.table_constraints AS tc
                JOIN information_schema.key_column_usage AS kcu
                  ON tc.constraint_name = kcu.constraint_name
                 AND tc.table_schema   = kcu.table_schema
                JOIN information_schema.constraint_column_usage AS ccu
                  ON ccu.constraint_name = tc.constraint_name
                 AND ccu.table_schema   = tc.table_schema
                WHERE tc.constraint_type = 'FOREIGN KEY'
                  AND tc.table_name   = {tbl}
                  AND tc.table_schema = 'public';
            """).format(tbl=sql.Literal(table_name))
            cursor.execute(query)
            fks = cursor.fetchall()
            for from_col, to_table, to_col in fks:
                cols_for_table.append(
                    f"  FOREIGN KEY ({self.quote_identifier(from_col)}) "
                    f"REFERENCES {self.quote_identifier(to_table)} "
                    f"({self.quote_identifier(to_col)})"
                )

            ddl_parts.append(
                f"CREATE TABLE {self.quote_identifier(table_name)} (\n"
                + ",\n".join(cols_for_table)
                + "\n);"
            )

        return "\n\n".join(ddl_parts)

    def map_type_to_ddl(self, sql_type: str) -> str:
        mapping: dict[str, str] = {
            "text":      "TEXT",
            "number":    "NUMERIC",
            "integer":   "INTEGER",
            "boolean":   "BOOLEAN",
            "timestamp": "TIMESTAMP",
            "date":      "DATE",
            "json":      "JSONB",
            "jsonb":     "JSONB",
        }
        return mapping.get(sql_type.lower(), "TEXT")

    def _postgres_type_to_generic(self, postgres_type: str) -> str:
        pg_type = postgres_type.lower().strip()

        if "int" in pg_type:
            return "INTEGER"
        if any(t in pg_type for t in ["char", "text", "varchar"]):
            return "TEXT"
        if any(t in pg_type for t in ["numeric", "decimal", "real", "float", "double"]):
            return "NUMBER"
        if pg_type.startswith("timestamp"):
            return "TIMESTAMP"
        if "date" in pg_type:
            return "DATE"
        if "bool" in pg_type:
            return "BOOLEAN"
        if pg_type in ("json", "jsonb"):
            return "JSONB"

        return "TEXT"
