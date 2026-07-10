"""PMO 03 主轴 fs_read：平面表分两段读入 LLM。"""

from __future__ import annotations

from types import SimpleNamespace

from l3_node.pmo_agent_policy import (
    PMO_03_VIEW_ID,
    _pmo_03_get_chunks_read,
    _pmo_maybe_chunk_03_fs_read_observation,
    _pmo_slice_03_markdown_for_llm,
)


def _sample_03_md(n_rows: int) -> str:
    header = """## 子表 1: 开发计划

### 层级视图（按 `父记录` 还原父子关系；完整字段仍以平面表为准）

- **`rec1…`** · Requirement: A

| Requirement | priority |
| --- | --- |
"""
    rows = "\n".join(f"| row-{i} | P0 |" for i in range(n_rows))
    return header + rows


def _ctx() -> SimpleNamespace:
    return SimpleNamespace(
        metadata={"_implicit_channel": "pmo_copilot_cli", "_pmo_bi_project_context_ok": True}
    )


def test_slice_03_chunk1_and_chunk2():
    md = _sample_03_md(1500)
    c1 = _pmo_slice_03_markdown_for_llm(
        md, chunk_no=1, start_row=0, end_row=1000, chunk_total=2, include_preamble=True
    )
    assert "row-0 |" in c1
    assert "row-999 |" in c1
    assert "row-1000 |" not in c1
    assert "第 1/2 段" in c1

    c2 = _pmo_slice_03_markdown_for_llm(
        md, chunk_no=2, start_row=1000, end_row=2000, chunk_total=2, include_preamble=False
    )
    assert "row-1000 |" in c2
    assert "row-1499 |" in c2
    assert "续读" in c2


def test_maybe_chunk_two_reads_same_path():
    md = _sample_03_md(1500)
    inp = '{"file_path": "/x/03_foo_vewpI8lyYw.md"}'
    ctx = _ctx()

    o1 = _pmo_maybe_chunk_03_fs_read_observation(md, inp, ctx)
    assert "row-999 |" in o1
    assert _pmo_03_get_chunks_read(ctx.metadata) == [1]

    o2 = _pmo_maybe_chunk_03_fs_read_observation(md, inp, ctx)
    assert "row-1000 |" in o2
    assert "row-1499 |" in o2
    assert _pmo_03_get_chunks_read(ctx.metadata) == [1, 2]

    o3 = _pmo_maybe_chunk_03_fs_read_observation(md, inp, ctx)
    assert "均已读过" in o3


def test_single_chunk_when_table_small():
    md = _sample_03_md(50)
    ctx = _ctx()
    inp = '{"file_path": "/x/03_vewpI8lyYw.md"}'
    out = _pmo_maybe_chunk_03_fs_read_observation(md, inp, ctx)
    assert "row-49 |" in out
    assert _pmo_03_get_chunks_read(ctx.metadata) == [1]
