"""
Tavily MCP：从「父进程环境变量」到「stdio 子进程 env」的传递链单测。

不启动真实 npx/Node（避免 CI/离线失败）；与 [TavilyMCP][chain] 日志 phase 对齐：
  phase A — 模拟 L3 已 load_dotenv 后 os.environ
  phase B — resolve_mcp_cfg_placeholders 输出中的 env（MCP SDK 与此合并后传给子进程）
  phase C — 与 mcp.client.stdio 一致：merged = default_white_list + stdio_env（Key 必须在 stdio_env）
"""

from __future__ import annotations

import os
import sys

import pytest

from core.mcp_embedded_runtime import (
    effective_stdio_env_for_sdk,
    expand_stdio_env_windows_npx_tavily,
    resolve_mcp_cfg_placeholders,
    resolve_tavily_stdio_cwd,
)


def _tavily_sample_cfg() -> dict:
    return {
        "id": "tavily-search",
        "command": "npx",
        "args": ["-y", "tavily-mcp@latest"],
        "env": {"TAVILY_API_KEY": "${TAVILY_API_KEY}"},
    }


@pytest.fixture
def tavily_key_in_parent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test-chain-phase-a")


@pytest.fixture
def no_tavily_in_parent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)


def test_phase_a_b_resolve_injects_non_empty_stdio_env(tavily_key_in_parent: None) -> None:
    """A+B：父进程有 Key → resolve 后 stdio env 含非空 TAVILY_API_KEY。"""
    out = resolve_mcp_cfg_placeholders(_tavily_sample_cfg())
    env = out.get("env")
    assert isinstance(env, dict)
    assert env.get("TAVILY_API_KEY") == "tvly-test-chain-phase-a"


def test_phase_b_without_parent_key_stdio_env_empty(no_tavily_in_parent: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """对照：父进程无 Key → 解析后 stdio 侧无有效 Key（与 MCP 报错 -32600 一致）。"""
    monkeypatch.setenv("JACHIN_DOTENV_MERGE_DISABLE", "1")
    out = resolve_mcp_cfg_placeholders(_tavily_sample_cfg())
    env = out.get("env") or {}
    assert not (str(env.get("TAVILY_API_KEY") or "").strip())


def test_phase_c_mcp_sdk_merge_formula(tavily_key_in_parent: None) -> None:
    """C：与 mcp.client.stdio 一致：merged = get_default_environment() + stdio_env；业务 Key 须在 stdio_env。"""
    from mcp.client.stdio import get_default_environment

    out = resolve_mcp_cfg_placeholders(_tavily_sample_cfg())
    stdio_env = out["env"]
    merged = {**get_default_environment(), **stdio_env}
    assert merged.get("TAVILY_API_KEY") == "tvly-test-chain-phase-a"
    # 无 TAVILY 的 stdio 片段合并后不会出现该 Key（说明不能仅靠「父进程全环境」）
    merged_no_tavily = {**get_default_environment(), **{"OTHER": "x"}}
    assert not (str(merged_no_tavily.get("TAVILY_API_KEY") or "").strip())


def test_phase_auto_inject_when_json_omits_env_block(tavily_key_in_parent: None) -> None:
    """无 env 块时仍从父进程注入（与 resolve 内 Tavily 分支一致）。"""
    out = resolve_mcp_cfg_placeholders(
        {
            "id": "tavily-search",
            "command": "npx",
            "args": ["-y", "tavily-mcp@latest"],
        }
    )
    assert out.get("env", {}).get("TAVILY_API_KEY") == "tvly-test-chain-phase-a"


def test_effective_stdio_env_tavily_raw_none_uses_parent_key(tavily_key_in_parent: None) -> None:
    """env=None 时若判为 Tavily，须显式补 Key（否则 SDK 仅用白名单，子进程无 TAVILY）。"""
    eff = effective_stdio_env_for_sdk("tavily-search", ["-y", "tavily-mcp@latest"], None)
    assert isinstance(eff, dict)
    assert eff.get("TAVILY_API_KEY") == "tvly-test-chain-phase-a"


def test_effective_stdio_env_non_tavily_none_when_empty() -> None:
    eff = effective_stdio_env_for_sdk("other", ["x"], None)
    assert eff is None


def test_mask_secret_for_log_no_leak() -> None:
    from core.mcp_embedded_runtime import mask_secret_for_log

    raw = "tvly-test-chain-phase-a"
    s = mask_secret_for_log(raw)
    assert raw not in s
    assert "len=" in s


def test_win32_expand_merges_full_parent_env_for_tavily(
    tavily_key_in_parent: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Windows：显式 eff 与完整父环境合并，便于 npx→node 链读到 NPM/PATH 等。"""
    monkeypatch.setenv("NPM_CHAIN_PROBE_VAR", "from_parent")
    monkeypatch.setattr(sys, "platform", "win32")
    eff = effective_stdio_env_for_sdk("tavily-search", ["-y", "tavily-mcp@latest"], None)
    out = expand_stdio_env_windows_npx_tavily("tavily-search", ["-y", "tavily-mcp@latest"], eff)
    assert isinstance(out, dict)
    assert out.get("TAVILY_API_KEY") == "tvly-test-chain-phase-a"
    assert out.get("NPM_CHAIN_PROBE_VAR") == "from_parent"


def test_non_win32_expand_is_noop(tavily_key_in_parent: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    eff = effective_stdio_env_for_sdk("tavily-search", ["-y", "tavily-mcp@latest"], None)
    out = expand_stdio_env_windows_npx_tavily("tavily-search", ["-y", "tavily-mcp@latest"], eff)
    assert out == eff


def test_resolve_tavily_stdio_cwd_prefers_repo_dotenv(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """子目录 cwd 时仍应解析到含 .env 的仓库根（与 tavily-mcp 内 dotenv.config 对齐）。"""
    monkeypatch.delenv("JACHIN_APP_ROOT", raising=False)
    core_dir = tmp_path / "core"
    core_dir.mkdir()
    (tmp_path / ".env").write_text("TAVILY_API_KEY=ok\n")
    monkeypatch.setattr(
        "core.mcp_embedded_runtime.__file__",
        str(core_dir / "mcp_embedded_runtime.py"),
    )
    sub = tmp_path / "clients" / "desktop"
    sub.mkdir(parents=True)
    monkeypatch.chdir(sub)
    out = resolve_tavily_stdio_cwd()
    assert out == str(tmp_path.resolve())
