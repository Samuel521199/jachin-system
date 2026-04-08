"""兼容：与 ``l3_node.primitives.skills.pmo_bmo.monitoring_skill`` 为同一模块对象。"""
from __future__ import annotations

import sys
from importlib import import_module

_real = import_module("l3_node.primitives.skills.pmo_bmo.monitoring_skill")
sys.modules[__name__] = _real
