from __future__ import annotations


def test_sea_without_sea_key_but_with_cn_key_uses_cn_endpoint(monkeypatch) -> None:
    from core.brain.llm.dashscope_regional import (
        get_dashscope_regional_api_base,
        get_jachin_active_region,
    )

    monkeypatch.setenv("JACHIN_ACTIVE_REGION", "SEA")
    monkeypatch.setenv("DASHSCOPE_API_KEY_CN", "sk-cn")
    monkeypatch.delenv("DASHSCOPE_API_KEY_SEA", raising=False)
    monkeypatch.delenv("DASHSCOPE_API_BASE", raising=False)
    monkeypatch.delenv("DASHSCOPE_API_BASE_SEA", raising=False)

    assert get_jachin_active_region() == "CN"
    assert "dashscope.aliyuncs.com" in get_dashscope_regional_api_base()
    assert "dashscope-intl" not in get_dashscope_regional_api_base()
