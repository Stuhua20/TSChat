"""Compare result values, preserving duplicates and optionally row order."""
from __future__ import annotations

import math
from typing import Any

MAX_ROWS = 200


def _value_equal(left: Any, right: Any) -> bool:
    if isinstance(left, (int, float)) and not isinstance(left, bool) and isinstance(right, (int, float)) and not isinstance(right, bool):
        return math.isclose(left, right, rel_tol=1e-9, abs_tol=1e-9)
    return type(left) is type(right) and left == right


def compare_results(expected: dict, actual: dict, ordered: bool = False) -> tuple[str, str]:
    expected_rows, actual_rows = expected.get("rows", []), actual.get("rows", [])
    if len(expected_rows) > MAX_ROWS or len(actual_rows) > MAX_ROWS:
        return "unknown", f"结果超过 {MAX_ROWS} 行，无法自动比较"
    if len(expected.get("columns", [])) != len(actual.get("columns", [])):
        return "incorrect", "结果列数不同"
    if len(expected_rows) != len(actual_rows):
        return "incorrect", "结果行数不同"

    def same(a: list, b: list) -> bool:
        return len(a) == len(b) and all(_value_equal(x, y) for x, y in zip(a, b))

    if ordered:
        return ("correct", "结果一致") if all(same(a, b) for a, b in zip(expected_rows, actual_rows)) else ("incorrect", "结果或顺序不同")
    # Bipartite matching preserves duplicates even when float tolerance overlaps.
    matches: dict[int, int] = {}
    def match(index: int, seen: set[int]) -> bool:
        for j, row in enumerate(actual_rows):
            if j not in seen and same(expected_rows[index], row):
                seen.add(j)
                if j not in matches or match(matches[j], seen):
                    matches[j] = index
                    return True
        return False
    return ("correct", "结果一致") if all(match(i, set()) for i in range(len(expected_rows))) else ("incorrect", "结果值或重复行数量不同")
