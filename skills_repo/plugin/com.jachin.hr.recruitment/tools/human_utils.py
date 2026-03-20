"""
拟人化等待：用随机延迟替代固定 sleep，降低反爬检测风险。
"""
import random


def human_wait(page, lo_sec: float, hi_sec: float) -> None:
    """随机等待 lo_sec ~ hi_sec 秒（模拟人类操作间隔）。"""
    ms = int(random.uniform(lo_sec, hi_sec) * 1000)
    page.wait_for_timeout(max(100, ms))
