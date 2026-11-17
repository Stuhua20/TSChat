"""生成独立的虚构医疗器械销售示例库，不连接或覆盖已有数据库。"""
from __future__ import annotations

import argparse
import os
import random
import sqlite3
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

SCHEMA = """
CREATE TABLE departments (
    department_id INTEGER PRIMARY KEY,
    department_name TEXT NOT NULL UNIQUE
);
CREATE TABLE employees (
    employee_id INTEGER PRIMARY KEY,
    employee_name TEXT NOT NULL,
    department_id INTEGER NOT NULL REFERENCES departments(department_id),
    job_title TEXT NOT NULL,
    hire_date {date_type} NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active', 'inactive'))
);
CREATE TABLE customers (
    customer_id INTEGER PRIMARY KEY,
    customer_name TEXT NOT NULL UNIQUE,
    customer_type TEXT NOT NULL CHECK (customer_type IN ('hospital', 'clinic', 'distributor')),
    province TEXT NOT NULL,
    region TEXT NOT NULL,
    created_at {date_type} NOT NULL
);
CREATE TABLE products (
    product_id INTEGER PRIMARY KEY,
    product_code TEXT NOT NULL UNIQUE,
    product_name TEXT NOT NULL,
    category TEXT NOT NULL,
    device_class TEXT NOT NULL CHECK (device_class IN ('I', 'II', 'III')),
    manufacturer TEXT NOT NULL,
    unit TEXT NOT NULL,
    list_price NUMERIC(12, 2) NOT NULL CHECK (list_price >= 0),
    status TEXT NOT NULL CHECK (status IN ('active', 'inactive'))
);
CREATE TABLE orders (
    order_id INTEGER PRIMARY KEY,
    order_number TEXT NOT NULL UNIQUE,
    customer_id INTEGER NOT NULL REFERENCES customers(customer_id),
    employee_id INTEGER NOT NULL REFERENCES employees(employee_id),
    order_date {date_type} NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'completed', 'cancelled'))
);
CREATE TABLE order_items (
    order_item_id INTEGER PRIMARY KEY,
    order_id INTEGER NOT NULL REFERENCES orders(order_id),
    product_id INTEGER NOT NULL REFERENCES products(product_id),
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    unit_price NUMERIC(12, 2) NOT NULL CHECK (unit_price >= 0),
    discount_amount NUMERIC(12, 2) NOT NULL DEFAULT 0
        CHECK (discount_amount >= 0 AND discount_amount <= quantity * unit_price),
    UNIQUE (order_id, product_id)
);
CREATE INDEX idx_employees_department ON employees(department_id);
CREATE INDEX idx_customers_region ON customers(region);
CREATE INDEX idx_orders_customer ON orders(customer_id);
CREATE INDEX idx_orders_employee ON orders(employee_id);
CREATE INDEX idx_orders_date_status ON orders(order_date, status);
CREATE INDEX idx_order_items_order ON order_items(order_id);
CREATE INDEX idx_order_items_product ON order_items(product_id);
"""


