"""嵌入式 runtime/node 下 npx 解析（resolve_mcp_stdio_command）。"""
from __future__ import annotations

from pathlib import Path

from core import mcp_embedded_runtime as mer


def test_resolve_npx_uses_embedded(tmp_path, monkeypatch) -> None:
    nd = tmp_path / "node"
    nd.mkdir()
    npx = nd / "npx.cmd"
    npx.write_text("@echo off\n", encoding="utf-8")
    node_exe = nd / "node.exe"
    node_exe.write_bytes(b"\x00")

    monkeypatch.setattr(mer, "_runtime_base_dirs", lambda: [tmp_path])

    out = mer.resolve_mcp_stdio_command("npx")
    assert Path(out).resolve() == npx.resolve()


def test_resolve_npx_preserves_absolute_user_path(tmp_path, monkeypatch) -> None:
    other = tmp_path / "other_npx.cmd"
    other.write_text("@echo off\n", encoding="utf-8")
    nd = tmp_path / "node"
    nd.mkdir()
    (nd / "npx.cmd").write_text("@echo off\n", encoding="utf-8")

    monkeypatch.setattr(mer, "_runtime_base_dirs", lambda: [tmp_path / "runtime"])

    out = mer.resolve_mcp_stdio_command(str(other))
    assert out == str(other)


def test_placeholder_jachin_mcp_npx(tmp_path, monkeypatch) -> None:
    nd = tmp_path / "node"
    nd.mkdir()
    npx = nd / "npx.cmd"
    npx.write_text("@echo off\n", encoding="utf-8")

    monkeypatch.setattr(mer, "_runtime_base_dirs", lambda: [tmp_path])

    out = mer.inject_embedded_tokens(mer.TOKEN_NPX)
    assert Path(out).name.lower() == "npx.cmd"
