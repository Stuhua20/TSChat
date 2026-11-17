# TSChat

TSChat 是一个自然语言数据查询应用：使用中文或英文提问，生成并执行 SQL，在网页中查看结果、图表和文字描述。项目提供 Web 界面和命令行工具，支持 SQLite 与 PostgreSQL。

## 功能

- 基于 LangGraph 的查询流程：读取表结构、生成 SQL、使用 SQLGlot 校验，再通过 MCP 执行；SQL 错误默认最多修正三次。
- Web 聊天：保存会话，展示 SQL、查询结果及简单图表，并根据结果生成文字描述。
- 内置 20 道 SQLite ELN 示例评测题，提供执行成功率、正确率和逐题结果。
- 支持 SQLite 示例数据库，也可连接独立的 PostgreSQL 业务数据库。

技术栈：React、TypeScript、Vite、FastAPI、LangGraph、MCP 和 SQLGlot。

## 快速开始

以下命令从项目根目录执行。需要 Python 3.10–3.13（推荐 3.11）、Node.js 20.19+ 或 22.12+，以及 npm。

### 1. 安装并配置

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
cp -n .env.example .env
```

编辑 `.env`，填入 `OPENAI_API_KEY`。默认使用 `gpt-4o-mini`；也可通过 `OPENAI_MODEL` 和 `OPENAI_BASE_URL` 配置兼容的模型服务。

### 2. 创建示例数据库和登录账号

```bash
# 创建 SQLite 业务数据库；已有文件时会拒绝覆盖
python -m texttosql.seed_sqlite

# 初始化应用数据库并创建账号，请替换示例密码
python -m texttosql.web.manage_users init
python -m texttosql.web.manage_users add researcher 'YOUR_TEST_PASSWORD' scientist
```

默认业务数据位于 `data/business.db`，账号、登录状态、聊天会话和评测结果存储在独立的 `data/app.db`。两个 SQLite 路径必须不同。

### 3. 启动服务

分别在三个终端中运行：

```bash
# 终端 1：MCP 数据库工具服务
source .venv/bin/activate
python -m texttosql.mcp_server
```

```bash
# 终端 2：FastAPI 后端
source .venv/bin/activate
uvicorn texttosql.web.app:app --host 127.0.0.1 --port 8000 --reload
```

```bash
# 终端 3：前端开发服务器
cd frontend
npm install
npm run dev
```

打开 Vite 输出的地址（通常为 `http://127.0.0.1:5173`），使用创建的账号登录。开发服务器会将 API 请求转发至 `127.0.0.1:8000`。

也可以在 `frontend` 目录执行 `npm install` 和 `npm run build`，然后启动 MCP 服务和 FastAPI，通过 `http://127.0.0.1:8000` 访问构建后的应用。

## 配置

项目从根目录的 `.env` 读取配置，完整示例见 [`.env.example`](.env.example)。

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `DB_DIALECT` | `sqlite` | 业务数据库类型：`sqlite` 或 `postgresql` |
| `DB_URI` | `sqlite:///data/business.db` | 业务数据库连接地址 |
| `APP_DB_URI` | `sqlite:///data/app.db` | 应用数据库连接地址，需独立于业务数据库 |
| `OPENAI_API_KEY` | 空 | 模型服务的 API 密钥 |
| `OPENAI_MODEL` | `gpt-4o-mini` | 模型名称 |
| `OPENAI_BASE_URL` | 空 | 兼容模型服务的地址；留空使用 OpenAI 默认地址 |
| `MCP_HOST` | `127.0.0.1` | MCP 服务监听地址 |
| `MCP_SERVER_PORT` | `3001` | MCP 服务监听端口 |
| `MCP_SERVER_URL` | `http://127.0.0.1:3001/mcp` | 查询流程连接 MCP 服务的地址 |
| `MAX_CORRECTION_ITERATIONS` | `3` | SQL 修正次数上限 |
| `APP_SECURE_COOKIE` | `false` | 通过 HTTPS 提供服务时设为 `true` |

