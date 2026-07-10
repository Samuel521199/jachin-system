"""
util:* / sys:* 原生工具烟测与行为断言（工具总数用下限断言，避免新增工具时测试误伤）。

运行（避免根 conftest 拉 ray / cov）：
  python -m pytest tests/unit/test_core_util_tools.py -v --override-ini="addopts=-v --tb=short --strict-markers" --noconftest
"""
from __future__ import annotations

import json
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest

from l3_node.primitives.tools import core_util_tools as cut
from l3_node.primitives.tools.core_util_tools import (
    dispatch_util_native_tool,
    util_tool_ids,
)


def test_util_native_tool_ids_registered_minimum() -> None:
    """工具数量会随业务增长；只断言下限，并校验核心 id 仍存在。"""
    assert len(util_tool_ids()) >= 19
    for tid in (
        "util:datetime_calc",
        "util:cron_explain",
        "util:precise_math",
        "util:uuid_gen",
        "util:hash_crypto",
        "util:json_jq",
        "util:regex_test",
        "util:http_ping",
        "util:stealth_extract",
        "util:dns_lookup",
        "util:get_weather_lite",
        "util:ab_test_calc",
        "util:fake_data_gen",
        "util:text_diff",
        "util:funnel_calc",
        "util:desktop_message_box",
        "util:generate_office_doc",
        "util:compose_long_document",
        "sys:health_stats",
        "sys:list_env_safe",
    ):
        assert tid in util_tool_ids()


def test_dispatch_unknown_returns_error() -> None:
    r = dispatch_util_native_tool("util:does_not_exist")
    assert r["ok"] is False
    assert "未知" in str(r.get("error", ""))


def test_util_uuid_gen() -> None:
    r = cut.run_uuid_gen()
    assert r["ok"] is True
    u = r["result"]["uuid"]
    assert len(u) == 36 and u.count("-") == 4


def test_util_precise_math_decimal() -> None:
    r = cut.run_precise_math(expression="1024.56 * 3.14 / 2")
    assert r["ok"] is True
    assert "1608.5592" in r["result"]["decimal_str"] or r["result"]["decimal_str"].startswith("1608.55")


def test_util_precise_math_rejects_injection() -> None:
    r = cut.run_precise_math(expression="__import__('os')")
    assert r["ok"] is False


def test_util_hash_crypto() -> None:
    r = cut.run_hash_crypto(text="hello", algo="md5")
    assert r["ok"] is True
    assert r["result"]["hex"] == "5d41402abc4b2a76b9719d911017c592"
    r2 = cut.run_hash_crypto(text="hello", algo="base64_encode")
    assert r2["ok"] is True
    assert r2["result"]["b64"] == "aGVsbG8="
    r3 = cut.run_hash_crypto(text="aGVsbG8=", algo="base64_decode")
    assert r3["ok"] is True
    assert r3["result"]["output_text"] == "hello"


def test_util_json_jq() -> None:
    payload = {"user": {"items": [{"name": "a"}, {"name": "b"}]}}
    r = cut.run_json_jq(json_string=json.dumps(payload), path="user.items.0.name")
    assert r["ok"] is True
    assert r["result"]["value"] == "a"


def test_util_regex_test() -> None:
    r = cut.run_regex_test(pattern=r"(\d+)", test_cases=["a1b", "no"])
    assert r["ok"] is True
    cases = r["result"]["cases"]
    assert cases[0]["matched"] is True
    assert cases[0]["groups"] == ["1"]
    assert cases[1]["matched"] is False


def test_util_datetime_calc_add_days() -> None:
    r = cut.run_datetime_calc(base_time="2020-01-01T12:00:00+08:00", add_days=10, target_timezone="Asia/Shanghai")
    assert r["ok"] is True
    assert "2020-01-11" in r["result"]["iso_local"]


def test_util_cron_explain() -> None:
    r = cut.run_cron_explain(cron_expr="0 9 * * 1")
    assert r["ok"] is True
    assert r["result"].get("engine") in ("apscheduler", "croniter")
    assert "summary_zh" in r["result"]
    assert len(r["result"]["next_three_iso_utc"]) == 3


def test_util_dns_lookup_localhost() -> None:
    r = cut.run_dns_lookup(domain="localhost")
    assert r["ok"] is True
    assert "127.0.0.1" in r["result"]["ips"]


@patch("l3_node.primitives.tools.core_util_tools._stealth_try_inprocess_fast")
def test_util_stealth_extract_in_process_fast_ok(mock_fast: MagicMock) -> None:
    mock_fast.return_value = (
        {
            "text": "plain",
            "html_excerpt": "<div>x</div>",
            "http_status": 200,
        },
        None,
    )
    r = cut.run_stealth_extract(url="https://example.com/page")
    assert r["ok"] is True
    assert r["result"]["url"] == "https://example.com/page"
    assert r["result"]["content"]["text"] == "plain"
    assert r["result"]["content"]["via"] == "in_process_fast"


