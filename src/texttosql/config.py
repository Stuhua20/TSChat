"""
从环境变量加载的配置。

通过环境变量或根目录 .env 选择数据库与模型。
"""

import os
from pathlib import Path
from urllib.parse import unquote

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")

# ── 数据库 ──────────────────────────────────────────────────────────────────
DB_DIALECT: str = os.getenv("DB_DIALECT", "sqlite").lower()
DB_URI: str = os.getenv("DB_URI") or (
    "sqlite:///data/business.db" if DB_DIALECT == "sqlite" else ""
)
APP_DB_URI: str = os.getenv("APP_DB_URI") or (
    "sqlite:///data/app.db" if DB_DIALECT == "sqlite" else DB_URI
)


def sqlite_path(db_uri: str) -> Path:
    """把基于文件的 SQLite URL 解析为相对于项目根目录的路径。"""
    prefix = "sqlite:///"
    if not db_uri.startswith(prefix):
        raise ValueError("SQLite database URL must start with sqlite:///")
    filename = unquote(db_uri[len(prefix) :])
    if not filename or filename == ":memory:" or "?" in filename or "#" in filename:
        raise ValueError("Use a file-based SQLite URL without query parameters")
    path = Path(filename).expanduser()
    return (path if path.is_absolute() else PROJECT_ROOT / path).resolve()


def app_sqlite_path() -> Path:
    """将可写的应用状态与只读查询数据库分开。"""
    path = sqlite_path(APP_DB_URI)
    if DB_DIALECT == "sqlite" and path == sqlite_path(DB_URI):
        raise ValueError("DB_URI and APP_DB_URI must point to different SQLite files")
    return path


# ── LLM：OpenAI 及任何兼容 ChatGPT 协议的服务商 ────────────────────────────────
OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
# 兼容接口地址，留空使用 OpenAI 官方（https://api.openai.com/v1）；
# 例如 DeepSeek 为 https://api.deepseek.com/v1，本地 vLLM/Ollama 为 http://127.0.0.1:8000/v1
OPENAI_BASE_URL: str = os.getenv("OPENAI_BASE_URL", "").rstrip("/")

# ── MCP ───────────────────────────────────────────────────────────────────────
# MCP 服务入口文件路径
MCP_SERVER_SCRIPT: str = os.path.join(os.path.dirname(__file__), "mcp_server.py")
MCP_SERVER_URL: str = os.environ.get("MCP_SERVER_URL", "http://127.0.0.1:3001/mcp")

# ── 循环保护 ──────────────────────────────────────────────────────────────────
MAX_CORRECTION_ITERATIONS: int = int(os.getenv("MAX_CORRECTION_ITERATIONS", "3"))

# Time limits in seconds. The database deadline is shorter than the MCP deadline.
DB_QUERY_TIMEOUT = 15
MCP_TIMEOUT = 25
LLM_TIMEOUT = 45

# 注意：以下字段说明会随提示词发送给 LLM，保持英文以保证生成质量
_ELN_SUMMARY_HINTS: dict[str, str] = {
    # ── 操作人 ────────────────────────────────────────────────────────────
    "ISID": "User/login ID of the experiment owner (e.g. 'r.shetty')",
    "COMPLETED_ISID": "User ID of the person who marked the experiment complete",
    "COUNTERSIGNER": "User ID of the person who countersigned the experiment (e.g. 'h.lin')",
    # ── 实验标识 ──────────────────────────────────────────────────────────
    "EXPERIMENT_ID": "Numeric experiment identifier stored as text (e.g. '138844')",
    "EXPERIMENT_NAME": "Human-readable experiment name / title",
    "PROTOCOL": "Protocol type used (e.g. 'ChemELN')",
    "PROTOCOL_ID": "Numeric protocol identifier stored as text (e.g. '81')",
    # ── 记录本位置 ────────────────────────────────────────────────────────
    "BOOK": "Lab notebook book number (e.g. '6')",
    "PAGE": "Page number within the lab notebook (e.g. '15')",
    # ── 日期（格式 DD/MM/YYYY）────────────────────────────────────────────
    "CREATED_DATE": "Date the experiment record was created, stored as DD/MM/YYYY text",
    "MODIFIED_DATE": "Date last modified, stored as DD/MM/YYYY text",
    "COMPLETED_DATE": "Date completed, stored as DD/MM/YYYY text",
    "COUNTERSIGNED_DATE": "Date countersigned, stored as DD/MM/YYYY text",
    # ── 状态标记 ──────────────────────────────────────────────────────────
    "STATUS": "Workflow status code as text — '0' means completed",
    "CURRENT_VERSION": "Record version number as text (e.g. '1')",
    "PDF_COMPLETE": "'Y' if the PDF has been generated, 'N' otherwise",
}

JSON_COLUMN_HINTS: dict[str, dict[str, dict[str, str]]] = {
    "eln_writeup_api_extract": {"summary_data": _ELN_SUMMARY_HINTS},
    "eln_writeup_anonymized": {"summary_data": _ELN_SUMMARY_HINTS},
}
