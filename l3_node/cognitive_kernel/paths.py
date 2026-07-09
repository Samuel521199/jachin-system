"""Filesystem paths for the Cognitive Kernel runtime."""

from __future__ import annotations

import os
from pathlib import Path


def kernel_home() -> Path:
    root = os.environ.get("JACHIN_COGNITIVE_KERNEL_HOME")
    if root:
        return Path(root).expanduser()
    return Path.home() / ".jachin" / "cognitive_kernel"


def ledger_dir() -> Path:
    path = kernel_home() / "ledger"
    path.mkdir(parents=True, exist_ok=True)
    return path


def state_dir() -> Path:
    path = kernel_home() / "state"
    path.mkdir(parents=True, exist_ok=True)
    return path