@patch("requests.post")
@patch("l3_node.primitives.tools.core_util_tools._stealth_sidecar_healthcheck", return_value=True)
@patch("l3_node.primitives.tools.core_util_tools._stealth_try_inprocess_fast")
def test_util_stealth_extract_sidecar_heavy_after_cf_block(
    mock_fast: MagicMock,
    _mock_hc: MagicMock,
    mock_post: MagicMock,
) -> None:
    mock_fast.return_value = (
        {
            "text": "",
            "html_excerpt": "<title>Just a moment...</title>",
            "http_status": 200,
        },
        None,
    )
    mock_resp = MagicMock()
    mock_resp.ok = True
    mock_resp.json.return_value = {
        "text": "heavy-plain",
        "html_excerpt": "<div>ok</div>",
        "http_status": 200,
    }
    mock_post.return_value = mock_resp
    r = cut.run_stealth_extract(url="https://example.com/cf")
    assert r["ok"] is True
    assert r["result"]["content"]["via"] == "sidecar_heavy"
    assert r["result"]["content"]["text"] == "heavy-plain"
    mock_post.assert_called_once()
    assert mock_post.call_args[1]["timeout"] == 15


@patch("requests.post")
@patch("l3_node.primitives.tools.core_util_tools._stealth_sidecar_healthcheck", return_value=False)
@patch("l3_node.primitives.tools.core_util_tools._stealth_try_inprocess_fast")
def test_util_stealth_extract_sidecar_unreachable_short_circuit(
    mock_fast: MagicMock,
    _mock_hc: MagicMock,
    mock_post: MagicMock,
) -> None:
    mock_fast.return_value = (None, OSError("no curl"))
    r = cut.run_stealth_extract(url="https://example.com/")
    assert r["ok"] is False
    assert "轻装抓取被拦截" in str(r.get("error", ""))
    assert "uvicorn" in str(r.get("error", ""))
    mock_post.assert_not_called()


@patch("l3_node.primitives.tools.core_util_tools.urlopen")
def test_util_http_ping(mock_urlopen: MagicMock) -> None:
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.getcode = lambda: 200
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = lambda *a: None
    mock_urlopen.return_value = mock_resp
    r = cut.run_http_ping(url="https://example.com")
    assert r["ok"] is True
    assert r["result"]["status_code"] == 200
    assert "elapsed_ms" in r["result"]


@patch("l3_node.primitives.tools.core_util_tools.urlopen")
def test_util_get_weather_lite(mock_urlopen: MagicMock) -> None:
    sample = {
        "current_condition": [{"temp_C": "22", "FeelsLikeC": "21", "humidity": "50", "observation_time": "12:00"}],
        "nearest_area": [{"areaName": [{"value": "TestCity"}]}],
    }
    raw = json.dumps(sample).encode("utf-8")
    mock_resp = MagicMock()
    mock_resp.read = lambda: raw
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = lambda *a: None
    mock_urlopen.return_value = mock_resp
    r = cut.run_get_weather_lite(city="TestCity")
    assert r["ok"] is True
    assert r["result"]["temp_C"] == "22"
    assert r["result"].get("queried_as") == "TestCity"
    assert r["result"].get("source") == "wttr.in"


@patch("l3_node.primitives.tools.core_util_tools.urlopen")
def test_util_get_weather_lite_null_condition_no_crash(mock_urlopen: MagicMock) -> None:
    sample = {"current_condition": [None], "nearest_area": []}
    raw = json.dumps(sample).encode("utf-8")
    mock_resp = MagicMock()
    mock_resp.read = lambda: raw
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = lambda *a: None
    mock_urlopen.return_value = mock_resp
    r = cut.run_get_weather_lite(city="X")
    assert r["ok"] is False
    assert "无有效数据" in (r.get("error") or "")


def test_sys_health_stats() -> None:
    pytest.importorskip("psutil")
    r = cut.run_health_stats()
    assert r["ok"] is True
    for k in ("cpu_percent", "memory_free_mb", "disk_free_gb"):
        assert k in r["result"]
        assert float(r["result"][k]) >= 0


def test_sys_list_env_safe_no_values() -> None:
    r = cut.run_list_env_safe()
    assert r["ok"] is True
    keys = r["result"]["keys"]
    assert isinstance(keys, list)
    assert "PATH" in keys or "PATH".upper() in [x.upper() for x in keys]
    assert "values" not in r["result"]
    assert all(isinstance(x, str) for x in keys)


def test_util_ab_test_calc_equal_groups_not_significant() -> None:
    r = cut.run_ab_test_calc(visitors_a=1000, conversions_a=100, visitors_b=1000, conversions_b=100)
    assert r["ok"] is True
    assert r["result"]["conversion_rate_a"] == r["result"]["conversion_rate_b"]
    assert r["result"]["is_significant"] is False
    assert r["result"]["p_value_two_tailed"] > 0.05


def test_util_ab_test_calc_rejects_bad_sample() -> None:
    r = cut.run_ab_test_calc(visitors_a=0, conversions_a=0, visitors_b=100, conversions_b=10)
    assert r["ok"] is False