def make_data() -> dict[str, list[tuple]]:
    rng = random.Random(20261117)
    data: dict[str, list[tuple]] = {name: [] for name in (
        "departments", "employees", "customers", "products", "orders", "order_items"
    )}
    for i, name in enumerate(("华东销售部", "华北销售部", "华南销售部", "西部销售部"), 1):
        data["departments"].append((i, name))
    surnames = "张王李刘陈杨赵黄周吴徐孙马朱胡郭林何高郑"
    given = ("明", "敏", "伟", "静", "磊", "芳", "杰", "婷", "宇", "洁")
    for i in range(1, 21):
        data["employees"].append((i, surnames[i - 1] + given[(i - 1) % 10],
                                  (i - 1) % 4 + 1, "销售经理" if i <= 4 else "销售专员",
                                  (date(2023, 1, 1) + timedelta(days=i * 13)).isoformat(), "active"))
    locations = (("上海", "华东"), ("江苏", "华东"), ("浙江", "华东"),
                 ("北京", "华北"), ("河北", "华北"), ("广东", "华南"),
                 ("广西", "华南"), ("四川", "西部"), ("陕西", "西部"), ("重庆", "西部"))
    suffixes = (("hospital", "医疗中心"), ("clinic", "诊疗门诊部"), ("distributor", "医疗器械经销有限公司"))
    for i in range(1, 101):
        province, region = locations[(i - 1) % len(locations)]
        kind, suffix = suffixes[(i - 1) % len(suffixes)]
        data["customers"].append((i, f"{province}{i:03d}{suffix}", kind, province, region,
                                  (date(2024, 1, 1) + timedelta(days=(i * 3) % 300)).isoformat()))
    # 类别、名称、示例管理类别、计量单位、目录价；均为虚构演示属性。
    templates = (
        ("监护设备", "多参数监护仪", "II", "台", 18000),
        ("诊断设备", "便携式心电图机", "II", "台", 12000),
        ("诊断设备", "医用电子血压计", "II", "台", 1800),
        ("影像设备", "超声诊断设备", "II", "台", 85000),
        ("检验设备", "离心机", "I", "台", 7500),
        ("康复设备", "康复训练器", "I", "台", 4200),
        ("手术器械", "手术器械套装", "I", "套", 2600),
        ("医用耗材", "一次性医用耗材包", "II", "盒", 120),
        ("医用耗材", "无菌导管套装", "III", "盒", 380),
        ("医用耗材", "医用敷料", "II", "盒", 90),
    )
    for i in range(1, 51):
        category, name, device_class, unit, price = templates[(i - 1) % len(templates)]
        version = (i - 1) // len(templates) + 1
        data["products"].append((i, f"MD-{i:04d}", f"{name} {version}型", category,
                                 device_class, f"器械制造商{(i - 1) % 5 + 1}", unit,
                                 price + (version - 1) * max(10, price // 20),
                                 "inactive" if i in (41, 42) else "active"))
    start = date(2025, 1, 1)
    # 截至 2026-11-16，确保时间分布固定、数据可以重现。
    span = (date(2026, 11, 16) - start).days
    item_id = 1
    for i in range(1, 601):
        order_date = start + timedelta(days=rng.randrange(span + 1))
        customer = data["customers"][rng.randrange(len(data["customers"]))]
        department = {"华东": 1, "华北": 2, "华南": 3, "西部": 4}[customer[4]]
        employee = rng.choice([row for row in data["employees"] if row[2] == department])
        status = rng.choices(("completed", "pending", "cancelled"), (78, 15, 7))[0]
        data["orders"].append((i, f"SO-{i:05d}", customer[0], employee[0], order_date.isoformat(), status))
        for product in rng.sample(data["products"][:40], rng.randint(1, 4)):
            quantity = rng.randint(1, 30 if product[6] == "盒" else 5)
            unit_price = product[7] * rng.choice((90, 95, 100)) // 100
            discount = (quantity * unit_price * rng.choice((0, 0, 2, 5))) // 100
            data["order_items"].append((item_id, i, product[0], quantity, unit_price, discount))
            item_id += 1
    return data


def literal(value: object) -> str:
    if isinstance(value, int):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


def build_sql(data: dict[str, list[tuple]], postgres: bool) -> str:
    parts = ["-- 虚构数据，仅供演示；请导入空数据库。\nBEGIN;\n"]
    if postgres:
        parts.append("SET LOCAL search_path TO public;\n")
    parts.append(SCHEMA.format(date_type="DATE" if postgres else "TEXT"))
    for table, rows in data.items():
        for row in rows:
            parts.append(f"INSERT INTO {table} VALUES ({', '.join(map(literal, row))});\n")
    parts.append("COMMIT;\n")
    return "".join(parts)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sqlite", type=Path, help="新建 SQLite 文件，拒绝覆盖")
    parser.add_argument("--postgres-sql", type=Path, help="生成 PostgreSQL 导入脚本，拒绝覆盖")
    args = parser.parse_args()
    if not args.sqlite and not args.postgres_sql:
        parser.error("请指定 --sqlite 和/或 --postgres-sql")
    data = make_data()
    if args.sqlite:
        path = args.sqlite
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        try:
            with sqlite3.connect(path) as connection:
                connection.execute("PRAGMA foreign_keys = ON")
                connection.executescript(build_sql(data, postgres=False))
                if connection.execute("PRAGMA foreign_key_check").fetchall():
                    raise ValueError("外键检查失败")
        except Exception:
            path.unlink(missing_ok=True)
            raise
        print(f"SQLite: {path}")
    if args.postgres_sql:
        path = args.postgres_sql
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8") as output:
            output.write(build_sql(data, postgres=True))
        print(f"PostgreSQL SQL: {path}")
    print("数据行数:", ", ".join(f"{name}={len(rows)}" for name, rows in data.items()))


if __name__ == "__main__":
    main()
