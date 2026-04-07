from __future__ import annotations

from l3_node.intent_gateway.workspace_db_context import (
    _score_example,
    load_db_semantics_snippet,
    load_golden_sql_fewshot,
)


def test_score_example_prefers_overlap() -> None:
    a = _score_example("哪个水果缺货了", {"q": "哪个水果缺货", "sql": "SELECT 1", "tags": ["水果"]})
    b = _score_example("哪个水果缺货了", {"q": " unrelated ", "sql": "SELECT 2", "tags": ["x"]})
    assert a >= b


def test_load_semantics_missing_file() -> None:
    assert load_db_semantics_snippet("/nonexistent/path/xxx", max_chars=100) == ""


def test_golden_fewshot_from_temp(tmp_path) -> None:
    ws = tmp_path / "w"
    ws.mkdir()
    (ws / "golden_sql_examples.jsonl").write_text(
        '{"q":"缺货","sql":"SELECT 1","tags":["a"]}\n'
        '{"q":"低库存","sql":"SELECT 2","tags":["b"]}\n',
        encoding="utf-8",
    )
    out = load_golden_sql_fewshot(str(ws), "查询缺货商品", max_chars=500, max_examples=2)
    assert "SELECT" in out
