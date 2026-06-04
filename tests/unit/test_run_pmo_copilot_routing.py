"""run_pmo_copilot_skill.py CLI 路由：无参命令与 --analysis-only 均默认多 Agent。"""
from __future__ import annotations

import argparse

from scripts.run_pmo_copilot_skill import _use_multi_agent_path


def _args(**kwargs: object) -> argparse.Namespace:
    defaults = {
        "analysis_only": False,
        "init": False,
        "single_agent": False,
        "multi_agent": False,
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def test_default_full_uses_multi_agent():
    assert _use_multi_agent_path(_args()) is True


def test_analysis_only_uses_multi_agent():
    assert _use_multi_agent_path(_args(analysis_only=True)) is True


def test_single_agent_disables_multi():
    assert _use_multi_agent_path(_args(single_agent=True)) is False
    assert _use_multi_agent_path(_args(analysis_only=True, single_agent=True)) is False


def test_init_only_not_multi():
    assert _use_multi_agent_path(_args(init=True)) is False
