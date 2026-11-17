import json
from types import SimpleNamespace

import pytest

from texttosql.web import description


@pytest.mark.asyncio
async def test_description_uses_bounded_complete_rows_and_reports_scope(monkeypatch):
    calls = []

    class Model:
        async def ainvoke(self, messages):
            calls.append(messages)
            return SimpleNamespace(content="查询返回了一些记录。")

    monkeypatch.setattr(description, "get_llm", lambda: Model())
    text, count = await description.describe_result("查一下", ["value"], [[i] for i in range(100)], 100)
    data = json.loads(calls[0][1].content)
    assert text == "查询返回了一些记录。"
    assert count == 50
    assert data["result"]["rows"] == [[i] for i in range(50)]
    assert data["result"]["total_rows"] == 100


@pytest.mark.asyncio
async def test_description_handles_empty_large_and_model_failure(monkeypatch):
    class Model:
        async def ainvoke(self, messages):
            assert json.loads(messages[1].content)["result"]["rows"] == []
            return SimpleNamespace(content="没有找到匹配结果。")

    monkeypatch.setattr(description, "get_llm", lambda: Model())
    assert await description.describe_result("查询", ["value"], [], 0) == ("没有找到匹配结果。", 0)
    assert await description.describe_result("查询", ["value"], [["x" * 20000]], 1) == (None, 0)

    class BrokenModel:
        async def ainvoke(self, messages):
            raise RuntimeError("unavailable")

    monkeypatch.setattr(description, "get_llm", lambda: BrokenModel())
    assert await description.describe_result("查询", ["value"], [[1]], 1) == (None, 1)
