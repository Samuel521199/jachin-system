#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tongits 出牌回合守卫 — 绿圈 abort、时间预算、点击前校验。

主循环在「回合结束」时调用 abort_active_play_session()；
execute_scout_coord_turn 内通过 TurnPlayContext 检查 session 与绿圈。
"""
from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

_play_generation = 0
_gen_lock = threading.Lock()


class TurnAbortedError(Exception):
    """回合已结束或绿圈消失，中止后续点击。"""


def get_play_session() -> int:
    with _gen_lock:
        return _play_generation


def abort_active_play_session() -> int:
    """回合结束（绿圈消失）：使正在执行的出牌线程失效。"""
    global _play_generation
    with _gen_lock:
        _play_generation += 1
        return _play_generation


def is_play_aborted(session: int) -> bool:
    with _gen_lock:
        return session != _play_generation


def _turn_budget_sec() -> float:
    try:
        return max(5.0, float(os.environ.get("TONGITS_TURN_BUDGET_SEC", "18")))
    except ValueError:
        return 18.0


def _turn_dump_reserve_sec() -> float:
    """为 Dump + 一次 hand-only 快刷预留的时间。"""
    try:
        return max(2.0, float(os.environ.get("TONGITS_TURN_DUMP_RESERVE_SEC", "5")))
    except ValueError:
        return 5.0


def _turn_meld_step_est_sec() -> float:
    """估计一步亮牌（含快刷）耗时，用于预算判断。"""
    try:
        return max(1.0, float(os.environ.get("TONGITS_TURN_MELD_STEP_EST_SEC", "4")))
    except ValueError:
        return 4.0


def _turn_hand_refresh_est_sec() -> float:
    try:
        return max(1.0, float(os.environ.get("TONGITS_TURN_HAND_REFRESH_EST_SEC", "2.5")))
    except ValueError:
        return 2.5


def _is_my_turn_on_frame(bgr: Any) -> bool:
    from main_bot_loop import is_my_turn_on_frame

    return is_my_turn_on_frame(bgr)


@dataclass
class TurnPlayContext:
    session: int
    started_at: float
    budget_sec: float
    grab_frame: Callable[[], tuple[Any, str] | tuple[Any, str, Any]] | None
    dry_run: bool
    log_fn: Callable[[str], None]
    dump_hand_refreshed: bool = False

    @classmethod
    def create(
        cls,
        *,
        grab_frame: Callable[[], tuple[Any, str] | tuple[Any, str, Any]] | None,
        dry_run: bool,
        log_fn: Callable[[str], None],
        started_at: float | None = None,
    ) -> TurnPlayContext:
        return cls(
            session=get_play_session(),
            started_at=started_at if started_at is not None else time.perf_counter(),
            budget_sec=_turn_budget_sec(),
            grab_frame=grab_frame,
            dry_run=dry_run,
            log_fn=log_fn,
        )

    def elapsed(self) -> float:
        return time.perf_counter() - self.started_at

    def remaining(self) -> float:
        return self.budget_sec - self.elapsed()

    def aborted(self) -> bool:
        return is_play_aborted(self.session)

    def check_aborted(self, what: str = "操作") -> None:
        if self.aborted():
            raise TurnAbortedError(f"回合已结束(abort)，跳过 {what}")

    def ensure_active(self, what: str = "点击") -> None:
        """每次真实点击前：session 有效 + 绿圈仍在。"""
        self.check_aborted(what)
        if self.dry_run:
            return
        if self.grab_frame is None:
            return
        grabbed = self.grab_frame()
        bgr = grabbed[0] if grabbed else None
        if bgr is None:
            raise TurnAbortedError(f"截屏失败，跳过 {what}")
        if not _is_my_turn_on_frame(bgr):
            abort_active_play_session()
            raise TurnAbortedError(f"绿圈已消失，跳过 {what}")

    def must_dump_only(self) -> bool:
        return self.remaining() <= _turn_dump_reserve_sec()

    def can_do_optional_meld(self) -> bool:
        need = _turn_dump_reserve_sec() + _turn_meld_step_est_sec()
        return self.remaining() > need

    def can_afford_meld_rescout(self) -> bool:
        """亮牌后是否还有时间做 VLM 快刷。"""
        return self.remaining() > _turn_dump_reserve_sec() + _turn_hand_refresh_est_sec()

    def can_afford_hand_refresh(self) -> bool:
        if self.dump_hand_refreshed:
            return False
        return self.remaining() > _turn_hand_refresh_est_sec() + 0.8

    def log_budget(self, phase: str) -> None:
        self.log_fn(
            f"[出牌] 时间预算 {phase}: 已用 {self.elapsed():.1f}s / {self.budget_sec:.0f}s"
            f"（剩余 {self.remaining():.1f}s）"
        )
