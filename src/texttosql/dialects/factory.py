from ..config import DB_DIALECT
from .dialect import DatabaseDialect


def get_dialect() -> DatabaseDialect:
    """
    根据 DB_DIALECT 环境变量返回配置的 DatabaseDialect。
    数据库特定模块按需惰性导入，选择 SQLite 时 PostgreSQL
    驱动保持可选状态。
    """
    dialect_name = DB_DIALECT.lower()

    if dialect_name == "sqlite":
        from .sqlite import SQLiteDialect  # 惰性导入 — 真正需要时才加载
        return SQLiteDialect()

    if dialect_name == "postgresql":
        from .postgres import PostgreSQLDialect
        return PostgreSQLDialect()

    raise ValueError(f"Unsupported DB_DIALECT: '{dialect_name}'. Use 'sqlite' or 'postgresql'.")
