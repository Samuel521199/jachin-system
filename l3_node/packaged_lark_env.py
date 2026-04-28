# -*- coding: utf-8 -*-
"""
发行包内置 Lark / K11 飞书键：由侧车构建时从仓库根 .env 生成
``packaged_lark_env_generated.PACKAGED_LARK_ENV``。

- **源码运行**：仅对 os.environ 中**尚未设置**的键补全（与安装目录 .env 合并后仍可缺省补全）。
- **PyInstaller frozen（sys.frozen）**：对表中**每个非空**键 **强制写入** os.environ，覆盖进程继承的错误/陈旧
  ``LARK_APP_ID`` 等，保证分发 exe 与构建时写入的机器人一致（K11 冒烟写表/发卡片同应用）。

与 ``merge_l3_dotenv_into_os``：桌面进程通常先 merge 用户 .env，再调本函数；frozen 下以内嵌为准覆盖 Lark/K11 键。

**例外（冒烟飞书三键）**：``K11_SMOKE_LARK_APP_ID`` / ``K11_SMOKE_LARK_APP_SECRET`` /
``K11_SMOKE_LARK_NOTIFY_CHAT_ID`` **不**由内嵌写入 frozen 环境，以免覆盖安装目录 ``.env``；
须以 ``merge_l3_dotenv_into_os`` 或 ``scripts/k11_lark_smoke_report._apply_k11_smoke_lark_env`` 为准。
"""
from __future__ import annotations

import os
import sys

# 打包后仍以「应用根目录 .env」为 SSOT，禁止内嵌 generated 覆盖这三项
_K11_SMOKE_ENV_FROM_DOTENV_ONLY = frozenset(
    {
        "K11_SMOKE_LARK_APP_ID",
        "K11_SMOKE_LARK_APP_SECRET",
        "K11_SMOKE_LARK_NOTIFY_CHAT_ID",
    }
)


def apply_packaged_lark_to_os_environ() -> None:
    try:
        from l3_node.packaged_lark_env_generated import PACKAGED_LARK_ENV
    except ImportError:
        return
    frozen = bool(getattr(sys, "frozen", False))
    for k, v in PACKAGED_LARK_ENV.items():
        if not k or v is None:
            continue
        if k in _K11_SMOKE_ENV_FROM_DOTENV_ONLY:
            continue
        s = str(v).strip() if v else ""
        if not s:
            continue
        if frozen:
            os.environ[k] = s
        else:
            cur = (os.environ.get(k) or "").strip()
            if not cur:
                os.environ[k] = s