def test_util_fake_data_gen() -> None:
    pytest.importorskip("faker")
    r = cut.run_fake_data_gen(locale="zh_CN", count=2, fields=["name", "email"])
    assert r["ok"] is True
    assert len(r["result"]["dummy_data"]) == 2
    assert "name" in r["result"]["dummy_data"][0]


def test_util_text_diff() -> None:
    r = cut.run_text_diff(text1="a\nb", text2="a\nc")
    assert r["ok"] is True
    lines = r["result"]["diff_lines"]
    assert any(x.startswith("-") for x in lines)
    assert any(x.startswith("+") for x in lines)


def test_util_funnel_calc() -> None:
    r = cut.run_funnel_calc(initial_traffic=1000, conversion_rates=[0.5, 0.2])
    assert r["ok"] is True
    assert r["result"]["layer_counts"][0] == 1000.0
    assert r["result"]["layer_counts"][-1] == 100.0
    assert r["result"]["roi"] is None
    r2 = cut.run_funnel_calc(initial_traffic=1000, conversion_rates=[0.5, 0.2], cac=1.0, arpu=50.0)
    assert r2["ok"] is True
    assert r2["result"]["total_cost"] == 1000.0
    assert r2["result"]["total_revenue"] == 5000.0
    assert r2["result"]["roi"] == 4.0


def test_util_generate_office_doc_docx(tmp_path, monkeypatch) -> None:
    pytest.importorskip("docx")
    import l3_node.workspace_context as wc

    monkeypatch.setattr(wc, "get_effective_workspace_root", lambda: tmp_path)
    r = cut.run_generate_office_doc(
        file_format="docx",
        file_path="t.docx",
        content_json={
            "blocks": [
                {"type": "h1", "text": "T"},
                {"type": "p", "text": "p1"},
                {"type": "bullet", "text": "item"},
                {"type": "table", "data": [["A", "B"], ["1", "2"]]},
            ],
        },
    )
    assert r["ok"] is True
    assert "file_path" in r
    assert str((tmp_path / "t.docx").resolve()) == r["file_path"]
    p = tmp_path / "t.docx"
    assert p.exists()
    assert p.stat().st_size > 200


def test_util_generate_office_doc_xlsx(tmp_path, monkeypatch) -> None:
    pytest.importorskip("openpyxl")
    import l3_node.workspace_context as wc

    monkeypatch.setattr(wc, "get_effective_workspace_root", lambda: tmp_path)
    r = cut.run_generate_office_doc(
        file_format="xlsx",
        file_path="d.xlsx",
        content_json={
            "sheets": [
                {"sheet_name": "数据", "data": [["h1", "h2"], [1, 2]]},
                {"sheet_name": "S2", "data": [["x"]]},
            ],
        },
    )
    assert r["ok"] is True
    assert "file_path" in r
    assert (tmp_path / "d.xlsx").exists()
    assert (tmp_path / "d.xlsx").stat().st_size > 50


def test_util_generate_office_doc_archived_aliases(tmp_path, monkeypatch) -> None:
    """file_type + content_data 仍可用。"""
    pytest.importorskip("docx")
    import l3_node.workspace_context as wc

    monkeypatch.setattr(wc, "get_effective_workspace_root", lambda: tmp_path)
    r = cut.run_generate_office_doc(
        file_type="docx",
        file_path="archived.docx",
        content_data={"blocks": [{"type": "p", "text": "x"}]},
    )
    assert r["ok"] is True
    assert (tmp_path / "archived.docx").exists()


def test_compose_long_document_mocked(tmp_path, monkeypatch) -> None:
    import l3_node.workspace_context as wc

    monkeypatch.setattr(wc, "get_effective_workspace_root", lambda: tmp_path)

    class DummyEng:
        def __init__(self, *a, **k):
            pass

        async def generate_response(self, messages, tools=None, **kwargs):
            return "Mock chapter markdown."

    with patch("core.llm_provider.LiteLLMEngine", DummyEng):
        r = cut.run_compose_long_document(
            file_path="report.md",
            topic="Test Topic",
            outline_sections=["A", "B"],
        )
    assert r["ok"] is True
    assert r["total_sections_processed"] == 2
    assert r.get("file_path")
    text = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "## A" in text and "## B" in text
    assert "Mock chapter" in text


def test_native_dispatch_routes_util() -> None:
    from core.native_tools import dispatch_native_tool

    r = dispatch_native_tool("util:uuid_gen")
    assert isinstance(r, dict)
    assert r.get("ok") is True


def test_loader_native_tools_includes_utils() -> None:
    from l3_node.primitives.tools.loader import NATIVE_TOOLS

    ids = {t["id"] for t in NATIVE_TOOLS if isinstance(t, dict)}
    assert "util:uuid_gen" in ids
    assert "util:desktop_message_box" in ids
    assert "util:generate_office_doc" in ids
    assert "util:compose_long_document" in ids
    assert "sys:list_env_safe" in ids
    assert "core:akshare_a_share_hist" in ids
    assert "core:yfinance_global_market_hist" in ids
    assert "core:yfinance_ticker_info" in ids
