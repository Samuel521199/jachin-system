"""
招聘定时任务动态间隔（启发式）：依据收网 MCP 观测到的 Boss「沟通」左侧会话列表规模，
判断沟通池是否枯竭；枯竭时缩短「推荐牛人」间隔、拉长「收网」间隔（无人可聊时高频收网无意义）。

pending 目录简历数不参与本策略（与「有没有人在聊」不是同一信号）。
"""

from __future__ import annotations


def compute_intervals_heuristic(
    *,
    base_recommend_minutes: int,
    base_harvest_minutes: int,
    enable_greet: bool,
    inbox_chat_list_count: int = -1,
    inbox_no_chats: bool = False,
) -> tuple[int, int]:
    """
    返回 (recommend_interval_minutes, harvest_interval_minutes)。

    - inbox_chat_list_count: 最近一次收网看到的左侧会话条数；-1 表示尚未观测或未知。
    - inbox_no_chats: 收网 MCP 确认列表为空（或等价错误）。
    """
    base_r = max(3, min(60, int(base_recommend_minutes) or 15))
    base_h = max(1, min(15, int(base_harvest_minutes) or 1))

    unknown = inbox_chat_list_count < 0
    cnt = max(0, int(inbox_chat_list_count)) if not unknown else 0
    dry = bool(inbox_no_chats) or (not unknown and cnt == 0)

    if enable_greet:
        if dry:
            rim = max(3, min(base_r, max(3, (base_r * 2) // 5)))
        elif unknown:
            rim = base_r
        elif cnt <= 3:
            rim = max(3, min(base_r, max(4, (base_r * 3) // 5)))
        elif cnt >= 25:
            rim = min(60, base_r + max(1, base_r // 5))
        else:
            rim = base_r
    else:
        rim = base_r

    if unknown:
        him = base_h
    elif dry:
        him = min(10, max(5, base_h + 4))
    elif cnt <= 5:
        him = min(6, max(2, base_h + 1))
    elif cnt >= 20:
        him = 1
    else:
        him = min(3, max(1, base_h))

    return int(rim), int(him)
