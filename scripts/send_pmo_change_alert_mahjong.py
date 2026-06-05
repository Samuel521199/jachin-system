#!/usr/bin/env python3
"""One-off: send PMO change alert to Lark (credentials via env, not stored)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CHAT_ID = "oc_437c98d11106295fb10751a5481ee465"
TITLE = "【变更预警】2026-06-05 · Gavin · 麻将开发插单"

MARKDOWN = """⚠️ **本轮发现 1 人需 PM 介入**（模拟 Webhook · 分支 B 变更分析）

---

📌 **变更摘要**（需求表新增 · 镜像库尚未同步该行）

| 项 | 内容 |
| :--- | :--- |
| 大需求 | **麻将开发**（新 Epic） |
| 子需求 | **麻将花色增加开发** |
| 负责人 | **Gavin** |
| Sprint | 2026/06/01-Sprint |
| Start Date | 2026-06-05 |
| 期待交付 | 2026-06-05 |
| 可接受交付 | 2026-06-06 |

> ⚠️ 已执行 INIT 拉表（4,986 行），`pmo_raw_records` 中 **未检索到「麻将」**——可能刚写入未进开发/人员视图，或 vewpI8lyYw 分页未全覆盖。以下人员结论基于 **镜像库现有 Gavin 任务 + 本次变更语义**。

---

**👥 人员影响（§1.4.1b 节奏判定）**

| 人员 | 本 Sprint 现状 | 变更后 | 判定 |
| :--- | :--- | :--- | :--- |
| **Gavin** | 本周计划 **1** 项（【P0】FB外跳-程序开发）；计划交付 **2026-06-02**；Progress/状态 **空** → 截至 **6/5（周五）仍无完成记录** | 新增 **麻将花色增加开发**（Start=Due=**6/5**，同日交付） | 🚨 **过载 + 延期叠加** |

**依据**：
1. 现有 P0 任务交付日已过 **3 天**仍无进展，已属 🚨 延期/进度严重落后。
2. 在延期未清情况下 **同日插单且 Start=Expected=今天**，无缓冲，完成率相对时间进度进一步恶化。
3. 条数 1→2 不是主因；**节奏与日期冲突**是主因（符合 Skill §1.4.1b，非 COUNT 排名）。

---

**⚠️ 项目 / 排期合理性**

| 检查项 | 结论 |
| :--- | :--- |
| 日期自洽 | Start=Expected=6/5 → **零工期排期**；可接受日 6/6 仅 1 天缓冲，对开发任务偏紧 |
| Sprint 窗口 | 属 **2026/06/01-Sprint** 中途（6/5）插 Epic，与当周 FB外跳 P0 并行 |
| 跨视图 | 产品池仅有「泉州麻将 / Victor / pending」，**未见「麻将开发 / Gavin」对齐** → 建议产品侧补录，避免幽灵需求 |
| 镜像同步 | 变更行 **未出现在镜像库** → 建议确认是否写入 `vewpI8lyYw` / `vewCz1FFJi` 并已保存 |

---

💡 **建议动作**（需 PM 确认，系统不自动改表）

1. **优先**：与 Gavin 确认 FB外跳 P0 实际进度；延期项未闭环前 **暂缓或后移** 麻将花色开发交付日。
2. 将「麻将花色增加开发」Expected 调整为 **≥6/6**（与可接受日一致）或拆 Sprint。
3. 产品表补录「麻将开发」Epic，与开发表负责人/Sprint 对齐。
4. 下次拉盘后复跑变更 diff，确认镜像已收录。

---

`change_alert_result: alert_sent` · 分析信道：分支 B 模拟 · 数据 SSOT：`pmo_db.sqlite` + INIT 2026-06-05
"""


def main() -> int:
    app_id = (os.environ.get("LARK_APP_ID") or "").strip()
    app_secret = (os.environ.get("LARK_APP_SECRET") or "").strip()
    if not app_id or not app_secret:
        print("ERROR: set LARK_APP_ID and LARK_APP_SECRET", file=sys.stderr)
        return 1

    os.environ.setdefault("LARK_USE_FEISHU", "0")

    from l3_node.channels.lark.im import send_markdown_card

    result = send_markdown_card(
        CHAT_ID,
        MARKDOWN,
        title=TITLE,
        app_id=app_id,
        app_secret=app_secret,
    )
    print(result)
    return 0 if result.get("status") == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
