# 医疗器械销售示例数据库

这是一套虚构的企业销售演示数据，适用于 Text-to-SQL 查询练习。客户、制造商、产品及交易均为模拟数据；产品名称和管理类别仅用于展示，不代表真实产品、注册信息或监管分类。库中没有患者、诊疗或真实企业数据。

## 表和字段

| 表 | 字段 | 含义 |
|---|---|---|
| `departments` | `department_id`, `department_name` | 部门编号、名称 |
| `employees` | `employee_id`, `employee_name`, `department_id` | 员工编号、姓名、所属部门 |
| | `job_title`, `hire_date`, `status` | 职位、入职日期、`active` 在职 / `inactive` 离职 |
| `customers` | `customer_id`, `customer_name`, `customer_type` | 客户编号、名称、类型：`hospital` 医疗中心、`clinic` 门诊、`distributor` 经销商 |
| | `province`, `region`, `created_at` | 省级地区、销售区域、客户创建日期 |
| `products` | `product_id`, `product_code`, `product_name` | 产品编号、编码、名称 |
| | `category`, `device_class`, `manufacturer` | 产品类别、示例管理类别、虚构制造商 |
| | `unit`, `list_price`, `status` | 销售单位、目录单价（元）、`active` 在售 / `inactive` 停售 |
| `orders` | `order_id`, `order_number`, `customer_id`, `employee_id` | 订单编号、订单号、客户、负责员工 |
| | `order_date`, `status` | 下单日期；`completed` 已完成、`pending` 待处理、`cancelled` 已取消 |
| `order_items` | `order_item_id`, `order_id`, `product_id` | 明细编号、订单、产品 |
| | `quantity`, `unit_price`, `discount_amount` | 数量、成交单价（元）、该行总优惠金额（元） |

关系：部门 → 员工 → 订单；客户 → 订单 → 订单明细 ← 产品。订单为交易单据，一条明细对应订单中的一种产品。

## 统计口径

- 订单明细金额 = `quantity * unit_price - discount_amount`。销售额统计 `completed` 订单，默认按 `order_date` 归属日期。
- 订单数量按 `orders.order_id` 计数。连接明细后要使用 `COUNT(DISTINCT orders.order_id)`，以免一笔多条明细的订单被重复计算。
- 订单金额是模拟成交金额，不代表实际收款。示例没有付款、退货、税率、库存、批次、序列号、注册证或有效期。
- 员工、客户区域和产品类别为当前属性，并非历史快照。员工调岗或产品变更类别后，用当前属性统计历史数据可能改变归属。
- 所有日期是 ISO 格式（`YYYY-MM-DD`）。SQLite 使用文本保存日期；PostgreSQL 脚本使用 `DATE`。金额以元为单位。SQLite 的 `NUMERIC` 不保证十进制定点精度；这些示例价格和优惠为整数，正式财务系统应采用适当的精确金额方案。
- 业务口径写在这份说明中，当前项目不会自动将此文档传给模型。提问时应说明统计口径；如果要自动理解口径，需要另外实现元数据注入。

## 直接接入现有 SQLite 项目

已生成 `data/medical_sales.db`。在项目根目录的 `.env` 中配置：

```dotenv
DB_DIALECT=sqlite
DB_URI=sqlite:///data/medical_sales.db
APP_DB_URI=sqlite:///data/app.db
```

如已有应用数据库路径，保留原来的 `APP_DB_URI` 即可。修改后重启 MCP 服务及 FastAPI。原有业务库 `data/business.db` 和应用数据库不会被生成脚本修改。项目内置的评测题针对 ELN 示例库，不适用于这套销售库。

数据库文件位于被 Git 忽略的 `data/` 中。若文件不存在，可以从项目根目录重新生成；脚本拒绝覆盖已有文件：

```bash
python3 examples/medical_sales/generate.py --sqlite data/medical_sales.db
```

## 导入 PostgreSQL

`postgres_seed.sql` 是独立导入脚本，使用 `public` schema，供已经创建好的**空** PostgreSQL 数据库使用；导入账号需要创建表和索引的权限。该操作会向目标数据库写入数据，请先确认连接的是示例库：

```bash
psql "$MEDICAL_DEMO_POSTGRES_URI" -v ON_ERROR_STOP=1 -f examples/medical_sales/postgres_seed.sql
```

设置 `MEDICAL_DEMO_POSTGRES_URI` 为你自己的示例库连接字符串。生成的表名若已存在，脚本会报错，不会删除已有表。导入后，项目连接时应使用仅有必要读取权限的数据库账号。业务数据库和应用数据库分别通过 `DB_URI`、`APP_DB_URI` 配置。

本环境未安装 PostgreSQL，脚本未在 PostgreSQL 服务上执行。如需重新生成到新文件：

```bash
python3 examples/medical_sales/generate.py --postgres-sql /tmp/medical_sales_seed.sql
```

## 可以尝试的问题

1. 各区域有多少家医院客户？
2. 目前在售的监护设备有哪些？
3. 2026 年每个月有多少笔已完成订单？
4. 按已完成订单的明细金额计算，各地区的销售额是多少？
5. 2026 年销售额最高的前 10 家客户是谁？只统计已完成订单。
6. 哪些产品从未出现在已完成订单中？