表中数据库地址为 SQLite 的默认值。修改 MCP 监听地址或端口时，需同时调整 `MCP_SERVER_URL`。`.env` 和 `data/` 已加入 Git 忽略列表，请勿提交密钥或本地数据库。

### PostgreSQL

安装可选依赖：

```bash
python -m pip install -e '.[postgres]'
```

将 `DB_DIALECT` 设为 `postgresql`，并分别设置业务数据库的 `DB_URI` 和独立应用数据库的 `APP_DB_URI`。业务数据库应使用权限受限的只读账号；应用数据库需要写权限，使用前执行上面的账号初始化命令。内置评测仅支持 SQLite。

另有一套虚构医疗器械销售数据，可用于 SQLite 或 PostgreSQL 查询练习，参见 [示例说明](examples/medical_sales/README.md)。该数据集不适用于内置 ELN 评测题。

## 使用

### Web 查询

登录后输入问题。查询成功时可展开“查看查询过程”，查看 SQL、结果表和图表；失败时显示错误信息。结果表最多展示前 50 行；问题包含“图表”等关键词时，数值结果可展示简单图表，最多显示前 15 行。

文字描述最多使用前 50 行，并受结果文本长度限制；未使用全部行时页面会标明范围。结果过大或描述服务不可用时，仍可查看原始结果。生成描述会增加一次模型调用。聊天历史会保存，但每次查询只处理当前问题，不会自动将历史消息作为模型上下文。

### 评测与分析

在侧栏打开“评测与分析”，可运行内置的 20 道 ELN 题目并查看进度和结果。题目和参考 SQL 位于 [`src/texttosql/evaluation/cases.json`](src/texttosql/evaluation/cases.json)，适用于自带的 SQLite 示例业务库。

评测结果按用户隔离并保存在应用数据库中。参考 SQL 失败、服务不可用等情况标记为“无法判定”，不计入正确率分母。评测要求业务库和应用库均为 SQLite，面向单进程本地部署；重启服务会中断任务。运行前后会检查业务数据库文件是否变化，但这不等同于数据库快照隔离。模型调用可能产生费用；停止任务后，已完成结果会保留。

### 命令行

保持 MCP 服务运行，在另一个终端执行：

```bash
source .venv/bin/activate
python -m texttosql.main '一共有多少个实验？'
```

不传问题时进入交互模式。

### 账号管理

```bash
python -m texttosql.web.manage_users list
python -m texttosql.web.manage_users passwd researcher 'NEW_PASSWORD'
python -m texttosql.web.manage_users delete researcher
```

初始化时，旧版聊天历史会复制到新会话表，原始历史表保留；重复初始化不会重复导入或恢复已删除的会话。旧消息以文本形式展示。

## 开发与测试

```bash
pytest -q
cd frontend && npm run build
```

主要目录：

```text
frontend/                    React 前端
src/texttosql/web/           Web API、认证、会话和评测接口
src/texttosql/graph.py       LangGraph 查询流程
src/texttosql/nodes.py       SQL 生成、校验和执行节点
src/texttosql/mcp_server.py  MCP 数据库工具服务
src/texttosql/dialects/      SQLite 和 PostgreSQL 适配
src/texttosql/evaluation/    评测题集和结果比较
examples/medical_sales/      医疗器械销售示例数据
tests/                       后端测试
```

## 使用注意

- 问题、表结构、SQL、错误反馈和用于描述的查询结果会发送给配置的模型服务商。
- SQLite 业务数据库以只读方式打开；PostgreSQL 应使用最小权限只读账号。
- MCP 服务没有公开访问鉴权，默认仅监听本机；对外部署前需配置访问控制。Web 应用默认面向单进程本地部署，对外提供服务时应使用 HTTPS，并配置反向代理和访问控制。
- SQLite 并发写入串行化，本项目适合本地或低并发使用。停止评测后，已开始的数据库查询可能继续运行至完成或超时。
