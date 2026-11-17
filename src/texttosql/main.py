"""
Text-to-SQL 命令行工具
======================
用法：
    python -m texttosql.main "上个月下了多少订单？"

或不带参数运行，进入交互式 REPL 模式：
    python -m texttosql.main
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s %(name)s: %(message)s",
    stream=sys.stderr,
)


async def _run(question: str) -> None:
    from texttosql.graph import run_pipeline  # 局部导入以加快启动速度

    print(f"\n问题：{question}\n{'─' * 60}")
    state = await run_pipeline(question)

    final_sql = state.get("final_sql_query")
    exec_result = state.get("execution_result") or {}

    if final_sql and exec_result.get("status") == "success":
        print(f"✅  SQL：\n{final_sql}\n")
        rows = exec_result.get("rows", [])
        columns = state.get("result_columns") or []
        row_count = exec_result.get("row_count", 0)
        display_rows = rows[:50]

        if columns and rows:
            # 计算列宽
            col_widths = [len(str(c)) for c in columns]
            for row in display_rows:
                for i, val in enumerate(row):
                    col_widths[i] = max(col_widths[i], len(str(val) if val is not None else "NULL"))

            # 表头
            header = "  ".join(str(c).ljust(col_widths[i]) for i, c in enumerate(columns))
            divider = "  ".join("─" * col_widths[i] for i in range(len(columns)))
            print(f"结果（共 {row_count} 行）：")
            print(header)
            print(divider)
            for row in display_rows:
                line = "  ".join(str(v if v is not None else "NULL").ljust(col_widths[i]) for i, v in enumerate(row))
                print(line)
            if row_count > 50:
                print(f"  …（其余 {row_count - 50} 行未显示）")
        else:
            # 查询没有列元数据时的回退处理
            print(f"结果（共 {row_count} 行）：")
            for row in display_rows:
                print(" ", row)
    else:
        print("❌  查询未完成。")
        if state.get("error"):
            print(f"   错误：{state['error']}")
        elif exec_result.get("error_message"):
            print(f"   执行错误：{exec_result['error_message']}")
        val = state.get("validation_result") or {}
        if val.get("errors"):
            print(f"   校验错误：{val['errors']}")
        print(f"\n最后一次尝试的 SQL：\n{state.get('sql_query', 'N/A')}")


def main() -> None:
    if len(sys.argv) > 1:
        question = " ".join(sys.argv[1:])
        asyncio.run(_run(question))
    else:
        # 交互式 REPL
        print("Text-to-SQL 交互模式（按 Ctrl-C 退出）\n")
        while True:
            try:
                question = input("请输入问题 > ").strip()
            except (KeyboardInterrupt, EOFError):
                print("\n再见！")
                break
            if not question:
                continue
            asyncio.run(_run(question))


if __name__ == "__main__":
    main()
