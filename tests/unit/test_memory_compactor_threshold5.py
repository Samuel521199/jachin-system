"""
记忆坍缩（梦境合并）：阈值为 5 条时触发 compact_local_memory_if_needed。

[已跳过] Memory Nexus 迁移后 ``compact_local_memory_if_needed`` 全局提前返回，旧 JSON 坍缩测试不再适用。
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.skip(reason="JSON memory compactor deprecated; compact_local_memory_if_needed is a no-op")

from l3_node.memory_compactor import _MEMORY_COMPACT_JSON_KEY


def _fake_llm_response_json(merged_items: list[dict]) -> MagicMock:
    """模拟 LiteLLM 返回的 message.content（单键对象，与 compactor 约定一致）。"""
    body = json.dumps({_MEMORY_COMPACT_JSON_KEY: merged_items}, ensure_ascii=False)
    msg = MagicMock()
    msg.content = body
    choice = MagicMock()
    choice.message = msg
    resp = MagicMock()
    resp.choices = [choice]
    return resp


def _entries(n: int, base_ts: float = 1_700_000_000.0) -> list[dict]:
    return [
        {"tag": "fact", "content": f"记忆片段-{i}", "source": "test", "timestamp": base_ts + i}
        for i in range(n)
    ]


@pytest.mark.asyncio
async def test_compact_triggers_when_entries_exceed_threshold_five(tmp_path: Path) -> None:
    """6 条 > threshold=5，应调用 LLM 并原子写入合并结果。"""
    mem_file = tmp_path / "l3_local.json"
    before = _entries(6)
    mem_file.write_text(json.dumps(before, ensure_ascii=False), encoding="utf-8")

    merged_out = [
        {"tag": "fact", "content": "【测试合并】六条合一", "source": "test", "timestamp": 1_700_000_100.0},
    ]
    mock_acompletion = AsyncMock(return_value=_fake_llm_response_json(merged_out))

    with (
        patch("litellm.acompletion", mock_acompletion),
        patch("l3_node.memory_compact_schedule.record_compact_completed", lambda: None),
    ):
        from l3_node.memory_compactor import compact_local_memory_if_needed

        report = await compact_local_memory_if_needed(str(mem_file), threshold=5)

    assert "坍缩完成" in report
    assert "原条目数: 6" in report
    after = json.loads(mem_file.read_text(encoding="utf-8"))
    assert isinstance(after, list)
    assert len(after) == 1
    assert after[0]["content"] == "【测试合并】六条合一"
    mock_acompletion.assert_awaited()
    # 成功后影子文件应删除
    assert not (tmp_path / "l3_local.json.shadow").exists()


@pytest.mark.asyncio
async def test_compact_skips_when_at_or_below_threshold_five(tmp_path: Path) -> None:
    """恰好 5 条 ≤ threshold=5，不触发坍缩。"""
    mem_file = tmp_path / "l3_local.json"
    before = _entries(5)
    mem_file.write_text(json.dumps(before, ensure_ascii=False), encoding="utf-8")

    mock_acompletion = AsyncMock(return_value=_fake_llm_response_json([]))

    with (
        patch("litellm.acompletion", mock_acompletion),
        patch("l3_node.memory_compact_schedule.record_compact_completed", lambda: None),
    ):
        from l3_node.memory_compactor import compact_local_memory_if_needed

        report = await compact_local_memory_if_needed(str(mem_file), threshold=5)

    assert report == ""
    assert json.loads(mem_file.read_text(encoding="utf-8")) == before
    mock_acompletion.assert_not_awaited()


@pytest.mark.asyncio
async def test_compact_disabled_env_returns_empty(tmp_path: Path) -> None:
    """JACHIN_MEMORY_COMPACT_ENABLED=0 时不调用 LLM。"""
    mem_file = tmp_path / "l3_local.json"
    mem_file.write_text(json.dumps(_entries(10), ensure_ascii=False), encoding="utf-8")
    mock_acompletion = AsyncMock(return_value=_fake_llm_response_json([{"tag": "x", "content": "y"}]))

    with (
        patch.dict("os.environ", {"JACHIN_MEMORY_COMPACT_ENABLED": "0"}),
        patch("litellm.acompletion", mock_acompletion),
    ):
        from l3_node.memory_compactor import compact_local_memory_if_needed

        report = await compact_local_memory_if_needed(str(mem_file), threshold=5)

    assert report == ""
    mock_acompletion.assert_not_awaited()


@pytest.mark.asyncio
async def test_compact_force_ignores_threshold(tmp_path: Path) -> None:
    """显式 force=True：仅 2 条也调用 LLM，无视 threshold=150。"""
    mem_file = tmp_path / "l3_local.json"
    before = _entries(2)
    mem_file.write_text(json.dumps(before, ensure_ascii=False), encoding="utf-8")
    merged_out = [{"tag": "fact", "content": "forced-merge", "source": "test", "timestamp": 1_700_000_050.0}]
    mock_acompletion = AsyncMock(return_value=_fake_llm_response_json(merged_out))

    with (
        patch("litellm.acompletion", mock_acompletion),
        patch("l3_node.memory_compact_schedule.record_compact_completed", lambda: None),
    ):
        from l3_node.memory_compactor import compact_local_memory_if_needed

        report = await compact_local_memory_if_needed(str(mem_file), threshold=150, force=True)

    assert "坍缩完成" in report
    assert "原条目数: 2" in report
    after = json.loads(mem_file.read_text(encoding="utf-8"))
    assert len(after) == 1
    assert after[0]["content"] == "forced-merge"
    mock_acompletion.assert_awaited()


@pytest.mark.asyncio
async def test_compact_force_empty_file_returns_hint(tmp_path: Path) -> None:
    """force=True 但主库为空数组：不调 LLM，返回简短说明。"""
    mem_file = tmp_path / "l3_local.json"
    mem_file.write_text("[]", encoding="utf-8")
    mock_acompletion = AsyncMock()

    with (
        patch("litellm.acompletion", mock_acompletion),
        patch("l3_node.memory_compact_schedule.record_compact_completed", lambda: None),
    ):
        from l3_node.memory_compactor import compact_local_memory_if_needed

        report = await compact_local_memory_if_needed(str(mem_file), threshold=150, force=True)

    assert "暂无条目" in report
    mock_acompletion.assert_not_awaited()


@pytest.mark.asyncio
async def test_compact_not_force_respects_default_threshold(tmp_path: Path) -> None:
    """force=False：2 条 < 150，不调用 LLM。"""
    mem_file = tmp_path / "l3_local.json"
    before = _entries(2)
    mem_file.write_text(json.dumps(before, ensure_ascii=False), encoding="utf-8")
    mock_acompletion = AsyncMock(return_value=_fake_llm_response_json([{"tag": "x", "content": "y"}]))

    with patch("litellm.acompletion", mock_acompletion):
        from l3_node.memory_compactor import compact_local_memory_if_needed

        report = await compact_local_memory_if_needed(str(mem_file), threshold=150, force=False)

    assert report == ""
    assert json.loads(mem_file.read_text(encoding="utf-8")) == before
    mock_acompletion.assert_not_awaited()


@pytest.mark.asyncio
async def test_compact_force_ignores_threshold(tmp_path: Path) -> None:
    """显式 force=True：仅 2 条也调用 LLM，无视 threshold=150。"""
    mem_file = tmp_path / "l3_local.json"
    before = _entries(2)
    mem_file.write_text(json.dumps(before, ensure_ascii=False), encoding="utf-8")
    merged_out = [{"tag": "fact", "content": "forced-merge", "source": "test", "timestamp": 1_700_000_050.0}]
    mock_acompletion = AsyncMock(return_value=_fake_llm_response_json(merged_out))

    with (
        patch("litellm.acompletion", mock_acompletion),
        patch("l3_node.memory_compact_schedule.record_compact_completed", lambda: None),
    ):
        from l3_node.memory_compactor import compact_local_memory_if_needed

        report = await compact_local_memory_if_needed(str(mem_file), threshold=150, force=True)

    assert "坍缩完成" in report
    assert "原条目数: 2" in report
    after = json.loads(mem_file.read_text(encoding="utf-8"))
    assert len(after) == 1
    assert after[0]["content"] == "forced-merge"
    mock_acompletion.assert_awaited()


@pytest.mark.asyncio
async def test_compact_force_empty_file_returns_hint(tmp_path: Path) -> None:
    """force=True 但主库为空数组：不调 LLM，返回简短说明。"""
    mem_file = tmp_path / "l3_local.json"
    mem_file.write_text("[]", encoding="utf-8")
    mock_acompletion = AsyncMock()

    with (
        patch("litellm.acompletion", mock_acompletion),
        patch("l3_node.memory_compact_schedule.record_compact_completed", lambda: None),
    ):
        from l3_node.memory_compactor import compact_local_memory_if_needed

        report = await compact_local_memory_if_needed(str(mem_file), threshold=150, force=True)

    assert "暂无条目" in report
    mock_acompletion.assert_not_awaited()


@pytest.mark.asyncio
async def test_compact_not_force_respects_default_threshold(tmp_path: Path) -> None:
    """force=False：2 条 < 150，不调用 LLM。"""
    mem_file = tmp_path / "l3_local.json"
    before = _entries(2)
    mem_file.write_text(json.dumps(before, ensure_ascii=False), encoding="utf-8")
    mock_acompletion = AsyncMock(return_value=_fake_llm_response_json([{"tag": "x", "content": "y"}]))

    with patch("litellm.acompletion", mock_acompletion):
        from l3_node.memory_compactor import compact_local_memory_if_needed

        report = await compact_local_memory_if_needed(str(mem_file), threshold=150, force=False)

    assert report == ""
    assert json.loads(mem_file.read_text(encoding="utf-8")) == before
    mock_acompletion.assert_not_awaited()
