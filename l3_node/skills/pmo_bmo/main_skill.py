"""兼容：与 ``l3_node.primitives.skills.pmo_bmo.main_skill`` 为同一模块对象。"""
from __future__ import annotations

import sys
from importlib import import_module

_real = import_module("l3_node.primitives.skills.pmo_bmo.main_skill")
sys.modules[__name__] = _real

if __name__ == "__main__":
    _real.pmo_bmo_skill_cli_main()
