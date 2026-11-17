"""Text-to-SQL 流程的 SQLite 业务数据库访问。"""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

from ..config import sqlite_path
from .dialect import DatabaseDialect


class SQLiteDialect(DatabaseDialect):
    @property
    def name(self) -> str:
        return "sqlite"

    @contextmanager
    def get_connection(self, db_uri: str) -> Iterator[sqlite3.Connection]:
        path = sqlite_path(db_uri)
        # mode=ro 既阻止写入，也避免静默创建一个空数据库。
        conn = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
        try:
            conn.execute("PRAGMA query_only = ON")
            yield conn
        finally:
            conn.close()

    def get_sqlglot_dialect(self) -> str:
        return "sqlite"

    def _ensure_schema_cached(self, db_uri: str) -> None:
        if db_uri in self._schema_cache:
            return
        with self.get_connection(db_uri) as conn:
            rows = conn.execute(
                "SELECT name, sql FROM sqlite_schema "
                "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' "
                "AND sql IS NOT NULL ORDER BY name"
            ).fetchall()
            if not rows:
                raise ValueError("SQLite 业务数据库中没有找到任何数据表")
            schema = {}
            for table_name, _ in rows:
                columns = conn.execute(
                    "SELECT name, type FROM pragma_table_xinfo(?) WHERE hidden != 1",
                    (table_name,),
                ).fetchall()
                schema[table_name] = {
                    name: self.map_type_to_ddl(declared_type)
                    for name, declared_type in columns
                }
        self._schema_cache[db_uri] = {
            "ddl": "\n\n".join(f"{ddl.rstrip(';')};" for _, ddl in rows),
            "sqlglot_schema": schema,
        }

    def _get_ddl_from_db(self, db_uri: str) -> str:
        self._ensure_schema_cached(db_uri)
        return self._schema_cache[db_uri]["ddl"]

    def map_type_to_ddl(self, sql_type: str) -> str:
        declared = sql_type.upper()
        if "INT" in declared:
            return "INTEGER"
        if any(kind in declared for kind in ("CHAR", "CLOB", "TEXT")):
            return "TEXT"
        if not declared or "BLOB" in declared:
            return "BLOB"
        if any(kind in declared for kind in ("REAL", "FLOA", "DOUB")):
            return "REAL"
        return "NUMERIC"
