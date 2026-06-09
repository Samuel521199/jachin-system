"""early_log 共享路径与 truncate 策略。"""
from __future__ import annotations

from pathlib import Path

from l3_node.early_log import _should_truncate_log_on_start, shared_l3_debug_log_path


def test_shared_l3_debug_log_path_is_user_jachin():
    p = shared_l3_debug_log_path()
    assert p.name == "l3_debug.log"
    assert p.parent.name == ".jachin"


def test_should_not_truncate_shared_jachin_log():
    shared = str(shared_l3_debug_log_path())
    assert _should_truncate_log_on_start(shared, pmo_run=False) is False


def test_should_truncate_install_logs_dir():
    install_log = str(Path("D:/Jachin/logs/l3_debug.log"))
    assert _should_truncate_log_on_start(install_log, pmo_run=False) is True


def test_pmo_run_never_truncates():
    assert _should_truncate_log_on_start("/any/path.log", pmo_run=True) is False
