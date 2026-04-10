"""core.l3_dotenv_merge：合并后 TAVILY 进入 os.environ（临时文件）。"""

from __future__ import annotations

import os

import pytest

from core.l3_dotenv_merge import merge_l3_dotenv_into_os


def test_merge_loads_tavily_from_temp_project_env(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    root = tmp_path
    env_file = root / ".env"
    env_file.write_text("TAVILY_API_KEY=tvly-unit-test-merge-abc123\n", encoding="utf-8")
    monkeypatch.chdir(root)
    monkeypatch.setenv("JACHIN_APP_ROOT", str(root))
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    merge_l3_dotenv_into_os()
    assert os.environ.get("TAVILY_API_KEY") == "tvly-unit-test-merge-abc123"
