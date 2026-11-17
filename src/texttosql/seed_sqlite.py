"""根据自带的 PostgreSQL 导出文件创建 SQLite 示例数据库。"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from .config import APP_DB_URI, DB_DIALECT, DB_URI, PROJECT_ROOT, sqlite_path

_INSERT_PREFIX = "INSERT INTO public.eln_writeup_anonymized "
_CREATE_TABLE = """
CREATE TABLE eln_writeup_anonymized (
    exp_id TEXT,
    system_name TEXT,
    write_up TEXT,
    summary_data TEXT,
    analysis_date TEXT
)
"""


def seed_database(path: Path, dump_path: Path) -> int:
    """创建新数据库，不覆盖任何已存在的文件。"""
    statements = []
    current = ""
    with dump_path.open(encoding="utf-8", newline="") as source:
        for line in source:
            if current or line.startswith(_INSERT_PREFIX):
                current += line
                if sqlite3.complete_statement(current):
                    statements.append(
                        current.replace(
                            _INSERT_PREFIX, "INSERT INTO eln_writeup_anonymized ", 1
                        )
                    )
                    current = ""
    if current:
        raise ValueError("示例导出文件中包含未完成的 INSERT 语句")
    if not statements:
        raise ValueError("在 PostgreSQL 导出文件中未找到示例 INSERT 语句")

    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    try:
        conn = sqlite3.connect(path)
        try:
            with conn:
                conn.execute(_CREATE_TABLE)
                for statement in statements:
                    conn.execute(statement)
        finally:
            conn.close()
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return len(statements)


def main() -> None:
    if DB_DIALECT != "sqlite":
        raise SystemExit("创建示例数据库前请先设置 DB_DIALECT=sqlite")
    path = sqlite_path(DB_URI)
    if APP_DB_URI.startswith("sqlite:///") and path == sqlite_path(APP_DB_URI):
        raise SystemExit("业务数据库与应用数据库必须使用不同的文件")
    try:
        count = seed_database(path, PROJECT_ROOT / "seed_data.sql")
    except FileExistsError:
        raise SystemExit(f"数据库已存在，拒绝覆盖：{path}")
    print(f"已创建 {path}，共导入 {count} 条示例数据。")


if __name__ == "__main__":
    main()
