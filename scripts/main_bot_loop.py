#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tongits 主循环 — 绿圈探回合 + 轮到我时 YOLO 侦察一次（默认）。

【默认模式】绿圈触发 → 延迟 → YOLO 识牌战报 → 截图存 scripts/omnioutput（与日志时间戳对照）::

  python scripts/main_bot_loop.py
  python scripts/main_bot_loop.py --debug        # 绿圈 / 手牌 ROI 校准
  python scripts/main_bot_loop.py --once         # 单次探测绿圈与发牌状态
  python scripts/main_bot_loop.py --conf 0.8

【仅截图模式】绿圈触发 → 保存 omnioutput（不做 YOLO）::

  python scripts/main_bot_loop.py --save-only

【调试】连续每秒 YOLO 侦察（非挂机用）::

  python scripts/main_bot_loop.py --continuous

依赖::

  pip install ultralytics mss opencv-python numpy

  Windows 若报 c10.dll / WinError 1114，勿用损坏的 Anaconda base，改用::

    .venv-omniparser\\Scripts\\python.exe scripts/main_bot_loop.py

  或运行 scripts/run_main_bot_loop.bat

环境变量::

  TONGITS_YOLO_MODEL / TONGITS_YOLO_CONF / TONGITS_MONITOR_INDEX
  TONGITS_YOLO_CONF / TONGITS_YOLO_IMGSZ / TONGITS_YOLO_IOU — 推理 conf/iou/imgsz（默认 0.40/0.40/512）
  TONGITS_YOLO_SAVE_MARKED — 保存模型标注图到 scripts/yolo_marked（默认 1）
  TONGITS_YOLO_MARKED_SHOW_ROI=1 — 标记图叠加五战区大框（默认 0，仅 YOLO 牌框）
  TONGITS_ROI_MY_MELDS — 我方明牌区 x1,y1,x2,y2（或 MY_MELD_*_RATIO 比例）
  TONGITS_HAND_ROI_*_RATIO — 手牌归类区（仅坐标分桶，不裁切推理）
  TONGITS_ROI_OPPONENT_LEFT / TONGITS_ROI_OPPONENT_RIGHT — 对手明牌区
  TONGITS_TURN_CAPTURE_DELAY_SEC — 回合开始后延迟再侦察（默认 1s）
  TONGITS_STARTUP_GRACE_SEC — 脚本启动后预热秒数，期间不截屏（默认 8）
  TONGITS_CAPTURE_RETRY_COUNT — 非牌桌/0检出时重试次数（默认 2）
  TONGITS_CAPTURE_WARMUP — 启动时被动预热截屏（默认 1，不改变窗口）
  TONGITS_HARD_EXAMPLES — 影子特训错题抓拍（默认 1）
  TONGITS_HARD_EXAMPLE_COOLDOWN_SEC — 错题抓拍冷却秒数（默认 3）
  TONGITS_HARD_EXAMPLE_HESITANT_LO/HI — 心虚置信度区间（默认 0.50~0.80）
  TONGITS_HYBRID_SCOUT — 混合侦察：手牌/我方明牌/弃牌 YOLO 裁区 + 左右对手 Qwen 并行（默认 1）
  TONGITS_SCOUT_MODE — 侦察模式：hybrid | yolo_full | florence_local（L2 本地 Florence OCR+HSV）
  TONGITS_QWEN_YOLO_VLM_FUSE — qwen_full 手牌/明牌：VLM 标签 + YOLO 坐标融合/补点（默认 1）
  TONGITS_FUSE_ROW_Y_TOL — 同行 YOLO 框 y 容差像素（默认 40）
  TONGITS_FUSE_CARD_GAP_PX — 补坐标默认牌间距（无 YOLO 锚点时，默认 47）
  TONGITS_FUSE_GROUP_GAP_RATIO — 同行内 x 大间隙切组倍数（默认 1.55）
  TONGITS_ROI_MY_MELD_Y1/Y2_RATIO — 我方明牌裁区（默认 0.50~0.60，四按钮正上方横条）
  TONGITS_ROI_MY_MELD_X1/X2_RATIO — 我方明牌裁区左右（默认 0.20~0.80，Drop~Dump 宽度）
  TONGITS_SAVE_MY_MELDS_CROP — 每回合保存 my_melds 裁图到 scripts/my_melds_crops（默认 1）
  TONGITS_VLM_LABEL_ZONE_TIMEOUT — 明牌/对手/弃牌 VLM 单路超时秒（默认 10，失败→[]）
  TONGITS_VLM_HAND_TIMEOUT — 手牌 VLM 超时秒（默认继承 TONGITS_VLM_TIMEOUT=25）
  TONGITS_AUTO_PLAY — 侦察后自动摸/吃/亮牌/贴牌/打牌（见 tongits_coord_executor.py）
  TONGITS_AUTO_PLAY_DRY_RUN — 自动出牌仅日志不点击（默认 1）
  TONGITS_AUTO_DROP — 自动 Group+Drop 亮牌（默认 1）
  TONGITS_AUTO_SAPAW — 自动 Sapaw 贴牌（默认 1）
  TONGITS_POST_MELD_WAIT_SEC — 亮牌/贴牌后等待动画（默认 1.0）
  TONGITS_HYBRID_VLM_MODEL — VLM 模型（默认 qwen3.5-flash，未设时继承 TONGITS_VLM_MODEL）
  TONGITS_QWEN_FULL_MAX_EDGE — 全屏 Qwen 上传前最长边缩放（默认 1280，0=不缩放）
  TONGITS_QWEN_FULL_JPEG_QUALITY — 全屏 Qwen 上传 JPEG 质量（默认 82）
  TONGITS_AVATAR_ROI / TONGITS_GREEN_* — 绿圈探针
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import re
import statistics
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable

import cv2
import numpy as np

# ---------------------------------------------------------------------------
# 路径与全局常量
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = Path(__file__).resolve().parent
OMNI_OUTPUT_DIR = SCRIPTS / "omnioutput"
MY_MELDS_CROP_DIR = SCRIPTS / "my_melds_crops"
YOLO_MARKED_DIR = SCRIPTS / "yolo_marked"
HARD_EXAMPLES_DIR = SCRIPTS / "hard_examples"
COIN_CROPS_DIR = SCRIPTS / "coin_crops"
DEFAULT_YOLO_WEIGHTS = SCRIPTS / "model" / "weights.pt"

# 影子特训（错题自动捕获）默认参数
HARD_EXAMPLE_COOLDOWN_SEC = 3.0
HARD_EXAMPLE_HESITANT_LO = 0.50
HARD_EXAMPLE_HESITANT_HI = 0.80
HARD_EXAMPLE_MAX_CLASS_LEN = 3

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

try:
    from dotenv import load_dotenv

    for _p in (ROOT / ".env", ROOT / "core" / ".env", Path.home() / ".jachin" / ".env"):
        if _p.exists():
            load_dotenv(_p, encoding="utf-8")
except ImportError:
    pass

logger = logging.getLogger("main_bot_loop")

# YOLO 视觉引擎强制参数（全屏推理，解除 640 压缩，保障远处对手角标）
YOLO_CONF_THRESHOLD = 0.40   # 置信度红线：低于 40% 的预测将被过滤
YOLO_IOU_THRESHOLD = 0.40   # NMS 重叠阈值：>40% 重叠视为重复框（角标标注法）
YOLO_IMGSZ = 512             # YOLO predict 输入边长（裁区 batch 推理）
HAND_DETECT_WARN_MIN = 8
HAND_ROI_PAD_X_RATIO = 0.02
HAND_ROI_PAD_Y_TOP_RATIO = 0.10
HAND_ROI_PAD_Y_BOT_RATIO = 0.05

# 全屏检测落入战区顺序（小区域 / 中央优先，避免重复归属）
ZONE_ASSIGN_ORDER: tuple[str, ...] = (
    "center_discard",
    "opponent_left",
    "opponent_right",
    "my_melds",
    "player_hand",
)

# 战区分组标签
ZONE_LABELS_CN: dict[str, str] = {
    "player_hand": "我的手牌",
    "my_melds": "我方已亮明牌",
    "opponent_left": "左侧对手明牌",
    "opponent_right": "右侧对手明牌",
    "center_discard": "中央弃牌顶牌",
}
TURN_SCOUT_ZONE_ORDER: tuple[str, ...] = (
    "player_hand",
    "my_melds",
    "opponent_left",
    "opponent_right",
    "center_discard",
)
# 混合侦察：YOLO 裁区 vs Qwen 战区批量
HYBRID_YOLO_ZONES: tuple[str, ...] = ("player_hand", "my_melds", "center_discard")
HYBRID_VLM_ZONES: tuple[str, ...] = ("opponent_left", "opponent_right")
# qwen_full：手牌 YOLO 坐标 + VLM 牌面融合；明牌/对手/弃牌仅 VLM 标签（无坐标）
QWEN_FULL_YOLO_ZONES: tuple[str, ...] = ("player_hand",)
QWEN_FULL_FUSE_ZONES: tuple[str, ...] = ("player_hand",)
QWEN_FULL_VLM_LABEL_ONLY_ZONES: tuple[str, ...] = (
    "my_melds",
    "opponent_left",
    "opponent_right",
    "center_discard",
)
QWEN_FULL_VLM_ZONES: tuple[str, ...] = ("player_hand",) + QWEN_FULL_VLM_LABEL_ONLY_ZONES
SCOUT_INTERVAL_SEC = 1.0  # 仅 --continuous 调试模式使用
MONITOR_INDEX = 1

# ---------------------------------------------------------------------------
# 绿圈回合检测常量（主循环轮询，YOLO / 截图均在此基础上触发）
# ---------------------------------------------------------------------------

AVATAR_ROI: tuple[int, int, int, int] = (8, 720, 118, 118)
_GREEN_RANGES: list[tuple[np.ndarray, np.ndarray]] = [
    (np.array([35, 100, 120], dtype=np.uint8), np.array([85, 255, 255], dtype=np.uint8)),
    (np.array([40, 150, 180], dtype=np.uint8), np.array([75, 255, 255], dtype=np.uint8)),
]
GREEN_PIXEL_THRESHOLD = 80
GREEN_RING_BORDER_ONLY = True
POLL_INTERVAL_SEC = 0.2
TURN_CAPTURE_DELAY_SEC = 1.0
STARTUP_GRACE_SEC = 8.0
CAPTURE_RETRY_COUNT = 2
CAPTURE_RETRY_DELAY_SEC = 1.5
CAPTURE_TIMEOUT_SEC = 8.0
GAME_TABLE_BLUE_RATIO_MIN = 0.06
HAND_CARD_RATIO_MIN = 0.04
HAND_EDGE_RATIO_MIN = 0.045
_DEFAULT_HAND_ROI: tuple[int, int, int, int] = (288, 761, 1632, 961)
TURN_ENTER_FRAMES = 2
TURN_EXIT_FRAMES = 4
# 头像 WIN 结算徽标（黄字 + 红底）；须与「绿圈消失」同时满足，避免正常打牌误判
WIN_YELLOW_RATIO_MIN = 0.10
WIN_RED_RATIO_MIN = 0.06
WIN_BRIGHT_YELLOW_RATIO_MIN = 0.045
WIN_CLUSTER_RATIO_MIN = 0.02
WIN_STRONG_YELLOW_RATIO_MIN = 0.14
WIN_STRONG_RED_RATIO_MIN = 0.09
_AVATAR_REF_SIZE = (1920, 1080)
_capture_busy = threading.Lock()
_pending_turn_scout = False
_last_win_click_at: float = 0.0
_last_fight_offer_click_at: float = 0.0
_last_fight_offer_probe_at: float = 0.0
_last_fight_offer_action: str = ""
_last_fight_offer_action_at: float = 0.0
_last_fight_point_cloud_try_at: float = 0.0
_last_settlement_click_at: float = 0.0
_last_settlement_probe_at: float = 0.0
_last_nonbar_diag_at: float = 0.0
_last_settlement_seen_at: float = 0.0
_settlement_overlay_first_seen_at: float = 0.0
_settlement_candidate_until: float = 0.0
_settlement_overlay_started_at: float = 0.0
_settlement_clicks_this_overlay: int = 0
_settlement_overlay_latched: bool = False
_settlement_retry_once_done: bool = False
_settlement_confirm_streak: int = 0
_settlement_not_seen_streak: int = 0
_settlement_ui_strong_streak: int = 0
_settlement_block_fight_streak: int = 0
_settlement_vlm_miss_streak: int = 0
_last_overlay_vlm_at: float = 0.0
_last_overlay_vlm_type: str = "none"
_overlay_vlm_mismatch_streak: dict[str, int] = {"settlement": 0, "duel": 0}
_overlay_vlm_failopen_until: dict[str, float] = {"settlement": 0.0, "duel": 0.0}
_last_known_hand_scatter: int | None = None
_last_settlement_coin_probe_at: float = 0.0
_settlement_coin_overlay_latched: bool = False
_last_settlement_coin_seen_at: float = 0.0
_last_settlement_locked_at: float = 0.0
_last_settlement_locked_frame: np.ndarray | None = None
_last_my_coin_amount: float | None = None
_last_my_coin_text: str = ""
_last_proto_coin_delta_at_seen: str = ""
_last_api_settlement_at_seen: str = ""
_pending_settlement_after_duel: bool = False
_pending_settlement_since: float = 0.0
_settlement_dump_seen: bool = False
_settlement_duel_seen: bool = False
_settlement_overlay_seen: bool = False
_settlement_continue_clicked_seen: bool = False
_settlement_coin_probe_armed: bool = False
_last_proto_status_log_at: float = 0.0
_last_proto_status_digest: str = ""
_loop_started_at: float = 0.0
_proto_settlement_service: Any = None

# 仓库内已验证可加载 torch 的虚拟环境（相对路径）
_PROJECT_VENV_PYTHON = ROOT / ".venv-omniparser" / "Scripts" / "python.exe"


def _prepare_frame_bgr(
    frame: np.ndarray,
    capture_backend: str = "",
    *,
    from_native: bool = False,
) -> np.ndarray:
    """
    视觉清洗协议：截屏原生矩阵 → OpenCV / YOLO 统一的 BGR 三通道。

    - mss：像素为 BGRA，使用 COLOR_BGRA2BGR
    - ImageGrab / pyautogui：像素为 RGB，使用 COLOR_RGB2BGR
    - 若 grab 层已转换（from_native=False），推理前不再二次 RGB2BGR，避免红蓝反转
    """
    if frame is None or frame.size == 0:
        return frame
    if frame.ndim == 2:
        return cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)

    ch = frame.shape[2]
    backend = (capture_backend or "").lower()

    if ch == 4:
        return cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)

    if ch != 3:
        return frame

    if not from_native:
        return frame

    if backend == "mss":
        return frame
    if backend in ("imagegrab", "pyautogui", "pyautogui(timeout)"):
        return cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
    return cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)


def _torch_runtime_hint() -> str:
    """PyTorch DLL 加载失败时的修复指引。"""
    lines = [
        "当前 Python 无法加载 PyTorch（常见于 Anaconda base 的 c10.dll 损坏）。",
        "",
        "推荐方案（任选其一）：",
    ]
    if _PROJECT_VENV_PYTHON.is_file():
        lines.append(f"  1. 使用项目虚拟环境:\n     {_PROJECT_VENV_PYTHON} {SCRIPTS / 'main_bot_loop.py'}")
        lines.append("     或双击 scripts/run_main_bot_loop.bat")
    lines.extend(
        [
            "  2. 在当前环境重装 CPU 版 PyTorch:",
            "     pip install --force-reinstall torch torchvision --index-url https://download.pytorch.org/whl/cpu",
            "  3. 安装 Microsoft Visual C++ 2015-2022 可再发行组件（x64）",
            "  4. 暂不做 YOLO: python scripts/main_bot_loop.py --save-only",
        ]
    )
    return "\n".join(lines)


def _import_ultralytics_yolo():
    """延迟导入 YOLO；捕获 torch DLL 错误并给出可执行修复指引。"""
    try:
        from ultralytics import YOLO

        return YOLO
    except ImportError as e:
        raise RuntimeError(
            "未安装 ultralytics，请执行: pip install ultralytics"
        ) from e
    except OSError as e:
        raise RuntimeError(_torch_runtime_hint()) from e


# =============================================================================
# YOLO 视觉感知子系统
# =============================================================================


@dataclass(frozen=True)
class CardDetection:
    """单张扑克牌检测结果。"""

    class_name: str
    center_x: int
    center_y: int
    confidence: float
    zone: str = "unknown"

    def format_brief(self, *, show_conf: bool = False) -> str:
        if self.center_x == 0 and self.center_y == 0:
            if show_conf:
                return f"{self.class_name}({self.confidence:.2f})"
            return self.class_name
        if show_conf:
            return (
                f"{self.class_name}@({self.center_x},{self.center_y},"
                f"{self.confidence:.2f})"
            )
        return f"{self.class_name}@({self.center_x},{self.center_y})"


@dataclass
class TurnScoutResult:
    """单回合多战区侦察结果。"""

    all_detections: list[CardDetection]
    by_zone: dict[str, list[CardDetection]]
    elapsed_ms: float
    zone_rois: dict[str, tuple[int, int, int, int]] | None = None
    raw_detection_count: int = 0
    yolo_ms: float = 0.0
    vlm_ms: float = 0.0
    hybrid: bool = False
    scout_mode: str = "hybrid"
    deck_valid: bool = True
    deck_issues: list[str] | None = None


# =============================================================================
# 影子特训子系统 — 心虚置信度区间自动捕获长尾错题
# =============================================================================


class HardExampleCollector:
    """
    影子特训（错题自动捕获）。

    在 YOLO 推理结果的「犹豫期」内，自动将截屏原图入库（无标注框），
    为后续 V6 训练储备高价值 hard example。全程被动、不阻塞主侦察链路。
    """

    def __init__(self, out_dir: Path) -> None:
        self.out_dir = out_dir
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self._cooldown_sec = _hard_example_cooldown_sec()
        self._hesitant_lo = _hard_example_hesitant_lo()
        self._hesitant_hi = _hard_example_hesitant_hi()
        self._max_class_len = HARD_EXAMPLE_MAX_CLASS_LEN
        self._last_saved_at = 0.0
        self._lock = threading.Lock()

    def try_capture(self, frame_bgr: np.ndarray, yolo_results: list[Any]) -> bool:
        """
        扫描本帧 YOLO 结果；若存在「心虚」扑克牌且不在冷却期，则保存截屏原图。

        返回 True 表示本次成功写入硬盘。
        """
        if not _hard_examples_enabled():
            return False
        if frame_bgr is None or frame_bgr.size == 0:
            return False
        if not yolo_results or not self._frame_has_hesitant_card(yolo_results):
            return False

        with self._lock:
            now = time.perf_counter()
            if now - self._last_saved_at < self._cooldown_sec:
                return False

            save_path = self._save_raw_frame(frame_bgr)
            if save_path is None:
                return False

            self._last_saved_at = now
            _emit_hard_example_alert(save_path)
            return True

    def _frame_has_hesitant_card(self, yolo_results: list[Any]) -> bool:
        """遍历检测框：扑克牌类别且置信度落在心虚区间则触发抓拍。"""
        for result in yolo_results:
            boxes = result.boxes
            if boxes is None or len(boxes) == 0:
                continue

            names: dict[int, str] = result.names or {}
            for box in boxes:
                conf = float(box.conf[0].item())
                cls_id = int(box.cls[0].item())
                class_name = _yolo_class_label(names, cls_id)

                if len(class_name) > self._max_class_len:
                    continue
                if self._hesitant_lo <= conf <= self._hesitant_hi:
                    return True
        return False

    def _save_raw_frame(self, frame_bgr: np.ndarray) -> Path | None:
        """将截屏原图写入 hard_examples（干净原图，供 V6 重新标注训练）。"""
        try:
            save_path = self._hard_example_path()
            cv2.imwrite(str(save_path), frame_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
            return save_path
        except Exception as e:
            logger.warning("影子特训抓拍失败: %s", e)
            return None

    @staticmethod
    def _hard_example_path() -> Path:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        return HARD_EXAMPLES_DIR / f"hard_example_{ts}.jpg"


_hard_example_collector: HardExampleCollector | None = None


def _init_hard_example_collector() -> HardExampleCollector:
    """初始化影子特训目录与单例（主循环启动时调用一次）。"""
    global _hard_example_collector
    if _hard_example_collector is None:
        _hard_example_collector = HardExampleCollector(HARD_EXAMPLES_DIR)
    return _hard_example_collector


def _shadow_training_try_capture(frame_bgr: np.ndarray, yolo_results: list[Any]) -> None:
    """推理后钩子：零额外推理，仅扫描 results，触发时保存截屏原图。"""
    if not _hard_examples_enabled():
        return
    _init_hard_example_collector().try_capture(frame_bgr, yolo_results)


def _hard_examples_enabled() -> bool:
    # 用户要求：关闭“高价值心虚错题”告警与入库。
    return False


def _hard_example_cooldown_sec() -> float:
    try:
        return float(
            os.environ.get("TONGITS_HARD_EXAMPLE_COOLDOWN_SEC", str(HARD_EXAMPLE_COOLDOWN_SEC))
        )
    except ValueError:
        return HARD_EXAMPLE_COOLDOWN_SEC


def _hard_example_hesitant_lo() -> float:
    try:
        return float(
            os.environ.get("TONGITS_HARD_EXAMPLE_HESITANT_LO", str(HARD_EXAMPLE_HESITANT_LO))
        )
    except ValueError:
        return HARD_EXAMPLE_HESITANT_LO


def _hard_example_hesitant_hi() -> float:
    try:
        return float(
            os.environ.get("TONGITS_HARD_EXAMPLE_HESITANT_HI", str(HARD_EXAMPLE_HESITANT_HI))
        )
    except ValueError:
        return HARD_EXAMPLE_HESITANT_HI


def _emit_hard_example_alert(save_path: Path) -> None:
    """醒目战报：终端高亮提示错题已入库。"""
    red = "\033[91m"
    bold = "\033[1m"
    reset = "\033[0m"
    msg = f"{bold}{red}🚨 [警报] 发现高价值心虚错题！已自动抓拍入库！ → {save_path}{reset}"
    print(msg, flush=True)
    logger.warning("影子特训抓拍 → %s", save_path.resolve())


class ScreenCapturer:
    """
    极速全屏截屏，多级回退（纯被动读屏，不操作鼠标/键盘/窗口）。

    mss → PIL.ImageGrab → pyautogui（规避 Windows BitBlt 偶发失败）。
    禁止 ShowWindow / SetForegroundWindow 等调用——会破坏浏览器全屏。
    """

    def __init__(self, *, monitor_index: int = 1) -> None:
        self.monitor_index = monitor_index
        self._mss: Any | None = None
        self._has_mss = False
        self._last_backend = "unknown"

        try:
            import mss  # noqa: F401

            self._mss_module = mss
            self._has_mss = True
        except ImportError:
            logger.warning("未安装 mss，截屏将跳过 mss 后端（建议: pip install mss）")

    def _grab_mss(self) -> np.ndarray:
        """mss 抓取主显示器；失败时关闭实例以便下次重建。"""
        try:
            if self._mss is None:
                mss_factory = getattr(self._mss_module, "MSS", None)
                self._mss = mss_factory() if mss_factory is not None else self._mss_module.mss()
            monitors = self._mss.monitors
            idx = min(max(1, self.monitor_index), len(monitors) - 1)
            monitor = monitors[idx]
            raw = np.array(self._mss.grab(monitor), dtype=np.uint8)
            return raw
        except Exception:
            if self._mss is not None:
                self._mss.close()
                self._mss = None
            raise

    @staticmethod
    def _grab_imagegrab() -> np.ndarray:
        from PIL import ImageGrab

        img = ImageGrab.grab(all_screens=False)
        return np.array(img)

    @staticmethod
    def _grab_pyautogui() -> np.ndarray:
        import pyautogui

        pyautogui.FAILSAFE = True
        shot = pyautogui.screenshot()
        return np.array(shot)

    def warmup(self) -> None:
        """冷启动被动预热：仅读一帧丢弃，不触碰窗口/焦点/全屏状态。"""
        if not _capture_warmup_enabled():
            return
        try:
            _ = self.grab()
        except Exception as e:
            logger.warning("截屏预热失败（可忽略）: %s", e)

    def grab(self) -> np.ndarray:
        errors: list[str] = []
        chain: list[tuple[str, Callable[[], np.ndarray]]] = []
        # Windows 优先 ImageGrab：与用户所见前台窗口一致，规避 mss 首轮 ReleaseDC
        if sys.platform == "win32":
            chain.append(("imagegrab", self._grab_imagegrab))
            if self._has_mss:
                chain.append(("mss", self._grab_mss))
        else:
            if self._has_mss:
                chain.append(("mss", self._grab_mss))
            chain.append(("imagegrab", self._grab_imagegrab))
        chain.append(("pyautogui", self._grab_pyautogui))

        for name, fn in chain:
            try:
                raw = fn()
                frame = _prepare_frame_bgr(raw, name, from_native=True)
                self._last_backend = name
                return frame
            except Exception as e:
                errors.append(f"{name}: {e}")

        raise RuntimeError("全屏截屏失败 → " + " | ".join(errors))

    def close(self) -> None:
        if self._mss is not None:
            self._mss.close()
            self._mss = None


class YOLOScreenScout:
    """
    YOLOv8 屏幕侦察引擎。

    职责：截屏 → 推理 → 解析类别与中心坐标 → 格式化战报。
    """

    def __init__(
        self,
        weights_path: Path,
        *,
        conf: float = YOLO_CONF_THRESHOLD,
        monitor_index: int = MONITOR_INDEX,
    ) -> None:
        if not weights_path.is_file():
            raise FileNotFoundError(
                f"YOLO 权重不存在: {weights_path}\n"
                "请将训练好的 weights.pt 放到 scripts/model/weights.pt"
            )

        YOLO = _import_ultralytics_yolo()

        self.weights_path = weights_path
        self.conf = conf
        self.iou = _yolo_iou()
        self.imgsz = _yolo_imgsz()
        self.capturer = ScreenCapturer(monitor_index=monitor_index)
        self.capturer.warmup()

        # 加载本地 YOLOv8 权重（仅加载一次，循环内复用）
        logger.info("正在加载 YOLO 模型 → %s", weights_path.resolve())
        self.model = YOLO(str(weights_path))
        logger.info(
            "YOLO 模型就绪 | conf=%.2f iou=%.2f imgsz=%d",
            self.conf,
            self.iou,
            self.imgsz,
        )

    def _parse_results(
        self,
        results: list[Any],
        *,
        offset_x: int = 0,
        offset_y: int = 0,
        coord_scale: float = 1.0,
        zone: str = "unknown",
    ) -> list[CardDetection]:
        """从 ultralytics Results 中提取类别名与框中心坐标（支持裁切区偏移）。"""
        detections: list[CardDetection] = []
        inv = 1.0 / max(coord_scale, 1e-6)

        for result in results:
            boxes = result.boxes
            if boxes is None or len(boxes) == 0:
                continue

            names: dict[int, str] = result.names or {}

            for box in boxes:
                cls_id = int(box.cls[0].item())
                class_name = _yolo_class_label(names, cls_id)
                conf = float(box.conf[0].item())

                x1, y1, x2, y2 = box.xyxy[0].tolist()
                center_x = int(round(offset_x + ((x1 + x2) / 2.0) * inv))
                center_y = int(round(offset_y + ((y1 + y2) / 2.0) * inv))

                detections.append(
                    CardDetection(
                        class_name=class_name,
                        center_x=center_x,
                        center_y=center_y,
                        confidence=conf,
                        zone=zone,
                    )
                )

        detections.sort(key=lambda d: (d.center_y, d.center_x))
        return detections

    def _predict(self, frame_bgr: np.ndarray) -> list[Any]:
        """
        YOLO 全屏推理核心入口（视觉引擎强制参数）。

        视觉清洗：送入 model.predict 前最后一道 BGR 校验（见 _prepare_frame_bgr）。
        参数说明：
        - conf: 置信度红线，低于 40% 的预测将被过滤
        - iou:  重叠度 NMS 阈值，角标标注法下 >40% 重叠视为重复识别并剔除
        - imgsz: YOLO 输入边长（默认 512，裁区 batch 推理）
        """
        frame_bgr = _prepare_frame_bgr(
            frame_bgr,
            self.capturer._last_backend,
            from_native=False,
        )
        return self.model.predict(
            source=frame_bgr,
            conf=self.conf,
            iou=self.iou,
            imgsz=self.imgsz,
            verbose=False,
        )

    def _predict_sources(self, sources: list[np.ndarray]) -> list[Any]:
        """对多张裁切图批量 YOLO 推理（混合侦察用手牌/明牌/弃牌区）。"""
        if not sources:
            return []
        prepared = [
            _prepare_frame_bgr(src, self.capturer._last_backend, from_native=False)
            for src in sources
        ]
        out = self.model.predict(
            source=prepared,
            conf=self.conf,
            iou=self.iou,
            imgsz=self.imgsz,
            verbose=False,
        )
        if not isinstance(out, list):
            return [out]
        return out

    def _infer_hybrid_yolo_zones(
        self,
        frame_bgr: np.ndarray,
        zone_rois: dict[str, tuple[int, int, int, int]],
    ) -> tuple[dict[str, list[CardDetection]], list[Any], float]:
        """YOLO 裁区：手牌 + 我方明牌 + 中央弃牌（一次 batch predict）。"""
        crops: list[np.ndarray] = []
        meta: list[tuple[str, int, int]] = []

        for zone_key in HYBRID_YOLO_ZONES:
            roi = zone_rois.get(zone_key)
            if not roi:
                continue
            crop, (ox, oy) = _crop_frame_roi(frame_bgr, roi)
            if crop.size == 0:
                continue
            crops.append(crop)
            meta.append((zone_key, ox, oy))

        by_zone: dict[str, list[CardDetection]] = {z: [] for z in HYBRID_YOLO_ZONES}
        if not crops:
            return by_zone, [], 0.0

        t0 = time.perf_counter()
        results = self._predict_sources(crops)
        yolo_ms = (time.perf_counter() - t0) * 1000.0

        hand_results: list[Any] = []
        for i, (zone_key, ox, oy) in enumerate(meta):
            if i >= len(results):
                break
            res = results[i]
            if zone_key == "player_hand":
                hand_results.append(res)
            dets = self._parse_results([res], offset_x=ox, offset_y=oy, zone=zone_key)
            by_zone[zone_key] = dets

        return by_zone, hand_results, yolo_ms

    def infer_turn_frame_hybrid(
        self,
        frame_bgr: np.ndarray,
        *,
        save_marked: bool | None = None,
    ) -> TurnScoutResult:
        """
        混合回合侦察：手牌/我方明牌/弃牌 YOLO 裁区 + 左右对手 Qwen 并行（各 1 次 API）。
        """
        t0 = time.perf_counter()
        zone_rois = _load_board_zone_rois(frame_bgr)

        if _is_round_end_win_screen(frame_bgr):
            _log_win_skip_reason(frame_bgr)
            return TurnScoutResult(
                all_detections=[],
                by_zone={z: [] for z in TURN_SCOUT_ZONE_ORDER},
                elapsed_ms=(time.perf_counter() - t0) * 1000.0,
                zone_rois=zone_rois,
                raw_detection_count=0,
                hybrid=True,
            )

        yolo_by_zone: dict[str, list[CardDetection]] = {z: [] for z in HYBRID_YOLO_ZONES}
        hand_yolo_results: list[Any] = []
        yolo_ms = 0.0
        vlm_ms = 0.0

        with ThreadPoolExecutor(max_workers=3) as pool:
            yolo_future = pool.submit(
                self._infer_hybrid_yolo_zones,
                frame_bgr,
                zone_rois,
            )
            vlm_futures = {
                zone_key: pool.submit(
                    _hybrid_infer_vlm_opponent_zone,
                    frame_bgr,
                    zone_key,
                    zone_rois[zone_key],
                )
                for zone_key in HYBRID_VLM_ZONES
                if zone_key in zone_rois
            }

            yolo_by_zone, hand_yolo_results, yolo_ms = yolo_future.result()
            vlm_by_zone: dict[str, list[CardDetection]] = {}
            vlm_elapsed: list[float] = []
            for zone_key, fut in vlm_futures.items():
                dets, zone_ms = fut.result()
                vlm_by_zone[zone_key] = dets
                vlm_elapsed.append(zone_ms)
            vlm_ms = max(vlm_elapsed) if vlm_elapsed else 0.0

        if hand_yolo_results and not _is_round_end_win_screen(frame_bgr):
            _shadow_training_try_capture(frame_bgr, hand_yolo_results)

        by_zone: dict[str, list[CardDetection]] = {z: [] for z in TURN_SCOUT_ZONE_ORDER}
        for zone_key in HYBRID_YOLO_ZONES:
            by_zone[zone_key] = yolo_by_zone.get(zone_key, [])
        for zone_key in HYBRID_VLM_ZONES:
            by_zone[zone_key] = vlm_by_zone.get(zone_key, [])

        classified = [d for zs in by_zone.values() for d in zs]
        raw_count = len(classified)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0

        do_save = _yolo_save_marked_enabled() if save_marked is None else bool(save_marked)
        hand_dets = by_zone.get("player_hand", [])
        if (
            do_save
            and hand_dets
            and hand_yolo_results
            and not _is_round_end_win_screen(frame_bgr)
        ):
            hand_roi = zone_rois.get("player_hand")
            hand_crop = (
                _crop_frame_roi(frame_bgr, hand_roi)[0]
                if hand_roi
                else frame_bgr
            )
            marked_path = _save_yolo_marked_image(
                hand_crop,
                hand_yolo_results,
                zone_rois=None,
                card_count=len(classified),
                raw_count=raw_count,
            )
            if marked_path is not None:
                logger.info("标记图已保存（手牌裁区）→ %s", marked_path.resolve())

        logger.info(
            "[hybrid] 分段耗时 YOLO=%.0fms VLM(并行)=%.0fms 合计=%.0fms",
            yolo_ms,
            vlm_ms,
            elapsed_ms,
        )

        return TurnScoutResult(
            all_detections=classified,
            by_zone=by_zone,
            elapsed_ms=elapsed_ms,
            zone_rois=zone_rois,
            raw_detection_count=raw_count,
            yolo_ms=yolo_ms,
            vlm_ms=vlm_ms,
            hybrid=True,
            scout_mode="hybrid",
        )

    def infer_frame(self, frame_bgr: np.ndarray) -> tuple[list[CardDetection], float]:
        """对已截好的 BGR 帧做全屏 YOLO 推理。"""
        t0 = time.perf_counter()
        results = self._predict(frame_bgr)
        if not _is_round_end_win_screen(frame_bgr):
            _shadow_training_try_capture(frame_bgr, results)
        detections = self._parse_results(results)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        return detections, elapsed_ms

    def infer_turn_frame(
        self,
        frame_bgr: np.ndarray,
        *,
        save_marked: bool | None = None,
    ) -> TurnScoutResult:
        """
        回合侦察：默认混合模式（手牌 YOLO + 对手 Qwen 并行）；可 TONGITS_HYBRID_SCOUT=0 回退全屏 YOLO。
        """
        if _hybrid_scout_enabled():
            return self.infer_turn_frame_hybrid(frame_bgr, save_marked=save_marked)

        return self._infer_turn_frame_full_yolo(frame_bgr, save_marked=save_marked)

    def _infer_turn_frame_full_yolo(
        self,
        frame_bgr: np.ndarray,
        *,
        save_marked: bool | None = None,
    ) -> TurnScoutResult:
        """
        全屏一次 YOLO → 按坐标落入五战区战报（legacy）。
        """
        t0 = time.perf_counter()
        zone_rois = _load_board_zone_rois(frame_bgr)

        if _is_round_end_win_screen(frame_bgr):
            _log_win_skip_reason(frame_bgr)
            return TurnScoutResult(
                all_detections=[],
                by_zone={z: [] for z in TURN_SCOUT_ZONE_ORDER},
                elapsed_ms=(time.perf_counter() - t0) * 1000.0,
                zone_rois=zone_rois,
                raw_detection_count=0,
            )

        results = self._predict(frame_bgr)
        if not _is_round_end_win_screen(frame_bgr):
            _shadow_training_try_capture(frame_bgr, results)
        all_dets = self._parse_results(results, zone="full_screen")
        raw_count = len(all_dets)
        by_zone = _classify_detections_by_zone(all_dets, zone_rois)
        classified = [d for zs in by_zone.values() for d in zs]
        elapsed_ms = (time.perf_counter() - t0) * 1000.0

        do_save = _yolo_save_marked_enabled() if save_marked is None else bool(save_marked)
        if do_save and raw_count > 0 and not _is_round_end_win_screen(frame_bgr):
            marked_path = _save_yolo_marked_image(
                frame_bgr,
                results,
                zone_rois=zone_rois,
                card_count=len(classified),
                raw_count=raw_count,
            )
            if marked_path is not None:
                logger.info("标记图已保存 → %s", marked_path.resolve())

        return TurnScoutResult(
            all_detections=classified,
            by_zone=by_zone,
            elapsed_ms=elapsed_ms,
            zone_rois=zone_rois,
            raw_detection_count=raw_count,
            hybrid=False,
            scout_mode="yolo_full",
        )

    def scout_once(self) -> tuple[list[CardDetection], float]:
        """截屏 + 推理（供 --continuous / --yolo-once 调试）。"""
        t0 = time.perf_counter()
        frame_bgr = self.capturer.grab()
        detections, infer_ms = self.infer_frame(frame_bgr)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        return detections, elapsed_ms

    def close(self) -> None:
        self.capturer.close()


class QwenFullScreenScout:
    """
    YOLO + VLM 融合（qwen_full）。

    - YOLO + 坐标融合：**仅 player_hand**（明牌/对手/弃牌仅 VLM 标签；YOLO 误检明牌区会在融合前剔除）
    - VLM 标签（无坐标）：my_melds、opponent_left/right、center_discard
    - VLM 标签（与 YOLO 融合出坐标）：player_hand
    """

    def __init__(
        self,
        weights_path: Path,
        *,
        conf: float = YOLO_CONF_THRESHOLD,
        monitor_index: int = MONITOR_INDEX,
    ) -> None:
        if not weights_path.is_file():
            raise FileNotFoundError(
                f"YOLO 权重不存在: {weights_path}\n"
                "qwen_full 模式需 YOLO 标手牌坐标，请将 weights.pt 放到 scripts/model/"
            )

        YOLO = _import_ultralytics_yolo()

        self.weights_path = weights_path
        self.conf = conf
        self.iou = _yolo_iou()
        self.imgsz = _yolo_imgsz()
        self.capturer = ScreenCapturer(monitor_index=monitor_index)
        self.capturer.warmup()

        logger.info("正在加载 YOLO（裁区认牌）→ %s", weights_path.resolve())
        self.model = YOLO(str(weights_path))
        logger.info(
            "手牌坐标+明牌VLM 就绪 | YOLO conf=%.2f imgsz=%d | VLM=%s",
            self.conf,
            self.imgsz,
            _qwen_vlm_model(),
        )

    def _parse_results(
        self,
        results: list[Any],
        *,
        offset_x: int = 0,
        offset_y: int = 0,
        zone: str = "player_hand",
    ) -> list[CardDetection]:
        detections: list[CardDetection] = []
        for result in results:
            boxes = result.boxes
            if boxes is None or len(boxes) == 0:
                continue
            names: dict[int, str] = result.names or {}
            for box in boxes:
                cls_id = int(box.cls[0].item())
                class_name = _yolo_class_label(names, cls_id)
                conf = float(box.conf[0].item())
                x1, y1, x2, y2 = box.xyxy[0].tolist()
                center_x = int(round(offset_x + (x1 + x2) / 2.0))
                center_y = int(round(offset_y + (y1 + y2) / 2.0))
                detections.append(
                    CardDetection(
                        class_name=class_name,
                        center_x=center_x,
                        center_y=center_y,
                        confidence=conf,
                        zone=zone,
                    )
                )
        detections.sort(key=lambda d: (d.center_y, d.center_x))
        return detections

    def _predict_sources(self, sources: list[np.ndarray]) -> list[Any]:
        """对多张裁切图批量 YOLO 推理（手牌 / 我方明牌）。"""
        if not sources:
            return []
        prepared = [
            _prepare_frame_bgr(src, self.capturer._last_backend, from_native=False)
            for src in sources
        ]
        out = self.model.predict(
            source=prepared,
            conf=self.conf,
            iou=self.iou,
            imgsz=self.imgsz,
            verbose=False,
        )
        if not isinstance(out, list):
            return [out]
        return out

    def _infer_yolo_hand_zone(
        self,
        frame_bgr: np.ndarray,
        zone_rois: dict[str, tuple[int, int, int, int]],
    ) -> tuple[dict[str, list[CardDetection]], list[Any], float]:
        """YOLO 裁区：仅 player_hand（手牌坐标）。"""
        crops: list[np.ndarray] = []
        meta: list[tuple[str, int, int]] = []

        for zone_key in QWEN_FULL_YOLO_ZONES:
            roi = zone_rois.get(zone_key)
            if not roi:
                continue
            crop, (ox, oy) = _crop_frame_roi(frame_bgr, roi)
            if crop.size == 0:
                continue
            crops.append(crop)
            meta.append((zone_key, ox, oy))

        by_zone: dict[str, list[CardDetection]] = {z: [] for z in QWEN_FULL_YOLO_ZONES}
        if not crops:
            return by_zone, [], 0.0

        t0 = time.perf_counter()
        results = self._predict_sources(crops)
        yolo_ms = (time.perf_counter() - t0) * 1000.0

        hand_results: list[Any] = []
        for i, (zone_key, ox, oy) in enumerate(meta):
            if i >= len(results):
                break
            res = results[i]
            if zone_key == "player_hand":
                hand_results.append(res)
            dets = self._parse_results([res], offset_x=ox, offset_y=oy, zone=zone_key)
            by_zone[zone_key] = dets

        return by_zone, hand_results, yolo_ms

    def infer_turn_frame(
        self,
        frame_bgr: np.ndarray,
        *,
        save_marked: bool | None = None,
    ) -> TurnScoutResult:
        t0 = time.perf_counter()
        zone_rois = _load_board_zone_rois(frame_bgr)

        if _is_round_end_win_screen(frame_bgr):
            _log_win_skip_reason(frame_bgr)
            return TurnScoutResult(
                all_detections=[],
                by_zone={z: [] for z in TURN_SCOUT_ZONE_ORDER},
                elapsed_ms=(time.perf_counter() - t0) * 1000.0,
                zone_rois=zone_rois,
                raw_detection_count=0,
                scout_mode="qwen_full",
            )

        hand_roi = zone_rois.get("player_hand")

        with ThreadPoolExecutor(max_workers=2) as pool:
            yolo_future = pool.submit(
                self._infer_yolo_hand_zone,
                frame_bgr,
                zone_rois,
            )
            vlm_future = pool.submit(
                _infer_qwen_vlm_zones_parallel,
                frame_bgr,
                zone_rois,
            )
            yolo_by_zone, hand_yolo_results, yolo_ms = yolo_future.result()
            vlm_labels, vlm_ms = vlm_future.result()

        hand_yolo = _exclude_non_hand_yolo_boxes(
            yolo_by_zone.get("player_hand", []),
            zone_rois,
        )
        yolo_by_zone["player_hand"] = hand_yolo

        if hand_yolo_results and not _is_round_end_win_screen(frame_bgr):
            _shadow_training_try_capture(frame_bgr, hand_yolo_results)

        by_zone: dict[str, list[CardDetection]] = {z: [] for z in TURN_SCOUT_ZONE_ORDER}
        fuse = _qwen_yolo_vlm_fuse_enabled()
        for zone_key in QWEN_FULL_FUSE_ZONES:
            if fuse:
                by_zone[zone_key] = _merge_yolo_coords_vlm_labels(
                    vlm_labels.get(zone_key, []),
                    yolo_by_zone.get(zone_key, []),
                    zone_key,
                )
            else:
                by_zone[zone_key] = yolo_by_zone.get(zone_key, [])
        for zone_key in QWEN_FULL_VLM_LABEL_ONLY_ZONES:
            by_zone[zone_key] = _label_list_to_detections(
                vlm_labels.get(zone_key, []),
                zone_key,
            )

        deck_valid, deck_issues = _validate_table_card_uniqueness(by_zone)
        if not deck_valid:
            logger.warning(
                "[qwen_full] 一副牌约束未通过: %s",
                "; ".join(deck_issues),
            )
            by_zone, deck_valid, deck_issues = _apply_qwen_full_deck_fail_fallback(
                by_zone,
                vlm_labels,
                yolo_by_zone,
                fuse=fuse,
            )

        classified = [d for zs in by_zone.values() for d in zs]
        raw_count = len(classified)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0

        do_save = _yolo_save_marked_enabled() if save_marked is None else bool(save_marked)
        hand_dets = by_zone.get("player_hand", [])
        if do_save and hand_dets and hand_yolo_results and hand_roi:
            hand_crop = _crop_frame_roi(frame_bgr, hand_roi)[0]
            marked_path = _save_yolo_marked_image(
                hand_crop,
                hand_yolo_results,
                zone_rois=None,
                card_count=len(classified),
                raw_count=raw_count,
            )
            if marked_path is not None:
                logger.info("标记图已保存（手牌 YOLO 裁区）→ %s", marked_path.resolve())

        logger.info(
            "[qwen_full] 并行 YOLO手牌=%.0fms VLM五路=%.0fms 合计=%.0fms 检出 %d 张 deck_ok=%s",
            yolo_ms,
            vlm_ms,
            elapsed_ms,
            raw_count,
            deck_valid,
        )

        return TurnScoutResult(
            all_detections=classified,
            by_zone=by_zone,
            elapsed_ms=elapsed_ms,
            zone_rois=zone_rois,
            raw_detection_count=raw_count,
            yolo_ms=yolo_ms,
            vlm_ms=vlm_ms,
            scout_mode="qwen_full",
            deck_valid=deck_valid,
            deck_issues=deck_issues if deck_issues else None,
        )

    def infer_hand_only(
        self,
        frame_bgr: np.ndarray,
        prev: TurnScoutResult | None = None,
        *,
        timeout_sec: float | None = None,
        no_retry: bool | None = None,
    ) -> TurnScoutResult:
        """
        摸/吃牌后快刷：仅 YOLO 手牌 + VLM 手牌区，保留 prev 中其它战区标签。

        比 infer_turn_frame 少 4 路 VLM，通常 ~0.5–3s vs 5–10s。
        """
        t0 = time.perf_counter()
        zone_rois = _load_board_zone_rois(frame_bgr)
        if prev and prev.zone_rois:
            zone_rois = dict(prev.zone_rois)

        hand_roi = zone_rois.get("player_hand")
        if not hand_roi:
            return self.infer_turn_frame(frame_bgr)

        hand_timeout = (
            _qwen_hand_only_vlm_timeout_sec()
            if timeout_sec is None
            else max(0.4, float(timeout_sec))
        )
        hand_no_retry = (
            _qwen_hand_only_no_retry_enabled()
            if no_retry is None
            else bool(no_retry)
        )
        with ThreadPoolExecutor(max_workers=2) as pool:
            yolo_future = pool.submit(
                self._infer_yolo_hand_zone,
                frame_bgr,
                zone_rois,
            )
            vlm_future = pool.submit(
                _vlm_zone_labels_task,
                frame_bgr,
                "player_hand",
                hand_roi,
                timeout_sec=hand_timeout,
                no_retry=hand_no_retry,
            )
            yolo_by_zone, hand_yolo_results, yolo_ms = yolo_future.result()
            hand_labels, hand_vlm_ms = vlm_future.result()

        hand_yolo = _exclude_non_hand_yolo_boxes(
            yolo_by_zone.get("player_hand", []),
            zone_rois,
        )

        fuse = _qwen_yolo_vlm_fuse_enabled()
        if fuse:
            player_hand = _merge_yolo_coords_vlm_labels(
                hand_labels,
                hand_yolo,
                "player_hand",
            )
        else:
            player_hand = _label_list_to_detections(hand_labels, "player_hand")

        by_zone: dict[str, list[CardDetection]] = {
            z: [] for z in TURN_SCOUT_ZONE_ORDER
        }
        if prev and prev.by_zone:
            for z in TURN_SCOUT_ZONE_ORDER:
                by_zone[z] = list(prev.by_zone.get(z) or [])
        by_zone["player_hand"] = player_hand

        classified = [d for zs in by_zone.values() for d in zs]
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        deck_valid = prev.deck_valid if prev else True
        deck_issues = list(prev.deck_issues or []) if prev else []

        logger.info(
            "[qwen_full] 手牌快刷 YOLO=%.0fms VLM=%.0fms 合计=%.0fms 手牌=%d"
            "（其它区沿用上一帧 timeout=%.1fs no_retry=%s）",
            yolo_ms,
            hand_vlm_ms,
            elapsed_ms,
            len(player_hand),
            hand_timeout,
            hand_no_retry,
        )

        return TurnScoutResult(
            all_detections=classified,
            by_zone=by_zone,
            elapsed_ms=elapsed_ms,
            zone_rois=zone_rois,
            raw_detection_count=len(classified),
            yolo_ms=yolo_ms,
            vlm_ms=hand_vlm_ms,
            scout_mode="qwen_full_hand_only",
            deck_valid=deck_valid,
            deck_issues=deck_issues if deck_issues else None,
        )

    def infer_hand_my_melds_only(
        self,
        frame_bgr: np.ndarray,
        prev: TurnScoutResult | None = None,
    ) -> TurnScoutResult:
        """亮牌/贴牌后快刷：YOLO 手牌 + VLM 手牌 + VLM my_melds（不跑对手/弃牌）。"""
        t0 = time.perf_counter()
        zone_rois = _load_board_zone_rois(frame_bgr)
        if prev and prev.zone_rois:
            zone_rois = dict(prev.zone_rois)

        hand_roi = zone_rois.get("player_hand")
        melds_roi = zone_rois.get("my_melds")
        if not hand_roi:
            return self.infer_turn_frame(frame_bgr)

        with ThreadPoolExecutor(max_workers=3) as pool:
            yolo_future = pool.submit(
                self._infer_yolo_hand_zone,
                frame_bgr,
                zone_rois,
            )
            hand_vlm_future = pool.submit(
                _vlm_zone_labels_task,
                frame_bgr,
                "player_hand",
                hand_roi,
            )
            melds_vlm_future = (
                pool.submit(
                    _vlm_zone_labels_task,
                    frame_bgr,
                    "my_melds",
                    melds_roi,
                )
                if melds_roi
                else None
            )
            yolo_by_zone, _, yolo_ms = yolo_future.result()
            hand_labels, hand_vlm_ms = hand_vlm_future.result()
            if melds_vlm_future is not None:
                melds_labels, melds_vlm_ms = melds_vlm_future.result()
            else:
                melds_labels, melds_vlm_ms = [], 0.0

        hand_yolo = _exclude_non_hand_yolo_boxes(
            yolo_by_zone.get("player_hand", []),
            zone_rois,
        )
        fuse = _qwen_yolo_vlm_fuse_enabled()
        if fuse:
            player_hand = _merge_yolo_coords_vlm_labels(
                hand_labels,
                hand_yolo,
                "player_hand",
            )
        else:
            player_hand = _label_list_to_detections(hand_labels, "player_hand")

        by_zone: dict[str, list[CardDetection]] = {
            z: [] for z in TURN_SCOUT_ZONE_ORDER
        }
        if prev and prev.by_zone:
            for z in TURN_SCOUT_ZONE_ORDER:
                by_zone[z] = list(prev.by_zone.get(z) or [])
        by_zone["player_hand"] = player_hand
        by_zone["my_melds"] = _label_list_to_detections(melds_labels, "my_melds")

        classified = [d for zs in by_zone.values() for d in zs]
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        vlm_ms = max(hand_vlm_ms, melds_vlm_ms)

        logger.info(
            "[qwen_full] 手牌+明牌快刷 YOLO=%.0fms VLM hand=%.0f melds=%.0f 合计=%.0fms "
            "手牌=%d my_melds=%d",
            yolo_ms,
            hand_vlm_ms,
            melds_vlm_ms,
            elapsed_ms,
            len(player_hand),
            len(melds_labels),
        )

        return TurnScoutResult(
            all_detections=classified,
            by_zone=by_zone,
            elapsed_ms=elapsed_ms,
            zone_rois=zone_rois,
            raw_detection_count=len(classified),
            yolo_ms=yolo_ms,
            vlm_ms=vlm_ms,
            scout_mode="qwen_full_hand_melds",
            deck_valid=prev.deck_valid if prev else True,
            deck_issues=list(prev.deck_issues or []) if prev else [],
        )

    def infer_hand_yolo_only(
        self,
        frame_bgr: np.ndarray,
        prev: TurnScoutResult | None = None,
    ) -> TurnScoutResult:
        """
        超低时延手牌重锚：仅 YOLO 手牌坐标，不调用 VLM。
        适用于 Drop 后手牌重排导致坐标漂移的快速纠偏。
        """
        t0 = time.perf_counter()
        zone_rois = _load_board_zone_rois(frame_bgr)
        if prev and prev.zone_rois:
            zone_rois = dict(prev.zone_rois)

        yolo_by_zone, _hand_yolo_results, yolo_ms = self._infer_yolo_hand_zone(frame_bgr, zone_rois)
        hand_yolo = _exclude_non_hand_yolo_boxes(
            yolo_by_zone.get("player_hand", []),
            zone_rois,
        )

        by_zone: dict[str, list[CardDetection]] = {z: [] for z in TURN_SCOUT_ZONE_ORDER}
        if prev and prev.by_zone:
            for z in TURN_SCOUT_ZONE_ORDER:
                by_zone[z] = list(prev.by_zone.get(z) or [])
        by_zone["player_hand"] = hand_yolo

        classified = [d for zs in by_zone.values() for d in zs]
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        logger.info(
            "[qwen_full] 手牌YOLO重锚=%.0fms 合计=%.0fms 手牌=%d（无VLM）",
            yolo_ms,
            elapsed_ms,
            len(hand_yolo),
        )
        return TurnScoutResult(
            all_detections=classified,
            by_zone=by_zone,
            elapsed_ms=elapsed_ms,
            zone_rois=zone_rois,
            raw_detection_count=len(classified),
            yolo_ms=yolo_ms,
            vlm_ms=0.0,
            scout_mode="qwen_full_hand_yolo_only",
            deck_valid=prev.deck_valid if prev else True,
            deck_issues=list(prev.deck_issues or []) if prev else [],
        )

    def close(self) -> None:
        self.capturer.close()


TurnScout = YOLOScreenScout | QwenFullScreenScout


def _create_turn_scout(
    weights_path: Path,
    *,
    conf: float,
    monitor_index: int,
) -> TurnScout:
    mode = _scout_mode()
    if mode == "florence_local":
        from vision_florence_local import FlorenceLocalScout

        return FlorenceLocalScout(
            weights_path,
            conf=conf,
            monitor_index=monitor_index,
        )
    if mode == "qwen_full":
        return QwenFullScreenScout(
            weights_path,
            conf=conf,
            monitor_index=monitor_index,
        )
    return YOLOScreenScout(
        weights_path,
        conf=conf,
        monitor_index=monitor_index,
    )


def _yolo_imgsz() -> int:
    try:
        return int(os.environ.get("TONGITS_YOLO_IMGSZ", str(YOLO_IMGSZ)))
    except ValueError:
        return YOLO_IMGSZ


def _yolo_iou() -> float:
    try:
        return float(os.environ.get("TONGITS_YOLO_IOU", str(YOLO_IOU_THRESHOLD)))
    except ValueError:
        return YOLO_IOU_THRESHOLD


def _expand_hand_roi(
    roi: tuple[int, int, int, int],
    sw: int,
    sh: int,
) -> tuple[int, int, int, int]:
    """外扩手牌 ROI，避免边缘牌 / 弹起牌被裁掉。"""
    x1, y1, x2, y2 = roi
    px = int(sw * float(os.environ.get("TONGITS_HAND_ROI_PAD_X", str(HAND_ROI_PAD_X_RATIO))))
    py_top = int(
        sh * float(os.environ.get("TONGITS_HAND_ROI_PAD_Y_TOP", str(HAND_ROI_PAD_Y_TOP_RATIO)))
    )
    py_bot = int(
        sh * float(os.environ.get("TONGITS_HAND_ROI_PAD_Y_BOT", str(HAND_ROI_PAD_Y_BOT_RATIO)))
    )
    return (
        max(0, x1 - px),
        max(0, y1 - py_top),
        min(sw, x2 + px),
        min(sh, y2 + py_bot),
    )


def _ratio_hand_roi(sw: int, sh: int) -> tuple[int, int, int, int]:
    """比例法手牌区（几乎全宽，含弹起牌纵向空间）。"""
    x1 = int(sw * float(os.environ.get("TONGITS_HAND_ROI_X1_RATIO", "0.01")))
    x2 = int(sw * float(os.environ.get("TONGITS_HAND_ROI_X2_RATIO", "0.99")))
    y1 = int(sh * float(os.environ.get("TONGITS_HAND_ROI_Y1_RATIO", "0.56")))
    y2 = int(sh * float(os.environ.get("TONGITS_HAND_ROI_Y2_RATIO", "0.965")))
    return (x1, y1, x2, y2)


def _scale_roi_xyxy(
    roi: tuple[int, int, int, int],
    *,
    ref_sw: int,
    ref_sh: int,
    sw: int,
    sh: int,
) -> tuple[int, int, int, int]:
    """将标定 ROI 按分辨率缩放（如浏览器非全屏 1707×1067）。"""
    if ref_sw <= 0 or ref_sh <= 0:
        return roi
    if ref_sw == sw and ref_sh == sh:
        return roi
    sx = sw / ref_sw
    sy = sh / ref_sh
    x1, y1, x2, y2 = roi
    return (
        int(round(x1 * sx)),
        int(round(y1 * sy)),
        int(round(x2 * sx)),
        int(round(y2 * sy)),
    )


def _my_melds_roi(sw: int, sh: int) -> tuple[int, int, int, int]:
    """
    Drop/Fight/Group/Dump 四按钮正上方的明牌横条（与用户标红区域一致）。

    默认 y=50%~60%、x=20%~80%，避免裁到牌桌中央牌堆/弃牌（旧 28%~46% 过高易慢且误识）。
    """
    env_roi = (os.environ.get("TONGITS_ROI_MY_MELDS") or "").strip()
    if env_roi:
        parts = [p.strip() for p in env_roi.split(",")]
        if len(parts) == 4:
            try:
                return tuple(int(p) for p in parts)  # type: ignore[return-value]
            except ValueError:
                pass

    y1 = int(sh * float(os.environ.get("TONGITS_ROI_MY_MELD_Y1_RATIO", "0.50")))
    y2 = int(sh * float(os.environ.get("TONGITS_ROI_MY_MELD_Y2_RATIO", "0.60")))
    x1 = int(sw * float(os.environ.get("TONGITS_ROI_MY_MELD_X1_RATIO", "0.20")))
    x2 = int(sw * float(os.environ.get("TONGITS_ROI_MY_MELD_X2_RATIO", "0.80")))
    return (max(0, x1), max(0, y1), min(sw, x2), min(sh, y2))


def _load_board_zone_rois(frame_bgr: np.ndarray) -> dict[str, tuple[int, int, int, int]]:
    """解析战区 ROI：手牌 + 我方明牌 + 左右对手 + 中央弃牌。"""
    from fast_card_recognizer import resolve_multi_zone_rois

    sh, sw = frame_bgr.shape[:2]
    use_wide = (os.environ.get("TONGITS_HAND_USE_WIDE_ROI") or "1").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    if use_wide:
        hand_roi = _ratio_hand_roi(sw, sh)
    else:
        hand_roi = _load_hand_roi(sw, sh)

    rois = resolve_multi_zone_rois(
        sw,
        sh,
        hand_roi_override=_expand_hand_roi(hand_roi, sw, sh),
    )
    rois["player_hand"] = _expand_hand_roi(hand_roi, sw, sh)
    rois["my_melds"] = _my_melds_roi(sw, sh)
    return rois


def _filter_detections_in_roi(
    detections: list[CardDetection],
    roi: tuple[int, int, int, int],
) -> list[CardDetection]:
    x1, y1, x2, y2 = roi
    return [
        d
        for d in detections
        if x1 <= d.center_x <= x2 and y1 <= d.center_y <= y2
    ]


def _exclude_non_hand_yolo_boxes(
    yolo_dets: list[CardDetection],
    zone_rois: dict[str, tuple[int, int, int, int]],
) -> list[CardDetection]:
    """
    手牌坐标融合专用：剔除 center 落在 my_melds 内的 YOLO 框。

    我方已亮明牌只需 VLM 标签、不要坐标；手牌裁区上扩时 YOLO 常会误检明牌区（如 D5/D6）。
    """
    melds_roi = zone_rois.get("my_melds")
    if not melds_roi or not yolo_dets:
        return yolo_dets
    kept: list[CardDetection] = []
    dropped = 0
    for det in yolo_dets:
        if _filter_detections_in_roi([det], melds_roi):
            dropped += 1
            logger.info(
                "[qwen_full] 忽略明牌区 YOLO 框 %s @(%d,%d)（明牌仅 VLM 标签，不参与手牌坐标）",
                det.class_name,
                det.center_x,
                det.center_y,
            )
            continue
        kept.append(det)
    if dropped:
        logger.info(
            "[qwen_full] 手牌 YOLO 剔除明牌区 %d 框，保留 %d",
            dropped,
            len(kept),
        )
    return kept


def _classify_detections_by_zone(
    detections: list[CardDetection],
    zone_rois: dict[str, tuple[int, int, int, int]],
) -> dict[str, list[CardDetection]]:
    """全屏检测结果按中心点坐标落入战区（每张牌只归属一个区）。"""
    by_zone: dict[str, list[CardDetection]] = {z: [] for z in TURN_SCOUT_ZONE_ORDER}

    for det in detections:
        for zone_key in ZONE_ASSIGN_ORDER:
            roi = zone_rois.get(zone_key)
            if not roi or not _filter_detections_in_roi([det], roi):
                continue
            by_zone[zone_key].append(
                CardDetection(
                    class_name=det.class_name,
                    center_x=det.center_x,
                    center_y=det.center_y,
                    confidence=det.confidence,
                    zone=zone_key,
                )
            )
            break

    for zone_key in by_zone:
        by_zone[zone_key].sort(key=lambda d: (d.center_y, d.center_x))
    return by_zone


def _scout_mode() -> str:
    """hybrid | yolo_full | florence_local | qwen_full(遗留)"""
    explicit = (os.environ.get("TONGITS_SCOUT_MODE") or "").strip().lower()
    if explicit in ("qwen_full", "qwen-full", "qwen"):
        return "qwen_full"
    if explicit in (
        "florence_local",
        "florence-local",
        "florence",
    ):
        return "florence_local"
    if explicit in ("yolo_full", "yolo-full", "full_yolo"):
        return "yolo_full"
    if explicit in ("hybrid",):
        return "hybrid"
    if not _env_bool("TONGITS_HYBRID_SCOUT", True):
        return "yolo_full"
    return "hybrid"


def _hybrid_scout_enabled() -> bool:
    return _scout_mode() == "hybrid"


def _qwen_vlm_model() -> str:
    from vision_proxy_qwen import default_vlm_model

    raw = (os.environ.get("TONGITS_HYBRID_VLM_MODEL") or "").strip()
    return raw or default_vlm_model()


def _hybrid_vlm_model() -> str:
    return _qwen_vlm_model()


def _qwen_full_max_edge() -> int:
    raw = (os.environ.get("TONGITS_QWEN_FULL_MAX_EDGE") or "").strip()
    if not raw:
        return 1280
    try:
        return max(0, int(raw))
    except ValueError:
        return 1280


def _qwen_full_jpeg_quality() -> int:
    try:
        return max(50, min(95, int(os.environ.get("TONGITS_QWEN_FULL_JPEG_QUALITY", "82"))))
    except ValueError:
        return 82


def _qwen_yolo_vlm_fuse_enabled() -> bool:
    return (os.environ.get("TONGITS_QWEN_YOLO_VLM_FUSE") or "1").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def _qwen_hand_only_vlm_timeout_sec() -> float:
    """
    摸/吃牌后 hand-only 快刷的 VLM 超时（默认 2.2s）。
    超时后将由 YOLO 兜底，避免回合末被 VLM 长尾拖死。
    """
    raw = (os.environ.get("TONGITS_VLM_HAND_ONLY_TIMEOUT") or "").strip()
    if not raw:
        return 2.2
    try:
        return max(0.6, min(8.0, float(raw)))
    except ValueError:
        return 2.2


def _qwen_hand_only_no_retry_enabled() -> bool:
    return (os.environ.get("TONGITS_VLM_HAND_ONLY_NO_RETRY") or "1").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _normalize_table_label(label: str) -> str:
    from vision_proxy_qwen import canonical_card_label

    canon = canonical_card_label(label)
    if canon:
        return canon
    return ""


def _yolo_class_label(names: dict[int, str], cls_id: int) -> str:
    raw = str(names.get(cls_id, f"class_{cls_id}"))
    return _normalize_table_label(raw) or raw


def _fuse_row_y_tol() -> int:
    try:
        return max(10, int(os.environ.get("TONGITS_FUSE_ROW_Y_TOL", "40")))
    except ValueError:
        return 40


def _fuse_default_card_gap() -> float:
    try:
        return max(20.0, float(os.environ.get("TONGITS_FUSE_CARD_GAP_PX", "47")))
    except ValueError:
        return 47.0


def _fuse_group_gap_ratio() -> float:
    try:
        return max(1.2, float(os.environ.get("TONGITS_FUSE_GROUP_GAP_RATIO", "1.55")))
    except ValueError:
        return 1.55


def _yolo_spatial_sort(dets: list[CardDetection]) -> list[CardDetection]:
    return sorted(dets, key=lambda d: (d.center_y, d.center_x))


def _estimate_gap_from_dets(dets: list[CardDetection]) -> float:
    """单组/单行相邻框估计水平间距。"""
    if len(dets) < 2:
        return _fuse_default_card_gap()
    xs = sorted(d.center_x for d in dets)
    gaps = [float(xs[i + 1] - xs[i]) for i in range(len(xs) - 1) if xs[i + 1] - xs[i] > 5]
    if gaps:
        return statistics.median(gaps)
    return _fuse_default_card_gap()


def _cluster_yolo_into_rows(dets: list[CardDetection]) -> list[list[CardDetection]]:
    """按 center_y 聚类为若干行（不跨行）。"""
    if not dets:
        return []
    y_tol = _fuse_row_y_tol()
    sorted_d = sorted(dets, key=lambda d: (d.center_y, d.center_x))
    rows: list[list[CardDetection]] = [[sorted_d[0]]]
    for det in sorted_d[1:]:
        row_mean_y = statistics.mean(d.center_y for d in rows[-1])
        if abs(det.center_y - row_mean_y) <= y_tol:
            rows[-1].append(det)
        else:
            rows.append([det])
    for row in rows:
        row.sort(key=lambda d: d.center_x)
    rows.sort(key=lambda row: statistics.mean(d.center_y for d in row))
    return rows


def _split_row_into_x_groups(row_dets: list[CardDetection]) -> list[list[CardDetection]]:
    """同行内按 x 大间隙切分为组（三同张/四同张/顺子/散牌组）。"""
    if len(row_dets) <= 1:
        return [row_dets] if row_dets else []
    gaps = [
        float(row_dets[i + 1].center_x - row_dets[i].center_x)
        for i in range(len(row_dets) - 1)
    ]
    median_gap = statistics.median(gaps) if gaps else _fuse_default_card_gap()
    threshold = max(50.0, median_gap * _fuse_group_gap_ratio())
    groups: list[list[CardDetection]] = [[row_dets[0]]]
    for det in row_dets[1:]:
        if float(det.center_x - groups[-1][-1].center_x) > threshold:
            groups.append([det])
        else:
            groups[-1].append(det)
    return groups


def _vlm_partition_hints(base_counts: list[int], n_vlm: int) -> list[int]:
    """按 YOLO 框数分配 VLM 切片长度，使总和等于 n_vlm。"""
    if not base_counts:
        return []
    hints = [max(0, c) for c in base_counts]
    if n_vlm <= 0:
        return [0] * len(hints)
    if sum(hints) == 0:
        per = n_vlm // len(hints)
        rem = n_vlm % len(hints)
        return [per + (1 if i < rem else 0) for i in range(len(hints))]

    diff = n_vlm - sum(hints)
    if diff > 0:
        order = sorted(range(len(hints)), key=lambda i: hints[i])
        for j in range(diff):
            hints[order[j % len(order)]] += 1
    elif diff < 0:
        order = sorted(range(len(hints)), key=lambda i: -hints[i])
        for j in range(-diff):
            idx = order[j % len(order)]
            if hints[idx] > 0:
                hints[idx] -= 1
    if sum(hints) != n_vlm:
        hints = base_counts[:]
        start = 0
        out: list[int] = []
        total = max(1, sum(hints))
        for i, h in enumerate(hints):
            if i == len(hints) - 1:
                out.append(n_vlm - start)
            else:
                k = max(0, round(n_vlm * h / total))
                k = min(k, n_vlm - start - (len(hints) - i - 1))
                out.append(k)
                start += k
        hints = out
    return hints


def _partition_vlm_slices(vlm: list[str], count_hints: list[int]) -> list[list[str]]:
    """顺序切分 VLM 列表，各段长度由 count_hints 决定。"""
    hints = _vlm_partition_hints(count_hints, len(vlm))
    parts: list[list[str]] = []
    start = 0
    for i, k in enumerate(hints):
        if i == len(hints) - 1:
            parts.append(vlm[start:])
        else:
            parts.append(vlm[start : start + k])
            start += k
    return parts


def _interpolate_insert_coords(
    prev: CardDetection | None,
    nxt: CardDetection | None,
    count: int,
    gap: float,
) -> list[tuple[int, int]]:
    """在同一行/组内，于两个 YOLO 锚点之间（或外延）等间距补坐标。"""
    if count <= 0:
        return []
    if prev and nxt:
        y = int(round((prev.center_y + nxt.center_y) / 2.0))
        x0, x1 = float(prev.center_x), float(nxt.center_x)
        if abs(x1 - x0) < 5:
            step = gap if x1 >= x0 else -gap
            return [(int(prev.center_x + step * (k + 1)), y) for k in range(count)]
        step = (x1 - x0) / float(count + 1)
        return [(int(round(x0 + step * (k + 1))), y) for k in range(count)]
    if prev and not nxt:
        y = prev.center_y
        return [(int(round(prev.center_x + gap * (k + 1))), y) for k in range(count)]
    if nxt and not prev:
        y = nxt.center_y
        return [(int(round(nxt.center_x - gap * (count - k))), y) for k in range(count)]
    return [(0, 0)] * count


def _dedupe_yolo_boxes_by_coord(
    yolo_grp: list[CardDetection],
    *,
    min_x_gap: int = 18,
) -> list[CardDetection]:
    """合并几乎重叠的 YOLO 框，避免同坐标绑定多张 VLM 标签。"""
    if len(yolo_grp) <= 1:
        return list(yolo_grp)
    y_tol = _fuse_row_y_tol()
    sorted_y = sorted(yolo_grp, key=lambda d: d.center_x)
    out: list[CardDetection] = [sorted_y[0]]
    for yd in sorted_y[1:]:
        prev = out[-1]
        if (
            abs(yd.center_x - prev.center_x) < min_x_gap
            and abs(yd.center_y - prev.center_y) <= y_tol
        ):
            if yd.confidence > prev.confidence:
                out[-1] = yd
            continue
        out.append(yd)
    return out


def _resolve_duplicate_fusion_coords(
    dets: list[CardDetection],
    gap: float,
    *,
    zone_key: str,
) -> list[CardDetection]:
    """融合后若仍有同坐标，按间距顺延 x（降权重复 YOLO 锚点）。"""
    if len(dets) < 2:
        return dets
    sorted_d = sorted(dets, key=lambda d: d.center_x)
    fixed: list[CardDetection] = [sorted_d[0]]
    step = max(20, int(round(gap)))
    for det in sorted_d[1:]:
        prev = fixed[-1]
        if det.center_x == prev.center_x and det.center_y == prev.center_y:
            new_x = prev.center_x + step
            logger.warning(
                "[qwen_full] %s 同坐标冲突 %s/%s @(%d,%d)，顺延 x→%d",
                zone_key,
                prev.class_name,
                det.class_name,
                prev.center_x,
                prev.center_y,
                new_x,
            )
            fixed.append(
                CardDetection(
                    class_name=det.class_name,
                    center_x=new_x,
                    center_y=det.center_y,
                    confidence=det.confidence,
                    zone=det.zone,
                )
            )
        else:
            fixed.append(det)
    return fixed


def _fuse_group_x_primary(
    vlm_grp: list[str],
    yolo_grp: list[CardDetection],
    *,
    zone_key: str,
) -> list[CardDetection]:
    """
    组内融合：VLM 标签 + YOLO 坐标；按 x 单调递增匹配（非下标对齐），漏检补点。
    """
    vlm = [_normalize_table_label(x) for x in vlm_grp if _normalize_table_label(x)]
    yolo = _dedupe_yolo_boxes_by_coord(sorted(yolo_grp, key=lambda d: d.center_x))
    if not vlm:
        return []
    if not yolo:
        return _label_list_to_detections(vlm, zone_key)

    gap = _estimate_gap_from_dets(yolo)
    out: list[CardDetection] = []
    pending: list[str] = []
    corrections = 0
    inferred = 0
    used = [False] * len(yolo)
    last_x = -99999
    x_slack = 12

    def flush(prev: CardDetection | None, nxt: CardDetection | None) -> None:
        nonlocal inferred
        if not pending:
            return
        coords = _interpolate_insert_coords(prev, nxt, len(pending), gap)
        for label, (cx, cy) in zip(pending, coords):
            inferred += 1
            logger.info(
                "[qwen_full] %s 组内补坐标 VLM=%s @(%d,%d) gap≈%.0f",
                zone_key,
                label,
                cx,
                cy,
                gap,
            )
            out.append(
                CardDetection(
                    class_name=label,
                    center_x=cx,
                    center_y=cy,
                    confidence=1.0,
                    zone=zone_key,
                )
            )
        pending.clear()

    def pick_next_yolo() -> CardDetection | None:
        nonlocal last_x
        best_idx: int | None = None
        best_x: int | None = None
        for j, yd in enumerate(yolo):
            if used[j] or yd.center_x < last_x - x_slack:
                continue
            if best_x is None or yd.center_x < best_x:
                best_x = yd.center_x
                best_idx = j
        if best_idx is None:
            return None
        used[best_idx] = True
        picked = yolo[best_idx]
        last_x = picked.center_x
        return picked

    last_matched: CardDetection | None = None
    for vlm_label in vlm:
        yd = pick_next_yolo()
        if yd is None:
            pending.append(vlm_label)
            continue
        flush(last_matched, yd)
        yolo_label = _normalize_table_label(yd.class_name)
        if yolo_label and vlm_label and yolo_label != vlm_label:
            corrections += 1
            logger.info(
                "[qwen_full] %s YOLO=%s → VLM=%s @(%d,%d)",
                zone_key,
                yolo_label,
                vlm_label,
                yd.center_x,
                yd.center_y,
            )
        det = CardDetection(
            class_name=vlm_label,
            center_x=yd.center_x,
            center_y=yd.center_y,
            confidence=yd.confidence,
            zone=zone_key,
        )
        out.append(det)
        last_matched = det

    flush(last_matched, None)

    unused = sum(1 for u in used if not u)
    if unused:
        logger.debug(
            "[qwen_full] %s 组内丢弃多余 YOLO 框 %d 个",
            zone_key,
            unused,
        )
    if corrections:
        logger.info("[qwen_full] %s 组内 VLM 校正 %d 张", zone_key, corrections)
    if inferred:
        logger.info("[qwen_full] %s 组内补坐标 %d 张", zone_key, inferred)
    out = _resolve_duplicate_fusion_coords(out, gap, zone_key=zone_key)
    return sorted(out, key=lambda d: d.center_x)


def _fuse_row_by_x(
    vlm_row: list[str],
    yolo_row: list[CardDetection],
    *,
    zone_key: str,
) -> list[CardDetection]:
    """单行：切 x 组 → 组内 x 顺序融合（禁止跨行）。"""
    yolo_groups = _split_row_into_x_groups(yolo_row)
    if not yolo_groups:
        return _label_list_to_detections(
            [_normalize_table_label(x) for x in vlm_row if _normalize_table_label(x)],
            zone_key,
        )
    vlm_parts = _partition_vlm_slices(
        [_normalize_table_label(x) for x in vlm_row if _normalize_table_label(x)],
        [len(g) for g in yolo_groups],
    )
    row_out: list[CardDetection] = []
    for vlm_grp, yolo_grp in zip(vlm_parts, yolo_groups):
        row_out.extend(
            _fuse_group_x_primary(vlm_grp, yolo_grp, zone_key=zone_key)
        )
    if len(vlm_parts) < len(yolo_groups):
        for yolo_grp in yolo_groups[len(vlm_parts) :]:
            row_out.extend(_fuse_group_x_primary([], yolo_grp, zone_key=zone_key))
    elif len(vlm_parts) > len(yolo_groups):
        extra = vlm_parts[len(yolo_groups) :]
        flat_extra = [lb for part in extra for lb in part]
        if flat_extra and row_out:
            gap = _estimate_gap_from_dets(yolo_row)
            last = row_out[-1]
            coords = _interpolate_insert_coords(last, None, len(flat_extra), gap)
            for label, (cx, cy) in zip(flat_extra, coords):
                row_out.append(
                    CardDetection(
                        class_name=label,
                        center_x=cx,
                        center_y=cy,
                        confidence=1.0,
                        zone=zone_key,
                    )
                )
    row_y = int(round(statistics.mean(d.center_y for d in yolo_row)))
    logger.debug(
        "[qwen_full] %s 行 y≈%d 组数=%d VLM=%d YOLO=%d",
        zone_key,
        row_y,
        len(yolo_groups),
        len(vlm_row),
        len(yolo_row),
    )
    return sorted(row_out, key=lambda d: d.center_x)


def _merge_yolo_coords_vlm_labels(
    vlm_labels: list[str],
    yolo_dets: list[CardDetection],
    zone_key: str,
) -> list[CardDetection]:
    """
    VLM 牌面 + YOLO 坐标：先按 y 分行，再行内按 x 分组，组内 x 顺序对齐并补点。
    """
    vlm_norm = [_normalize_table_label(x) for x in vlm_labels if _normalize_table_label(x)]
    if not vlm_norm and yolo_dets:
        logger.warning("[qwen_full] %s VLM 无标签，回退 YOLO（%d 框）", zone_key, len(yolo_dets))
        return _yolo_spatial_sort(yolo_dets)
    if vlm_norm and not yolo_dets:
        logger.warning(
            "[qwen_full] %s YOLO 无框，无法补坐标（%d 张 VLM）",
            zone_key,
            len(vlm_norm),
        )
        return _label_list_to_detections(vlm_norm, zone_key)

    yolo_rows = _cluster_yolo_into_rows(yolo_dets)
    if not yolo_rows:
        return _label_list_to_detections(vlm_norm, zone_key)

    if len(yolo_rows) == 1:
        vlm_rows = [vlm_norm]
    else:
        vlm_rows = _partition_vlm_slices(vlm_norm, [len(r) for r in yolo_rows])
        logger.info(
            "[qwen_full] %s 分行 VLM=%d YOLO=%d → %d 行 %s",
            zone_key,
            len(vlm_norm),
            len(yolo_dets),
            len(yolo_rows),
            [len(r) for r in vlm_rows],
        )

    fused: list[CardDetection] = []
    for row_idx, yolo_row in enumerate(yolo_rows):
        vlm_row = vlm_rows[row_idx] if row_idx < len(vlm_rows) else []
        fused.extend(_fuse_row_by_x(vlm_row, yolo_row, zone_key=zone_key))
    return _yolo_spatial_sort(fused)


def _validate_table_card_uniqueness(
    by_zone: dict[str, list[CardDetection]],
) -> tuple[bool, list[str]]:
    """全桌可见牌：同一 SHCD+rank 标签不得出现两次（一副牌约束）。"""
    seen: dict[str, str] = {}
    issues: list[str] = []
    for zone_key in TURN_SCOUT_ZONE_ORDER:
        for det in by_zone.get(zone_key, []):
            label = _normalize_table_label(det.class_name)
            if not label or len(label) < 2:
                continue
            prev = seen.get(label)
            if prev is not None:
                issues.append(f"{label} 重复于 {prev} 与 {zone_key}")
            else:
                seen[label] = zone_key
    return len(issues) == 0, issues


def _apply_qwen_full_deck_fail_fallback(
    by_zone: dict[str, list[CardDetection]],
    vlm_labels: dict[str, list[str]],
    yolo_by_zone: dict[str, list[CardDetection]],
    *,
    fuse: bool,
) -> tuple[dict[str, list[CardDetection]], bool, list[str]]:
    """
    一副牌约束失败时：仅保留 player_hand（VLM 标签 + 可选 YOLO 坐标），其它战区清零。
    """
    logger.warning(
        "[qwen_full] 约束失败回退：仅信任 player_hand VLM，清空明牌/对手/弃牌"
    )
    hand_vlm = [
        _normalize_table_label(x)
        for x in vlm_labels.get("player_hand", [])
        if _normalize_table_label(x)
    ]
    if fuse:
        player_hand = _merge_yolo_coords_vlm_labels(
            hand_vlm,
            yolo_by_zone.get("player_hand", []),
            "player_hand",
        )
    else:
        player_hand = _label_list_to_detections(hand_vlm, "player_hand")

    new_by_zone: dict[str, list[CardDetection]] = {
        z: [] for z in TURN_SCOUT_ZONE_ORDER
    }
    new_by_zone["player_hand"] = player_hand
    deck_valid, deck_issues = _validate_table_card_uniqueness(new_by_zone)
    if not deck_valid:
        logger.warning(
            "[qwen_full] 回退后 player_hand 仍违反约束: %s",
            "; ".join(deck_issues),
        )
    return new_by_zone, deck_valid, deck_issues


def _vlm_zone_timeout_sec(zone_key: str) -> float:
    """手牌区较长超时；明牌/对手/弃牌短超时，失败快速 []。"""
    from vision_proxy_qwen import vlm_zone_timeout_sec

    return vlm_zone_timeout_sec(zone_key)


def _nonbar_diag_interval_sec() -> float:
    """非常规全屏诊断的最小保存间隔（秒），避免刷屏。"""
    try:
        return max(1.0, float(os.environ.get("TONGITS_NONBAR_DIAG_INTERVAL_SEC") or "2.0"))
    except ValueError:
        return 2.0


def _qwen_full_vlm_phase_budget_sec() -> float:
    """VLM 五路并行的整体硬预算（秒）：超过即放弃剩余战区，防止单路拖垮整轮。"""
    try:
        return max(4.0, float(os.environ.get("TONGITS_QWEN_FULL_VLM_PHASE_BUDGET_SEC") or "14"))
    except ValueError:
        return 14.0


def _infer_qwen_vlm_zones_parallel(
    frame_bgr: np.ndarray,
    zone_rois: dict[str, tuple[int, int, int, int]],
) -> tuple[dict[str, list[str]], float]:
    """
    VLM 五路并行：手牌(融合坐标) + 明牌/对手/弃牌(仅标签，无坐标)。
    """
    t0 = time.perf_counter()
    timings: dict[str, float] = {}
    labels_by_zone: dict[str, list[str]] = {}

    # 五路整体硬预算：避免任何单路（含重试/网络抖动）拖垮整轮回合。
    phase_budget_sec = _qwen_full_vlm_phase_budget_sec()

    # 不用 with：退出时 with 会 shutdown(wait=True) 阻塞等待“掉队/还在重试”的线程，
    # 这正是 wall 远大于单路超时的根因。改为收集完即 shutdown(wait=False) 不等待。
    pool = ThreadPoolExecutor(max_workers=5)
    try:
        futures: dict[str, Any] = {}
        for zone_key in QWEN_FULL_VLM_ZONES:
            roi = zone_rois.get(zone_key)
            if roi:
                # 回合预算阶段：所有战区一律禁用 SDK 重试，避免“25s×N 次”超时雪崩；
                # 手牌失败由 YOLO 坐标兜底，无需 VLM 重试。
                futures[zone_key] = pool.submit(
                    _vlm_zone_labels_task,
                    frame_bgr,
                    zone_key,
                    roi,
                    no_retry=True,
                )
        for zone_key in QWEN_FULL_VLM_ZONES:
            fut = futures.get(zone_key)
            if fut is None:
                labels_by_zone[zone_key] = []
                timings[zone_key] = 0.0
                continue
            elapsed = time.perf_counter() - t0
            remain = max(0.0, phase_budget_sec - elapsed)
            wait_sec = min(_vlm_zone_timeout_sec(zone_key) + 2.0, remain)
            try:
                zone_labels, zone_ms = fut.result(timeout=wait_sec)
            except FuturesTimeoutError:
                logger.warning(
                    "[qwen_full] VLM 战区超时 zone=%s (>%.1fs, 预算剩=%.1fs)，视为 []",
                    zone_key,
                    wait_sec,
                    remain,
                )
                zone_labels, zone_ms = [], wait_sec * 1000.0
            labels_by_zone[zone_key] = zone_labels
            timings[zone_key] = zone_ms
    finally:
        # 立即放手，不阻塞等待仍在跑的线程（它们会在各自超时后自行结束）。
        pool.shutdown(wait=False)

    hand_ms = timings.get("player_hand", 0.0)
    melds_ms = timings.get("my_melds", 0.0)
    left_ms = timings.get("opponent_left", 0.0)
    right_ms = timings.get("opponent_right", 0.0)
    discard_ms = timings.get("center_discard", 0.0)
    wall_ms = max(timings.values()) if timings else 0.0
    wall_ms = max(wall_ms, (time.perf_counter() - t0) * 1000.0)
    total = sum(len(v) for v in labels_by_zone.values())
    logger.info(
        "[qwen_full] VLM五路 wall=%.0fms (hand=%.0f melds=%.0f left=%.0f right=%.0f discard=%.0f) total=%d",
        wall_ms,
        hand_ms,
        melds_ms,
        left_ms,
        right_ms,
        discard_ms,
        total,
    )
    return labels_by_zone, wall_ms


def _label_list_to_detections(
    labels: list[str],
    zone_key: str,
) -> list[CardDetection]:
    """对手战区 VLM 标签 → 无坐标 Detection（战报仅牌面）。"""
    out: list[CardDetection] = []
    for label in labels:
        canon = _normalize_table_label(label)
        if not canon:
            continue
        out.append(
            CardDetection(
                class_name=canon,
                center_x=0,
                center_y=0,
                confidence=1.0,
                zone=zone_key,
            )
        )
    return out


def _my_melds_crop_save_enabled() -> bool:
    return (os.environ.get("TONGITS_SAVE_MY_MELDS_CROP") or "1").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def _my_melds_crop_save_path() -> Path:
    now = datetime.now()
    ts = now.strftime("%Y%m%d_%H%M%S") + f"_{now.microsecond // 1000:03d}"
    return MY_MELDS_CROP_DIR / f"{ts}_my_melds.jpg"


def _save_my_melds_crop_debug(
    crop_bgr: np.ndarray,
    roi: tuple[int, int, int, int],
) -> Path | None:
    """每回合保存我方明牌裁区，便于对照 ROI 与 VLM 日志。"""
    if not _my_melds_crop_save_enabled() or crop_bgr.size == 0:
        return None
    try:
        MY_MELDS_CROP_DIR.mkdir(parents=True, exist_ok=True)
        save_path = _my_melds_crop_save_path()
        cv2.imwrite(str(save_path), crop_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
        logger.info("my_melds 裁图已保存 → %s roi=%s", save_path.resolve(), roi)
        return save_path
    except Exception as e:
        logger.warning("my_melds 裁图保存失败: %s", e)
        return None


def _save_vlm_crop_jpeg(crop_bgr: np.ndarray) -> str:
    """裁切图写入临时 JPEG，返回路径（调用方负责 unlink）。"""
    upload, _ = _prepare_vlm_upload_frame(crop_bgr)
    fd, tmp_path = tempfile.mkstemp(suffix=".jpg")
    os.close(fd)
    if not cv2.imwrite(
        tmp_path,
        upload,
        [int(cv2.IMWRITE_JPEG_QUALITY), _qwen_full_jpeg_quality()],
    ):
        raise RuntimeError("VLM 裁区 JPEG 写入失败")
    return tmp_path


def _coin_crops_enabled() -> bool:
    # CDP 3016 为主路径时，默认关闭诊断裁图（减少截屏与日志噪声，不阻塞 CDP 线程）
    default = "0" if _coin_use_cdp_enabled() else "1"
    return (os.environ.get("TONGITS_SAVE_COIN_CROPS") or default).strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _init_coin_crops_dir() -> None:
    if not _coin_crops_enabled():
        return
    try:
        COIN_CROPS_DIR.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        logger.warning("[settlement] coin_crops 目录初始化失败: %s", e)


def _save_coin_crop(kind: str, crop_bgr: np.ndarray) -> Path | None:
    if (not _coin_crops_enabled()) or crop_bgr is None or crop_bgr.size == 0:
        return None
    try:
        COIN_CROPS_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=8))).strftime(
            "%Y%m%d_%H%M%S_%f"
        )[:-3]
        p = COIN_CROPS_DIR / f"{ts}_{kind}.jpg"
        cv2.imwrite(str(p), crop_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
        logger.info("[settlement] 金币裁图已保存 → %s", p.resolve())
        return p
    except Exception as e:
        logger.warning("[settlement] 金币裁图保存失败 kind=%s: %s", kind, e)
        return None


def _vlm_zone_labels_task(
    frame_bgr: np.ndarray,
    zone_key: str,
    roi: tuple[int, int, int, int],
    *,
    timeout_sec: float | None = None,
    no_retry: bool | None = None,
) -> tuple[list[str], float]:
    from vision_proxy_qwen import analyze_zone_labels_compact_with_qwen

    zone_desc = ZONE_LABELS_CN.get(zone_key, zone_key)
    if zone_key == "player_hand":
        zone_desc = (
            "玩家手牌区（屏幕下方持牌，含可能的两行；"
            "按视觉从左到右、从上到下顺序列出每一张正面牌）"
        )
    elif zone_key == "my_melds":
        zone_desc = (
            "我方已亮明牌区（Drop/Fight/Group/Dump 四色大按钮正上方、"
            "窄横条槽位；按从左到右列出每一张正面朝上的牌；"
            "勿包含下方手牌与上方中央牌堆；若槽位空白则必须输出 []）"
        )
    elif zone_key == "center_discard":
        zone_desc = (
            "中央弃牌堆（仅识别**正面朝上**的顶牌；"
            "若仅有牌背、牌堆张数数字（如15）或无顶牌则必须输出 []）"
        )
    elif zone_key == "opponent_left":
        zone_desc = (
            "左侧对手已亮明牌区（头像旁明牌槽；"
            "若无任何正面朝上的牌则必须输出 []）"
        )
    elif zone_key == "opponent_right":
        zone_desc = (
            "右侧对手已亮明牌区（头像旁明牌槽；"
            "若无任何正面朝上的牌则必须输出 []）"
        )
    crop, _ = _crop_frame_roi(frame_bgr, roi)
    if crop.size == 0:
        return [], 0.0
    if zone_key == "my_melds":
        _save_my_melds_crop_debug(crop, roi)
    tmp_path = _save_vlm_crop_jpeg(crop)
    t0 = time.perf_counter()
    try:
        labels = analyze_zone_labels_compact_with_qwen(
            tmp_path,
            zone_desc=zone_desc,
            zone_key=zone_key,
            model=_qwen_vlm_model(),
            timeout_sec=(
                _vlm_zone_timeout_sec(zone_key)
                if timeout_sec is None
                else max(0.4, float(timeout_sec))
            ),
            no_retry=no_retry,
        )
        return labels, (time.perf_counter() - t0) * 1000.0
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def _prepare_vlm_upload_frame(
    frame_bgr: np.ndarray,
) -> tuple[np.ndarray, float]:
    """可选缩放上传图；返回 (upload_bgr, coord_scale) 原图坐标 = VLM坐标 * coord_scale。"""
    max_edge = _qwen_full_max_edge()
    if max_edge <= 0:
        return frame_bgr, 1.0
    h, w = frame_bgr.shape[:2]
    longest = max(h, w)
    if longest <= max_edge:
        return frame_bgr, 1.0
    scale = max_edge / float(longest)
    new_w = max(1, int(round(w * scale)))
    new_h = max(1, int(round(h * scale)))
    upload = cv2.resize(frame_bgr, (new_w, new_h), interpolation=cv2.INTER_AREA)
    return upload, 1.0 / scale


def _crop_frame_roi(
    frame_bgr: np.ndarray,
    roi: tuple[int, int, int, int],
) -> tuple[np.ndarray, tuple[int, int]]:
    """从全帧裁出战区；返回 (crop, (offset_x, offset_y))。"""
    sh, sw = frame_bgr.shape[:2]
    x1, y1, x2, y2 = roi
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(sw, max(x1 + 1, x2)), min(sh, max(y1 + 1, y2))
    crop = frame_bgr[y1:y2, x1:x2]
    return crop, (x1, y1)


def _xywh_to_xyxy(roi_xywh: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    x, y, w, h = roi_xywh
    return x, y, x + max(1, w), y + max(1, h)


def _vlm_cards_to_detections(
    cards: list[dict[str, str]],
    zone_key: str,
    roi: tuple[int, int, int, int],
) -> list[CardDetection]:
    """VLM 战区 JSON → CardDetection（中心点按 ROI 内水平均分）。"""
    if not cards:
        return []
    x1, y1, x2, y2 = roi
    width = max(1, x2 - x1)
    cy = int((y1 + y2) / 2)
    detections: list[CardDetection] = []
    n = len(cards)
    for i, card in enumerate(cards):
        label = str(card.get("label") or f"{card['suit']}{card['rank']}").upper()
        cx = int(x1 + (i + 0.5) * width / n)
        detections.append(
            CardDetection(
                class_name=label,
                center_x=cx,
                center_y=cy,
                confidence=1.0,
                zone=zone_key,
            )
        )
    return detections


def _hybrid_infer_vlm_opponent_zone(
    frame_bgr: np.ndarray,
    zone_key: str,
    roi: tuple[int, int, int, int],
) -> tuple[list[CardDetection], float]:
    """裁切对手战区 → 一次 Qwen VLM 列出全部明牌。"""
    from vision_proxy_qwen import analyze_zone_melds_with_qwen

    zone_desc = ZONE_LABELS_CN.get(zone_key, zone_key)
    crop, _ = _crop_frame_roi(frame_bgr, roi)
    if crop.size == 0:
        return [], 0.0

    fd, tmp_path = tempfile.mkstemp(suffix=".jpg")
    os.close(fd)
    t0 = time.perf_counter()
    try:
        if not cv2.imwrite(tmp_path, crop, [int(cv2.IMWRITE_JPEG_QUALITY), 90]):
            return [], (time.perf_counter() - t0) * 1000.0
        cards = analyze_zone_melds_with_qwen(
            tmp_path,
            zone_desc=zone_desc,
            model=_hybrid_vlm_model(),
        )
        dets = _vlm_cards_to_detections(cards, zone_key, roi)
        return dets, (time.perf_counter() - t0) * 1000.0
    except Exception as e:
        logger.warning("[hybrid] VLM 战区 %s 失败: %s", zone_key, e)
        return [], (time.perf_counter() - t0) * 1000.0
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def _setup_scout_logging() -> None:
    """侦察模式日志：时间戳 + 消息，不重复输出 ultralytics 噪声。"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
        force=True,
    )
    # 压低第三方库日志级别，避免污染战报
    logging.getLogger("ultralytics").setLevel(logging.WARNING)


def _format_detection_list(
    detections: list[CardDetection],
    *,
    show_conf: bool,
) -> str:
    if not detections:
        return "无"
    return ", ".join(d.format_brief(show_conf=show_conf) for d in detections)


def _looks_like_turn_scout_result(obj: Any) -> bool:
    """
    兼容 __main__ / main_bot_loop 双模块场景：
    不能只靠 isinstance(TurnScoutResult)。
    """
    return (
        hasattr(obj, "all_detections")
        and hasattr(obj, "by_zone")
        and hasattr(obj, "elapsed_ms")
    )


def _print_scout_report(
    scout_result: TurnScoutResult | list[CardDetection],
    elapsed_ms: float | None = None,
    *,
    capture_backend: str = "",
    frame_shape: tuple[int, int] | None = None,
) -> None:
    """格式化打印战报（支持分区：我的手牌 / 左对手 / 右对手）。"""
    show_conf = (os.environ.get("TONGITS_YOLO_SHOW_CONF") or "").strip() in (
        "1",
        "true",
        "yes",
    )

    is_turn_result = _looks_like_turn_scout_result(scout_result)
    if is_turn_result:
        result = scout_result
        ms = result.elapsed_ms
        all_dets = result.all_detections
        by_zone = result.by_zone
    else:
        ms = elapsed_ms or 0.0
        all_dets = scout_result
        by_zone = {}

    meta = ""
    if capture_backend or frame_shape:
        h, w = frame_shape or (0, 0)
        meta = f" | 截屏={capture_backend or '?'} {w}x{h}"

    show_zones = bool(by_zone) and is_turn_result
    if show_zones:
        logger.info("耗时 %.0fms%s | 全场合计 %d 张", ms, meta, len(all_dets))
        if scout_result.scout_mode in ("qwen_full", "florence_local"):
            tag = "Florence OCR" if scout_result.scout_mode.startswith("florence") else "全屏Qwen"
            logger.info(
                "  [%s] 感知=%.0fms | 一副牌约束=%s",
                tag,
                scout_result.vlm_ms,
                "通过" if scout_result.deck_valid else "未通过",
            )
            if scout_result.deck_issues:
                for issue in scout_result.deck_issues:
                    logger.warning("  [约束] %s", issue)
        elif scout_result.hybrid:
            logger.info(
                "  [混合] YOLO 裁区=%.0fms | 对手 VLM(并行)=%.0fms",
                scout_result.yolo_ms,
                scout_result.vlm_ms,
            )
        for zone_key in TURN_SCOUT_ZONE_ORDER:
            label = ZONE_LABELS_CN.get(zone_key, zone_key)
            dets = by_zone.get(zone_key, [])
            logger.info(
                "  %s %d 张: [%s]",
                label,
                len(dets),
                _format_detection_list(dets, show_conf=show_conf),
            )
        if is_turn_result:
            hand_dets = by_zone.get("player_hand", [])
            if len(hand_dets) < HAND_DETECT_WARN_MIN:
                hand_roi = (scout_result.zone_rois or {}).get("player_hand")
                roi_hint = f" ROI={hand_roi}" if hand_roi else ""
                if scout_result.scout_mode.startswith("florence"):
                    mode_hint = "Florence OCR+HSV，明牌仅标签"
                elif scout_result.scout_mode == "qwen_full":
                    mode_hint = "手牌坐标+VLM，明牌仅标签"
                elif scout_result.hybrid:
                    mode_hint = "混合 YOLO 手牌裁区"
                else:
                    mode_hint = "全屏推理"
                logger.warning(
                    "手牌识别偏少（<%d 张）%s | %s，可调低 --conf 或扩大 HAND_ROI",
                    HAND_DETECT_WARN_MIN,
                    roi_hint,
                    mode_hint,
                )
        return

    if not all_dets:
        raw_n = getattr(result, "raw_detection_count", 0) if is_turn_result else 0
        if raw_n > 0:
            logger.info(
                "耗时 %.0fms%s | 模型检出 %d 张，均未落入战区 ROI",
                ms,
                meta,
                raw_n,
            )
        else:
            logger.info("耗时 %.0fms%s | 视野干净（模型 0 检出）", ms, meta)
        return

    logger.info("耗时 %.0fms%s | 全场合计 %d 张", ms, meta, len(all_dets))

    if by_zone:
        for zone_key in TURN_SCOUT_ZONE_ORDER:
            label = ZONE_LABELS_CN.get(zone_key, zone_key)
            dets = by_zone.get(zone_key, [])
            logger.info(
                "  %s %d 张: [%s]",
                label,
                len(dets),
                _format_detection_list(dets, show_conf=show_conf),
            )
    else:
        parts = _format_detection_list(all_dets, show_conf=show_conf)
        logger.info("识别: [%s]", parts)


def yolo_scout_loop(
    *,
    weights_path: Path,
    conf: float,
    interval_sec: float,
    monitor_index: int,
) -> None:
    """
    YOLO 实时侦察主循环。

    每秒截屏推理一次（可通过 interval_sec 调整），Ctrl+C 安全退出。
    """
    _setup_scout_logging()
    scout = YOLOScreenScout(
        weights_path,
        conf=conf,
        monitor_index=monitor_index,
    )

    logger.info(
        "侦察启动 | 模型=%s | 间隔=%.1fs | 标记图=%s | 按 Ctrl+C 终止",
        weights_path.name,
        interval_sec,
        "关" if not _yolo_save_marked_enabled() else str(YOLO_MARKED_DIR),
    )

    try:
        while True:
            try:
                detections, elapsed_ms = scout.scout_once()
                _print_scout_report(detections, elapsed_ms)
            except Exception as e:
                logger.error("本轮侦察失败: %s", e)

            time.sleep(interval_sec)
    except KeyboardInterrupt:
        logger.info("收到 Ctrl+C，侦察终止")
    finally:
        scout.close()


def yolo_scout_once(
    *,
    weights_path: Path,
    conf: float,
    monitor_index: int,
) -> int:
    """单次全屏 YOLO 侦察（--yolo-once 调试用，不依赖绿圈）。"""
    _setup_scout_logging()
    scout = YOLOScreenScout(
        weights_path,
        conf=conf,
        monitor_index=monitor_index,
    )
    try:
        frame_bgr = scout.capturer.grab()
        scout_result = scout.infer_turn_frame(frame_bgr)
        _print_scout_report(
            scout_result,
            capture_backend=scout.capturer._last_backend,
            frame_shape=frame_bgr.shape[:2],
        )
        return 0
    finally:
        scout.close()


# =============================================================================
# 绿圈回合子系统（轮询探针 + 触发 YOLO / 截图）
# =============================================================================


def _parse_roi_env(name: str, default: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    parts = [p.strip() for p in raw.split(",")]
    if len(parts) != 4:
        return default
    try:
        return tuple(int(p) for p in parts)  # type: ignore[return-value]
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = (os.environ.get(name) or ("1" if default else "0")).strip().lower()
    return raw in ("1", "true", "yes", "on")


def load_turn_runtime_config() -> None:
    global AVATAR_ROI, GREEN_PIXEL_THRESHOLD, GREEN_RING_BORDER_ONLY, POLL_INTERVAL_SEC
    global TURN_CAPTURE_DELAY_SEC, STARTUP_GRACE_SEC, CAPTURE_RETRY_COUNT
    global CAPTURE_RETRY_DELAY_SEC, CAPTURE_TIMEOUT_SEC
    global HAND_CARD_RATIO_MIN, HAND_EDGE_RATIO_MIN
    global TURN_ENTER_FRAMES, TURN_EXIT_FRAMES

    AVATAR_ROI = _parse_roi_env("TONGITS_AVATAR_ROI", AVATAR_ROI)
    GREEN_PIXEL_THRESHOLD = int(
        os.environ.get("TONGITS_GREEN_PIXEL_THRESHOLD", str(GREEN_PIXEL_THRESHOLD))
    )
    GREEN_RING_BORDER_ONLY = _env_bool("TONGITS_GREEN_RING_BORDER_ONLY", GREEN_RING_BORDER_ONLY)
    POLL_INTERVAL_SEC = float(
        os.environ.get("TONGITS_POLL_INTERVAL_SEC", str(POLL_INTERVAL_SEC))
    )
    TURN_CAPTURE_DELAY_SEC = float(
        os.environ.get("TONGITS_TURN_CAPTURE_DELAY_SEC", str(TURN_CAPTURE_DELAY_SEC))
    )
    STARTUP_GRACE_SEC = float(
        os.environ.get("TONGITS_STARTUP_GRACE_SEC", str(STARTUP_GRACE_SEC))
    )
    CAPTURE_RETRY_COUNT = max(
        0, int(os.environ.get("TONGITS_CAPTURE_RETRY_COUNT", str(CAPTURE_RETRY_COUNT)))
    )
    CAPTURE_RETRY_DELAY_SEC = float(
        os.environ.get("TONGITS_CAPTURE_RETRY_DELAY_SEC", str(CAPTURE_RETRY_DELAY_SEC))
    )
    CAPTURE_TIMEOUT_SEC = float(
        os.environ.get("TONGITS_CAPTURE_TIMEOUT_SEC", str(CAPTURE_TIMEOUT_SEC))
    )
    HAND_CARD_RATIO_MIN = float(
        os.environ.get("TONGITS_HAND_CARD_RATIO_MIN", str(HAND_CARD_RATIO_MIN))
    )
    HAND_EDGE_RATIO_MIN = float(
        os.environ.get("TONGITS_HAND_EDGE_RATIO_MIN", str(HAND_EDGE_RATIO_MIN))
    )
    TURN_ENTER_FRAMES = max(
        1, int(os.environ.get("TONGITS_TURN_ENTER_FRAMES", str(TURN_ENTER_FRAMES)))
    )
    TURN_EXIT_FRAMES = max(
        1, int(os.environ.get("TONGITS_TURN_EXIT_FRAMES", str(TURN_EXIT_FRAMES)))
    )


def _require_pyautogui():
    try:
        import pyautogui
    except ImportError as e:
        raise RuntimeError("请安装 pyautogui: pip install pyautogui") from e
    pyautogui.FAILSAFE = True
    return pyautogui


def _capture_avatar_bgr() -> np.ndarray:
    pyautogui = _require_pyautogui()
    left, top, width, height = AVATAR_ROI
    shot = pyautogui.screenshot(region=(left, top, width, height))
    return _prepare_frame_bgr(np.array(shot), "pyautogui", from_native=True)


def _load_hand_roi(sw: int = 1920, sh: int = 1080) -> tuple[int, int, int, int]:
    """手牌 ROI；若截图分辨率与标定不一致则按比例缩放。"""
    env_roi = (os.environ.get("TONGITS_PLAYER_HAND_ROI") or "").strip()
    if env_roi:
        parts = [p.strip() for p in env_roi.split(",")]
        if len(parts) == 4:
            try:
                return tuple(int(p) for p in parts)  # type: ignore[return-value]
            except ValueError:
                pass

    cfg_path = SCRIPTS / "roi_config.json"
    if cfg_path.is_file():
        try:
            data = json.loads(cfg_path.read_text(encoding="utf-8"))
            roi_raw = data.get("roi")
            if isinstance(roi_raw, (list, tuple)) and len(roi_raw) == 4:
                roi = tuple(int(v) for v in roi_raw)  # type: ignore[assignment]
                ref_sw = int(data.get("screen_width") or sw)
                ref_sh = int(data.get("screen_height") or sh)
                return _scale_roi_xyxy(roi, ref_sw=ref_sw, ref_sh=ref_sh, sw=sw, sh=sh)
        except Exception:
            pass

    return _scale_roi_xyxy(
        _DEFAULT_HAND_ROI,
        ref_sw=1920,
        ref_sh=1080,
        sw=sw,
        sh=sh,
    )


def _capture_screen_bgr_impl() -> np.ndarray:
    pyautogui = _require_pyautogui()
    shot = pyautogui.screenshot()
    return _prepare_frame_bgr(np.array(shot), "pyautogui", from_native=True)


def _capture_screen_bgr() -> np.ndarray | None:
    with ThreadPoolExecutor(max_workers=1) as pool:
        fut = pool.submit(_capture_screen_bgr_impl)
        try:
            return fut.result(timeout=CAPTURE_TIMEOUT_SEC)
        except FuturesTimeoutError:
            _turn_log(f"[截图] 超时（>{CAPTURE_TIMEOUT_SEC:.0f}s），跳过")
            return None


def _board_save_path() -> Path:
    now = datetime.now()
    ts = now.strftime("%Y%m%d_%H%M%S") + f"_{now.microsecond // 1000:03d}"
    return OMNI_OUTPUT_DIR / f"{ts}_board_raw.jpg"


def _turn_screenshot_save_enabled() -> bool:
    return (os.environ.get("TONGITS_TURN_SAVE_SCREENSHOT") or "1").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def _save_turn_board_screenshot(bgr: np.ndarray) -> Path | None:
    """每回合侦察成功后存全屏原图到 omnioutput，文件名带毫秒时间戳便于对日志。"""
    if not _turn_screenshot_save_enabled():
        return None
    OMNI_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    save_path = _board_save_path()
    cv2.imwrite(str(save_path), bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
    logger.info("截图已保存 → %s", save_path.resolve())
    return save_path


def _yolo_save_marked_enabled() -> bool:
    return (os.environ.get("TONGITS_YOLO_SAVE_MARKED") or "1").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def _yolo_marked_show_roi() -> bool:
    """是否在标记图上画五战区大框（调 ROI 时开，日常默认关）。"""
    return (os.environ.get("TONGITS_YOLO_MARKED_SHOW_ROI") or "0").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def _marked_save_path(*, card_count: int, raw_count: int) -> Path:
    now = datetime.now()
    ts = now.strftime("%Y%m%d_%H%M%S") + f"_{now.microsecond // 1000:03d}"
    return YOLO_MARKED_DIR / f"{ts}_raw{raw_count}_n{card_count}_marked.jpg"


def _render_yolo_marked_image(
    frame_bgr: np.ndarray,
    yolo_results: list[Any],
    *,
    zone_rois: dict[str, tuple[int, int, int, int]] | None = None,
) -> np.ndarray:
    """YOLO 检测框 + 战区 ROI 叠加到一帧。"""
    if yolo_results and getattr(yolo_results[0], "boxes", None) is not None:
        # plot() 在 numpy BGR 输入下经 cv2 Annotator 返回 BGR；勿再 RGB2BGR（会红蓝反转）
        img = yolo_results[0].plot(conf=False, labels=True)
    else:
        img = frame_bgr.copy()

    if zone_rois and _yolo_marked_show_roi():
        zone_colors: dict[str, tuple[int, int, int]] = {
            "center_discard": (0, 220, 255),
            "opponent_left": (0, 140, 255),
            "opponent_right": (255, 80, 200),
            "my_melds": (255, 200, 0),
            "player_hand": (80, 220, 80),
        }
        for zone_key in ZONE_ASSIGN_ORDER:
            roi = zone_rois.get(zone_key)
            if not roi:
                continue
            x1, y1, x2, y2 = roi
            color = zone_colors.get(zone_key, (200, 200, 200))
            cv2.rectangle(img, (x1, y1), (x2, y2), color, 2)
            label = ZONE_LABELS_CN.get(zone_key, zone_key)
            cv2.putText(
                img,
                label,
                (x1 + 4, max(18, y1 + 18)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.55,
                color,
                2,
                cv2.LINE_AA,
            )
    return img


def _save_yolo_marked_image(
    frame_bgr: np.ndarray,
    yolo_results: list[Any],
    *,
    zone_rois: dict[str, tuple[int, int, int, int]] | None = None,
    card_count: int = 0,
    raw_count: int = 0,
) -> Path | None:
    """保存 YOLO 标注图到 scripts/yolo_marked/。"""
    try:
        YOLO_MARKED_DIR.mkdir(parents=True, exist_ok=True)
        img = _render_yolo_marked_image(
            frame_bgr,
            yolo_results,
            zone_rois=zone_rois,
        )
        save_path = _marked_save_path(card_count=card_count, raw_count=raw_count)
        cv2.imwrite(str(save_path), img, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
        return save_path
    except Exception as e:
        logger.warning("保存标记图失败: %s", e)
        return None


def probe_hand_cards_stats(bgr: np.ndarray) -> dict[str, float]:
    sh, sw = bgr.shape[:2]
    x1, y1, x2, y2 = _load_hand_roi(sw, sh)
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(sw, x2), min(sh, y2)
    zone = bgr[y1:y2, x1:x2]
    if zone.size == 0:
        return {"card_ratio": 0.0, "edge_ratio": 0.0, "roi": (x1, y1, x2, y2)}

    hsv = cv2.cvtColor(zone, cv2.COLOR_BGR2HSV)
    card_mask = (hsv[:, :, 2] >= 150) & (hsv[:, :, 1] <= 90)
    card_ratio = float(np.count_nonzero(card_mask)) / max(1, card_mask.size)

    gray = cv2.cvtColor(zone, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 60, 140)
    edge_ratio = float(np.count_nonzero(edges)) / max(1, edges.size)

    return {"card_ratio": card_ratio, "edge_ratio": edge_ratio, "roi": (x1, y1, x2, y2)}


def _green_mask(hsv: np.ndarray) -> np.ndarray:
    mask = np.zeros(hsv.shape[:2], dtype=np.uint8)
    for lo, hi in _GREEN_RANGES:
        mask = cv2.bitwise_or(mask, cv2.inRange(hsv, lo, hi))
    return mask


def _border_ring_mask(h: int, w: int) -> np.ndarray:
    t = max(4, min(h, w) // 5)
    outer = np.ones((h, w), dtype=np.uint8) * 255
    inner = np.zeros((h, w), dtype=np.uint8)
    cv2.rectangle(inner, (t, t), (max(t + 1, w - t), max(t + 1, h - t)), 255, -1)
    return cv2.subtract(outer, inner)


def _green_border_score(avatar_bgr: np.ndarray) -> int:
    hsv = cv2.cvtColor(avatar_bgr, cv2.COLOR_BGR2HSV)
    mask = _green_mask(hsv)
    h, w = mask.shape[:2]
    if GREEN_RING_BORDER_ONLY:
        border_m = _border_ring_mask(h, w)
        return int(cv2.countNonZero(cv2.bitwise_and(mask, border_m)))
    return int(cv2.countNonZero(mask))


def is_my_turn() -> bool:
    return _green_border_score(_capture_avatar_bgr()) > GREEN_PIXEL_THRESHOLD


def _crop_avatar_from_frame(bgr: np.ndarray) -> np.ndarray:
    """从全屏帧裁切左下角头像区（按分辨率缩放）。"""
    sh, sw = bgr.shape[:2]
    left, top, width, height = AVATAR_ROI
    ref_sw, ref_sh = _AVATAR_REF_SIZE
    x1, y1, x2, y2 = _scale_roi_xyxy(
        (left, top, left + width, top + height),
        ref_sw=ref_sw,
        ref_sh=ref_sh,
        sw=sw,
        sh=sh,
    )
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(sw, x2), min(sh, y2)
    if x2 <= x1 or y2 <= y1:
        return np.empty((0, 0, 3), dtype=np.uint8)
    return bgr[y1:y2, x1:x2]


def _green_border_score_on_frame(bgr: np.ndarray) -> int:
    """从全屏帧同一时刻读取绿圈分数（与 WIN 判定对齐，避免两次截屏不一致）。"""
    avatar = _crop_avatar_from_frame(bgr)
    if avatar.size == 0:
        return 0
    return _green_border_score(avatar)


def is_my_turn_on_frame(bgr: np.ndarray) -> bool:
    return _green_border_score_on_frame(bgr) > GREEN_PIXEL_THRESHOLD


def _avatar_win_badge_scores(avatar_bgr: np.ndarray) -> dict[str, float]:
    """
    统计头像区 WIN 徽标特征。

    真 WIN：大面积亮黄字 + 红飘带，且黄/红在空间上成簇（非头像肤色/筹码零星色点）。
    """
    area = max(1, avatar_bgr.shape[0] * avatar_bgr.shape[1])
    hsv = cv2.cvtColor(avatar_bgr, cv2.COLOR_BGR2HSV)
    yellow = cv2.inRange(hsv, (16, 90, 130), (46, 255, 255))
    bright_yellow = cv2.inRange(hsv, (20, 120, 175), (44, 255, 255))
    red1 = cv2.inRange(hsv, (0, 80, 80), (10, 255, 255))
    red2 = cv2.inRange(hsv, (170, 80, 80), (180, 255, 255))
    red = cv2.bitwise_or(red1, red2)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (9, 9))
    red_dilated = cv2.dilate(red, kernel, iterations=1)
    win_cluster = cv2.bitwise_and(yellow, red_dilated)
    return {
        "yellow_ratio": cv2.countNonZero(yellow) / area,
        "bright_yellow_ratio": cv2.countNonZero(bright_yellow) / area,
        "red_ratio": cv2.countNonZero(red) / area,
        "cluster_ratio": cv2.countNonZero(win_cluster) / area,
    }


def _is_round_end_win_screen(bgr: np.ndarray) -> bool:
    """
    回合结束 WIN 结算：必须「绿圈已消失」且头像区出现大型 WIN 徽标。

    正常打牌时头像/筹码/UI 也可能含黄红像素，但此时绿圈仍在，不得判 WIN。
    """
    avatar = _crop_avatar_from_frame(bgr)
    if avatar.size == 0:
        return False

    green_score = _green_border_score(avatar)
    if green_score > GREEN_PIXEL_THRESHOLD:
        return False

    scores = _avatar_win_badge_scores(avatar)
    yellow_min = float(os.environ.get("TONGITS_WIN_YELLOW_RATIO_MIN", str(WIN_YELLOW_RATIO_MIN)))
    red_min = float(os.environ.get("TONGITS_WIN_RED_RATIO_MIN", str(WIN_RED_RATIO_MIN)))
    bright_min = float(
        os.environ.get("TONGITS_WIN_BRIGHT_YELLOW_RATIO_MIN", str(WIN_BRIGHT_YELLOW_RATIO_MIN))
    )
    cluster_min = float(os.environ.get("TONGITS_WIN_CLUSTER_RATIO_MIN", str(WIN_CLUSTER_RATIO_MIN)))
    strong_yellow = float(
        os.environ.get("TONGITS_WIN_STRONG_YELLOW_RATIO_MIN", str(WIN_STRONG_YELLOW_RATIO_MIN))
    )
    strong_red = float(
        os.environ.get("TONGITS_WIN_STRONG_RED_RATIO_MIN", str(WIN_STRONG_RED_RATIO_MIN))
    )

    clustered_win = (
        scores["bright_yellow_ratio"] >= bright_min
        and scores["red_ratio"] >= red_min
        and scores["cluster_ratio"] >= cluster_min
    )
    strong_win = scores["yellow_ratio"] >= strong_yellow and scores["red_ratio"] >= strong_red
    classic_win = scores["yellow_ratio"] >= yellow_min and scores["red_ratio"] >= red_min

    return clustered_win or strong_win or classic_win


def _log_win_skip_reason(bgr: np.ndarray) -> None:
    """调试：输出 WIN 跳过时的像素特征，便于校准阈值。"""
    avatar = _crop_avatar_from_frame(bgr)
    if avatar.size == 0:
        return
    scores = _avatar_win_badge_scores(avatar)
    green = _green_border_score(avatar)
    logger.info(
        "跳过：头像 WIN 结算徽标（绿圈=%d 黄=%.3f 亮黄=%.3f 红=%.3f 簇=%.3f）",
        green,
        scores["yellow_ratio"],
        scores["bright_yellow_ratio"],
        scores["red_ratio"],
        scores["cluster_ratio"],
    )


def _auto_click_win_enabled() -> bool:
    return _env_bool("TONGITS_AUTO_CLICK_WIN", True)


def _win_click_cooldown_sec() -> float:
    try:
        return max(0.8, float(os.environ.get("TONGITS_WIN_CLICK_COOLDOWN_SEC", "2.0")))
    except ValueError:
        return 2.0


def _auto_fight_defense_enabled() -> bool:
    return _env_bool("TONGITS_AUTO_FIGHT_DEFENSE", True)


def _fight_defense_scatter_max() -> int:
    try:
        return max(0, int(os.environ.get("TONGITS_FIGHT_DEFENSE_SCATTER_MAX", "7")))
    except ValueError:
        return 7


def _fight_offer_click_cooldown_sec() -> float:
    try:
        return max(0.8, float(os.environ.get("TONGITS_FIGHT_OFFER_CLICK_COOLDOWN_SEC", "1.5")))
    except ValueError:
        return 1.5


def _fight_offer_repeat_action_cooldown_sec() -> float:
    try:
        return max(2.0, float(os.environ.get("TONGITS_FIGHT_OFFER_REPEAT_ACTION_COOLDOWN_SEC", "8.0")))
    except ValueError:
        return 8.0


def _fight_use_overlay_point_enabled() -> bool:
    return _env_bool("TONGITS_FIGHT_USE_OVERLAY_POINT", True)


def _fight_use_overlay_point_cloud_enabled() -> bool:
    return _env_bool("TONGITS_FIGHT_USE_OVERLAY_POINT_CLOUD", True)


def _fight_overlay_point_max() -> int:
    try:
        return max(0, int(os.environ.get("TONGITS_FIGHT_OVERLAY_POINT_MAX", "7")))
    except ValueError:
        return 7


def _fight_overlay_point_timeout_sec() -> float:
    try:
        return max(0.8, float(os.environ.get("TONGITS_FIGHT_OVERLAY_POINT_TIMEOUT_SEC", "1.4")))
    except ValueError:
        return 1.4


def _fight_point_cloud_retry_sec() -> float:
    try:
        return max(1.0, float(os.environ.get("TONGITS_FIGHT_POINT_CLOUD_RETRY_SEC", "4.0")))
    except ValueError:
        return 4.0


def _fight_detect_challenge_ratio_min_strict() -> float:
    try:
        return max(0.06, float(os.environ.get("TONGITS_FIGHT_DETECT_CHALLENGE_RATIO_MIN_STRICT", "0.12")))
    except ValueError:
        return 0.12


def _fight_detect_fold_ratio_min_strict() -> float:
    try:
        return max(0.06, float(os.environ.get("TONGITS_FIGHT_DETECT_FOLD_RATIO_MIN_STRICT", "0.12")))
    except ValueError:
        return 0.12


def _fight_require_point_evidence() -> bool:
    return _env_bool("TONGITS_FIGHT_REQUIRE_POINT_EVIDENCE", True)


def _fight_point_ink_ratio_min() -> float:
    try:
        return max(0.01, float(os.environ.get("TONGITS_FIGHT_POINT_INK_RATIO_MIN", "0.045")))
    except ValueError:
        return 0.045


def _fight_offer_poll_sec() -> float:
    try:
        return max(0.6, float(os.environ.get("TONGITS_FIGHT_OFFER_POLL_SEC", "1.2")))
    except ValueError:
        return 1.2


def _fight_ultra_conf_challenge_ratio_min() -> float:
    try:
        return max(0.15, float(os.environ.get("TONGITS_FIGHT_ULTRA_CONF_CHALLENGE_RATIO_MIN", "0.30")))
    except ValueError:
        return 0.30


def _fight_ultra_conf_fold_ratio_min() -> float:
    try:
        return max(0.15, float(os.environ.get("TONGITS_FIGHT_ULTRA_CONF_FOLD_RATIO_MIN", "0.30")))
    except ValueError:
        return 0.30


def _fight_default_action_no_cache() -> str:
    raw = (os.environ.get("TONGITS_FIGHT_DEFAULT_ACTION_NO_CACHE") or "fold").strip().lower()
    if raw in ("challenge", "fold"):
        return raw
    return "fold"


def _fight_skip_after_settlement_sec() -> float:
    try:
        return max(0.0, float(os.environ.get("TONGITS_FIGHT_SKIP_AFTER_SETTLEMENT_SEC", "3.0")))
    except ValueError:
        return 3.0


def _settlement_block_fight_confirm_frames() -> int:
    try:
        return max(1, int(os.environ.get("TONGITS_SETTLEMENT_BLOCK_FIGHT_CONFIRM_FRAMES", "2")))
    except ValueError:
        return 2


def _settlement_block_fight_require_border() -> bool:
    return _env_bool("TONGITS_SETTLEMENT_BLOCK_FIGHT_REQUIRE_BORDER", True)


def _settlement_allow_vlm_failopen() -> bool:
    # 结算判定默认禁用 VLM fail-open，优先保证“不中招误判”。
    return _env_bool("TONGITS_SETTLEMENT_VLM_FAILOPEN", False)


def _auto_click_settlement_continue_enabled() -> bool:
    return _env_bool("TONGITS_AUTO_CLICK_SETTLEMENT_CONTINUE", True)


def _settlement_poll_sec() -> float:
    try:
        return max(0.8, float(os.environ.get("TONGITS_SETTLEMENT_POLL_SEC", "1.2")))
    except ValueError:
        return 1.2


def _settlement_click_cooldown_sec() -> float:
    try:
        return max(0.8, float(os.environ.get("TONGITS_SETTLEMENT_CLICK_COOLDOWN_SEC", "2.0")))
    except ValueError:
        return 2.0


def _settlement_overlay_reset_sec() -> float:
    try:
        return max(1.0, float(os.environ.get("TONGITS_SETTLEMENT_OVERLAY_RESET_SEC", "2.5")))
    except ValueError:
        return 2.5


def _settlement_max_clicks_per_overlay() -> int:
    try:
        return max(1, int(os.environ.get("TONGITS_SETTLEMENT_MAX_CLICKS_PER_OVERLAY", "1")))
    except ValueError:
        return 1


def _settlement_repeat_click_after_sec() -> float:
    try:
        return max(0.0, float(os.environ.get("TONGITS_SETTLEMENT_REPEAT_CLICK_AFTER_SEC", "8.0")))
    except ValueError:
        return 8.0


def _settlement_repeat_click_extra() -> int:
    try:
        return max(0, int(os.environ.get("TONGITS_SETTLEMENT_REPEAT_CLICK_EXTRA", "1")))
    except ValueError:
        return 1


def _settlement_stabilize_sec() -> float:
    try:
        return max(0.0, float(os.environ.get("TONGITS_SETTLEMENT_STABILIZE_SEC", "0.0")))
    except ValueError:
        return 0.0


def _settlement_post_duel_continue_ratio_min() -> float:
    try:
        return max(0.05, float(os.environ.get("TONGITS_SETTLEMENT_POST_DUEL_CONTINUE_RATIO_MIN", "0.09")))
    except ValueError:
        return 0.09


def _settlement_post_duel_details_ratio_min() -> float:
    try:
        return max(0.04, float(os.environ.get("TONGITS_SETTLEMENT_POST_DUEL_DETAILS_RATIO_MIN", "0.06")))
    except ValueError:
        return 0.06


def _settlement_post_duel_timer_ratio_min() -> float:
    try:
        return max(0.005, float(os.environ.get("TONGITS_SETTLEMENT_POST_DUEL_TIMER_RATIO_MIN", "0.01")))
    except ValueError:
        return 0.01


def _settlement_skip_vlm_on_strong_ui() -> bool:
    return _env_bool("TONGITS_SETTLEMENT_SKIP_VLM_ON_STRONG_UI", True)


def _settlement_confirm_frames() -> int:
    try:
        return max(1, int(os.environ.get("TONGITS_SETTLEMENT_CONFIRM_FRAMES", "2")))
    except ValueError:
        return 2


def _settlement_ui_strong_frames() -> int:
    try:
        return max(1, int(os.environ.get("TONGITS_SETTLEMENT_UI_STRONG_FRAMES", "2")))
    except ValueError:
        return 2


def _settlement_release_frames() -> int:
    try:
        return max(1, int(os.environ.get("TONGITS_SETTLEMENT_RELEASE_FRAMES", "2")))
    except ValueError:
        return 2


def _overlay_vlm_poll_sec() -> float:
    try:
        return max(0.8, float(os.environ.get("TONGITS_OVERLAY_VLM_POLL_SEC", "1.6")))
    except ValueError:
        return 1.6


def _overlay_vlm_timeout_sec() -> float:
    try:
        return max(1.2, float(os.environ.get("TONGITS_OVERLAY_VLM_TIMEOUT_SEC", "4.8")))
    except ValueError:
        return 4.8


def _overlay_vlm_failopen_after() -> int:
    try:
        return max(1, int(os.environ.get("TONGITS_OVERLAY_VLM_FAILOPEN_AFTER", "3")))
    except ValueError:
        return 3


def _overlay_vlm_failopen_sec() -> float:
    try:
        return max(1.0, float(os.environ.get("TONGITS_OVERLAY_VLM_FAILOPEN_SEC", "12.0")))
    except ValueError:
        return 12.0


def _settlement_coin_probe_poll_sec() -> float:
    try:
        return max(0.5, float(os.environ.get("TONGITS_SETTLEMENT_COIN_POLL_SEC", "0.8")))
    except ValueError:
        return 0.8


def _settlement_coin_vlm_timeout_sec() -> float:
    try:
        return max(1.2, float(os.environ.get("TONGITS_SETTLEMENT_COIN_TIMEOUT_SEC", "4.6")))
    except ValueError:
        return 4.6


def _settlement_coin_retry_count() -> int:
    try:
        return max(1, int(os.environ.get("TONGITS_SETTLEMENT_COIN_RETRIES", "2")))
    except ValueError:
        return 2


def _settlement_panel_vlm_timeout_sec() -> float:
    try:
        return max(
            1.2,
            float(
                os.environ.get(
                    "TONGITS_SETTLEMENT_PANEL_TIMEOUT_SEC",
                    "4.8",
                )
            ),
        )
    except ValueError:
        return 4.8


def _settlement_panel_retry_count() -> int:
    try:
        return max(1, int(os.environ.get("TONGITS_SETTLEMENT_PANEL_RETRIES", "2")))
    except ValueError:
        return 2


def _settlement_lock_hold_sec() -> float:
    try:
        return max(0.5, float(os.environ.get("TONGITS_SETTLEMENT_LOCK_HOLD_SEC", "3.0")))
    except ValueError:
        return 3.0


def _settlement_delta_fallback_enabled() -> bool:
    return _env_bool("TONGITS_SETTLEMENT_DELTA_FALLBACK", True)


def _settlement_after_duel_hold_sec() -> float:
    try:
        return max(5.0, float(os.environ.get("TONGITS_SETTLEMENT_AFTER_DUEL_HOLD_SEC", "45.0")))
    except ValueError:
        return 45.0


def _settlement_conflict_grace_sec() -> float:
    try:
        return max(0.0, float(os.environ.get("TONGITS_SETTLEMENT_CONFLICT_GRACE_SEC", "3.0")))
    except ValueError:
        return 3.0


def _settlement_release_on_vlm_miss_streak() -> int:
    try:
        return max(1, int(os.environ.get("TONGITS_SETTLEMENT_RELEASE_ON_VLM_MISS_STREAK", "4")))
    except ValueError:
        return 4


def _settlement_coin_allow_no_duel() -> bool:
    # 允许“无决斗直接结算”场景下识别金币（需已 dump 且已确认处于结算页）。
    return _env_bool("TONGITS_SETTLEMENT_COIN_ALLOW_NO_DUEL", True)


def _coin_use_proto_enabled() -> bool:
    # CDP 结算开启时不再走旧 17888 bridge 金币通道。
    if _coin_use_cdp_enabled():
        return False
    return _env_bool("TONGITS_COIN_USE_PROTO", False)


def _coin_use_cdp_enabled() -> bool:
    """默认 True：Chrome CDP 后台线程抓 3016 结算帧记账。"""
    return _env_bool("TONGITS_COIN_USE_CDP", True)


def _settlement_api_only() -> bool:
    """
    仅协议 3016 记账（browser_proto_bridge / CDP），不截结算页、不 OCR/VLM 读金币。
    显式 TONGITS_SETTLEMENT_VISUAL=1 才恢复旧视觉路径。
    """
    return not _env_bool("TONGITS_SETTLEMENT_VISUAL", False)


def _my_player_name() -> str:
    return str(os.environ.get("TONGITS_MY_NAME") or "victor").strip()


def _cdp_settlement_fallback_sec() -> float:
    """CDP 开启但超时未记账时，降级视觉读金币的等待秒数（须大于 3016 去抖 2.5s）。"""
    try:
        return max(3.0, float(os.environ.get("TONGITS_CDP_SETTLE_FALLBACK_SEC") or "6.0"))
    except ValueError:
        return 6.0


def _sync_api_settlement_from_proto() -> bool:
    """从 proto_status.json（bridge/CDP 写入）同步 [结算] 行到主日志并 latch。"""
    global _settlement_coin_overlay_latched, _last_api_settlement_at_seen
    if _settlement_coin_overlay_latched:
        return True
    obj = _load_proto_status_file()
    at = str(obj.get("settlement_record_at") or "").strip()
    line = str(obj.get("settlement_record_line") or "").strip()
    if not line or not at or at == _last_api_settlement_at_seen:
        return False
    _last_api_settlement_at_seen = at
    _settlement_coin_overlay_latched = True
    logger.info("%s", line)
    return True


def _on_proto_cdp_settlement(data: dict[str, Any]) -> None:
    """CDP 后台线程回调：落盘 settlement.log + 打主日志，并 latch 防重复/视觉双记。"""
    global _settlement_coin_overlay_latched
    logger.info("%s", str(data.get("line") or ""))
    _settlement_coin_overlay_latched = True


def _start_proto_settlement_service_if_enabled() -> None:
    global _proto_settlement_service
    if not _coin_use_cdp_enabled():
        logger.info("[proto] CDP 结算=关（TONGITS_COIN_USE_CDP=0），仍走视觉/OCR 金币路径")
        return
    if _proto_settlement_service is not None:
        return
    try:
        from tongits_proto_settlement_service import ProtoSettlementService

        port = int(os.environ.get("TONGITS_CDP_PORT") or "9222")
        launch = _env_bool("TONGITS_CDP_LAUNCH_CHROME", True)
        discover = _env_bool("TONGITS_CDP_DISCOVER", False)
        _proto_settlement_service = ProtoSettlementService(
            my_name=_my_player_name(),
            out_dir=OMNI_OUTPUT_DIR,
            on_settlement=_on_proto_cdp_settlement,
            port=port,
            url_filter=str(os.environ.get("TONGITS_CDP_URL_FILTER") or "herontest"),
            launch_chrome=launch,
            game_url=os.environ.get("TONGITS_CDP_GAME_URL"),
            discover=discover,
        )
        _proto_settlement_service.start_daemon()
        logger.info(
            "[proto] CDP 结算后台线程已启动 port=%s my=%s launch_chrome=%s",
            port,
            _my_player_name(),
            launch,
        )
    except Exception as exc:
        logger.warning("[proto] CDP 结算后台线程启动失败：%s", exc)
        _proto_settlement_service = None


def _stop_proto_settlement_service() -> None:
    global _proto_settlement_service
    svc = _proto_settlement_service
    _proto_settlement_service = None
    if svc is not None:
        try:
            svc.stop()
        except Exception:
            pass


def _proto_log_enabled() -> bool:
    return _env_bool("TONGITS_PROTO_LOG_ENABLED", True)


def _proto_status_poll_sec() -> float:
    try:
        return max(0.2, float(os.environ.get("TONGITS_PROTO_STATUS_POLL_SEC", "1.0")))
    except ValueError:
        return 1.0


def _proto_status_heartbeat_sec() -> float:
    try:
        return max(1.0, float(os.environ.get("TONGITS_PROTO_HEARTBEAT_SEC", "5.0")))
    except ValueError:
        return 5.0


def _proto_status_file_path() -> Path:
    raw = os.environ.get(
        "TONGITS_PROTO_STATUS_FILE",
        str(OMNI_OUTPUT_DIR / "proto_status.json"),
    )
    return Path(raw)


def _proto_signal_window_sec() -> float:
    try:
        return max(1.0, float(os.environ.get("TONGITS_PROTO_SIGNAL_WINDOW_SEC", "8.0")))
    except ValueError:
        return 8.0


def _parse_iso_ts_to_utc(ts: str) -> datetime | None:
    text = (ts or "").strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def _proto_signal_recent(file_obj: dict[str, Any], signal_key: str) -> bool:
    val = str(file_obj.get(signal_key, "-"))
    if not val or val == "-":
        return False
    ts_key = f"{signal_key}_at"
    ts = _parse_iso_ts_to_utc(str(file_obj.get(ts_key, "")))
    if ts is None:
        return False
    age = (datetime.now(timezone.utc) - ts).total_seconds()
    return age <= _proto_signal_window_sec()


def _load_proto_status_file() -> dict[str, Any]:
    p = _proto_status_file_path()
    if not p.exists():
        return {}
    try:
        raw = p.read_text(encoding="utf-8").strip()
    except OSError:
        return {}
    if not raw:
        return {}
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return obj if isinstance(obj, dict) else {}


def _log_proto_status(*, reason: str, force: bool = False) -> None:
    global _last_proto_status_log_at, _last_proto_status_digest
    if not _proto_log_enabled():
        return
    now = time.perf_counter()
    if not force and (now - _last_proto_status_log_at) < _proto_status_poll_sec():
        return

    file_obj = _load_proto_status_file()
    file_api = file_obj.get("api") if isinstance(file_obj.get("api"), dict) else {}
    file_ws = file_obj.get("ws") if isinstance(file_obj.get("ws"), dict) else {}
    file_source = str(file_obj.get("source", "none"))

    snap = {
        "mode": os.environ.get("TONGITS_PROTO_MODE", "visual_only"),
        "source": os.environ.get("TONGITS_PROTO_SOURCE", file_source),
        "api": os.environ.get("TONGITS_PROTO_API_STATE", str(file_api.get("state", "off"))),
        "ws": os.environ.get("TONGITS_PROTO_WS_STATE", str(file_ws.get("state", "off"))),
        "duel": os.environ.get("TONGITS_PROTO_DUEL_STATE", str(file_obj.get("duel", "-"))),
        "settlement": os.environ.get("TONGITS_PROTO_SETTLEMENT_STATE", str(file_obj.get("settlement", "-"))),
        "coin": os.environ.get("TONGITS_PROTO_COIN_STATE", str(file_obj.get("coin", "-"))),
        "duel_recent": int(_proto_signal_recent(file_obj, "duel")),
        "settlement_recent": int(_proto_signal_recent(file_obj, "settlement")),
        "coin_recent": int(_proto_signal_recent(file_obj, "coin")),
        "pending_settlement": int(_pending_settlement_after_duel),
        "settlement_gate": int(_settlement_coin_probe_armed),
        "coin_latched": int(_settlement_coin_overlay_latched),
        "lock_frame": int(_last_settlement_locked_frame is not None),
    }
    digest = json.dumps(snap, ensure_ascii=True, sort_keys=True)
    changed = digest != _last_proto_status_digest
    heartbeat_due = (now - _last_proto_status_log_at) >= _proto_status_heartbeat_sec()
    if (not force) and reason == "waiting_tick" and (not changed):
        return
    if force or changed or heartbeat_due:
        logger.info(
            "[proto] reason=%s mode=%s source=%s api=%s ws=%s duel=%s settlement=%s coin=%s "
            "recent(d/s/c)=%d/%d/%d pending=%d gate=%d coin_latched=%d lock=%d",
            reason,
            snap["mode"],
            snap["source"],
            snap["api"],
            snap["ws"],
            snap["duel"],
            snap["settlement"],
            snap["coin"],
            snap["duel_recent"],
            snap["settlement_recent"],
            snap["coin_recent"],
            snap["pending_settlement"],
            snap["settlement_gate"],
            snap["coin_latched"],
            snap["lock_frame"],
        )
        _last_proto_status_digest = digest
        _last_proto_status_log_at = now


def _my_coin_roi_xywh(bgr: np.ndarray) -> tuple[int, int, int, int]:
    """
    我的金币“余额”显示区域（左下角本人头像旁的余额胶囊，例如 victor 1.37M）。
    注意：旧默认 y=360 会框到左上角“对手 Regndo”，必须指向左下角本人。
    可通过环境变量覆盖（基于 1920x1080 经验值）。
    """
    sh, sw = bgr.shape[:2]
    x = int(os.environ.get("TONGITS_MY_COIN_ROI_X", "15"))
    y = int(os.environ.get("TONGITS_MY_COIN_ROI_Y", "860"))
    w = int(os.environ.get("TONGITS_MY_COIN_ROI_W", "280"))
    h = int(os.environ.get("TONGITS_MY_COIN_ROI_H", "110"))
    x = max(0, min(sw - 1, x))
    y = max(0, min(sh - 1, y))
    w = max(1, min(w, sw - x))
    h = max(1, min(h, sh - y))
    return x, y, w, h


def _my_settlement_delta_roi_xywh(bgr: np.ndarray) -> tuple[int, int, int, int]:
    """
    结算页“我方本局增减”数字区域（本人座位在左下角，赢为黄色 +1500、输为红色 -XXX）。
    位于左下角本人头像“上方”的大号带符号数字。基于 1920x1080 经验值，可环境变量覆盖。
    """
    sh, sw = bgr.shape[:2]
    x = int(os.environ.get("TONGITS_MY_DELTA_ROI_X", "12"))
    y = int(os.environ.get("TONGITS_MY_DELTA_ROI_Y", "600"))
    w = int(os.environ.get("TONGITS_MY_DELTA_ROI_W", "310"))
    h = int(os.environ.get("TONGITS_MY_DELTA_ROI_H", "150"))
    x = max(0, min(sw - 1, x))
    y = max(0, min(sh - 1, y))
    w = max(1, min(w, sw - x))
    h = max(1, min(h, sh - y))
    return x, y, w, h


def _settlement_panel_roi_xywh(bgr: np.ndarray) -> tuple[int, int, int, int]:
    """
    结算面板主区域（用于备用识别 +3000/-1500）。
    默认按 1920x1080 经验值，可环境变量覆盖。
    """
    sh, sw = bgr.shape[:2]
    x = int(os.environ.get("TONGITS_SETTLEMENT_PANEL_ROI_X", "220"))
    y = int(os.environ.get("TONGITS_SETTLEMENT_PANEL_ROI_Y", "80"))
    w = int(os.environ.get("TONGITS_SETTLEMENT_PANEL_ROI_W", "1480"))
    h = int(os.environ.get("TONGITS_SETTLEMENT_PANEL_ROI_H", "760"))
    x = max(0, min(sw - 1, x))
    y = max(0, min(sh - 1, y))
    w = max(1, min(w, sw - x))
    h = max(1, min(h, sh - y))
    return x, y, w, h


def _parse_coin_value(text: str) -> float | None:
    raw = (text or "").strip().upper().replace(",", "")
    if not raw:
        return None
    m = re.search(r"(\d+(?:\.\d+)?)([KMB]?)", raw)
    if not m:
        return None
    try:
        base = float(m.group(1))
    except ValueError:
        return None
    unit = m.group(2)
    mult = 1.0
    if unit == "K":
        mult = 1_000.0
    elif unit == "M":
        mult = 1_000_000.0
    elif unit == "B":
        mult = 1_000_000_000.0
    return base * mult


def _proto_coin_delta_from_status(file_obj: dict[str, Any]) -> tuple[int | None, str]:
    raw = file_obj.get("coin_delta")
    ts = str(file_obj.get("coin_delta_at") or "")
    if raw is None:
        return None, ts
    try:
        return int(raw), ts
    except (TypeError, ValueError):
        return None, ts


def _local_ocr_signed_delta_from_panel(panel_crop: np.ndarray, *, tight: bool = False) -> int | None:
    """
    本地 OCR（非 VLM）识别结算面板中的带符号数值（例如 +4000 / -2000）。
    依赖可选 pytesseract；不可用时返回 None。

    tight=True 时表示传入的已是“我方增减”紧裁剪区域（如左下座位 +1500），
    跳过“上半区中间带”子裁剪，直接对整块 OCR，避免裁飞唯一的数字。
    """
    try:
        import pytesseract  # type: ignore
    except Exception:
        return None
    if panel_crop is None or panel_crop.size == 0:
        return None
    try:
        def _ocr_ints(crop: np.ndarray) -> tuple[list[int], list[int]]:
            hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
            yellow = cv2.inRange(hsv, (10, 65, 110), (45, 255, 255))
            white = cv2.inRange(hsv, (0, 0, 165), (180, 80, 255))
            mask = cv2.bitwise_or(yellow, white)
            gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
            _, bw = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            masks = [mask, bw]
            signed: list[int] = []
            unsigned: list[int] = []
            for m in masks:
                up = cv2.resize(m, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC)
                for psm in (7, 6):
                    cfg = f"--psm {psm} -c tessedit_char_whitelist=+-WINLOSEwinlose0123456789,"
                    txt = pytesseract.image_to_string(up, config=cfg) or ""
                    for token in re.findall(r"([+-])\s*(\d[\d,]{1,8})", txt):
                        try:
                            v = int(token[1].replace(",", ""))
                            signed.append(v if token[0] == "+" else -v)
                        except ValueError:
                            continue
                    # 兜底：若没有显式符号，结合 WIN/LOSE 文本推符号
                    sign_hint = 0
                    txt_u = txt.upper()
                    if "WIN" in txt_u:
                        sign_hint = 1
                    elif "LOSE" in txt_u:
                        sign_hint = -1
                    for token in re.findall(r"\b(\d[\d,]{2,8})\b", txt):
                        try:
                            v = int(token.replace(",", ""))
                            if sign_hint == 0:
                                unsigned.append(v)
                            else:
                                signed.append(v if sign_hint > 0 else -v)
                        except ValueError:
                            continue
            return signed, unsigned

        if tight:
            s0, u0 = _ocr_ints(panel_crop)
            if s0:
                return max(s0, key=lambda v: abs(v))
            if u0:
                return max(u0, key=lambda v: abs(v))
            return None

        h, w = panel_crop.shape[:2]
        # 我方金币增减通常位于结算面板上半区中间，先扫此区域，减少读到对手 -2000/+2000 的概率。
        y1 = int(h * 0.22)
        y2 = int(h * 0.58)
        x1 = int(w * 0.20)
        x2 = int(w * 0.82)
        my_band = panel_crop[max(0, y1) : min(h, y2), max(0, x1) : min(w, x2)]

        s1, u1 = _ocr_ints(my_band if my_band.size != 0 else panel_crop)
        if s1:
            return max(s1, key=lambda v: abs(v))
        if u1:
            return max(u1, key=lambda v: abs(v))

        s2, u2 = _ocr_ints(panel_crop)
        if s2:
            return max(s2, key=lambda v: abs(v))
        if u2:
            return max(u2, key=lambda v: abs(v))
        return None
    except Exception:
        return None


def _set_pending_settlement(enabled: bool, *, reason: str, now: float | None = None) -> None:
    """
    显式状态日志：pending_settlement ON/OFF（仅状态变化时打印）。
    """
    global _pending_settlement_after_duel, _pending_settlement_since
    global _settlement_vlm_miss_streak
    ts = time.perf_counter() if now is None else now
    if enabled:
        if not _pending_settlement_after_duel:
            _pending_settlement_after_duel = True
            _pending_settlement_since = ts
            logger.info("[state] pending_settlement=ON reason=%s", reason)
        return
    if _pending_settlement_after_duel:
        held = max(0.0, ts - _pending_settlement_since)
        _pending_settlement_after_duel = False
        _pending_settlement_since = 0.0
        _settlement_vlm_miss_streak = 0
        logger.info("[state] pending_settlement=OFF reason=%s held=%.1fs", reason, held)


def _refresh_settlement_probe_arm(*, reason: str) -> None:
    """
    结算金币识别门闸：
    最硬门槛：必须已成功点击过 settlement_continue。
    其余仍维持：
    - 默认要求“已出牌(Dump) + 已发生决斗(主动或被动)”；
    - 若开启 no-duel 模式，则“已出牌 + 已确认结算页”也可开启。
    """
    global _settlement_coin_probe_armed
    ui_strong_ready = _settlement_ui_strong_streak >= _settlement_ui_strong_frames()
    should_arm = (_settlement_continue_clicked_seen or ui_strong_ready) and _settlement_dump_seen and (
        _settlement_duel_seen
        or (_settlement_coin_allow_no_duel() and _settlement_overlay_seen)
    )
    if should_arm and not _settlement_coin_probe_armed:
        _settlement_coin_probe_armed = True
        logger.info("[state] settlement_coin_probe=ON reason=%s", reason)
    elif (not should_arm) and _settlement_coin_probe_armed:
        _settlement_coin_probe_armed = False
        logger.info("[state] settlement_coin_probe=OFF reason=%s", reason)


def _mark_settlement_dump_seen(*, reason: str) -> None:
    global _settlement_dump_seen
    if not _settlement_dump_seen:
        _settlement_dump_seen = True
        logger.info("[state] settlement_dump_seen=ON reason=%s", reason)
    _refresh_settlement_probe_arm(reason=reason)


def _mark_settlement_duel_seen(*, reason: str) -> None:
    global _settlement_duel_seen
    if not _settlement_duel_seen:
        _settlement_duel_seen = True
        logger.info("[state] settlement_duel_seen=ON reason=%s", reason)
    _refresh_settlement_probe_arm(reason=reason)


def _mark_settlement_overlay_seen(*, reason: str) -> None:
    global _settlement_overlay_seen, _settlement_coin_overlay_latched
    if not _settlement_overlay_seen:
        _settlement_overlay_seen = True
        # 新一轮结算覆盖层出现时，解除金币识别锁存，允许重新识别本局金币变化。
        _settlement_coin_overlay_latched = False
        logger.info("[state] settlement_overlay_seen=ON reason=%s", reason)
    _refresh_settlement_probe_arm(reason=reason)


def _mark_settlement_continue_clicked(*, reason: str) -> None:
    global _settlement_continue_clicked_seen
    if not _settlement_continue_clicked_seen:
        _settlement_continue_clicked_seen = True
        logger.info("[state] settlement_continue_clicked=ON reason=%s", reason)
    _refresh_settlement_probe_arm(reason=reason)


def _reset_settlement_coin_gate(*, reason: str) -> None:
    global _settlement_dump_seen, _settlement_duel_seen, _settlement_overlay_seen
    global _settlement_continue_clicked_seen
    global _settlement_coin_probe_armed
    global _settlement_coin_overlay_latched
    global _settlement_ui_strong_streak
    global _settlement_block_fight_streak
    global _settlement_vlm_miss_streak
    global _settlement_overlay_first_seen_at
    global _settlement_candidate_until
    global _settlement_retry_once_done
    changed = (
        _settlement_dump_seen
        or _settlement_duel_seen
        or _settlement_overlay_seen
        or _settlement_continue_clicked_seen
        or _settlement_coin_probe_armed
    )
    _settlement_dump_seen = False
    _settlement_duel_seen = False
    _settlement_overlay_seen = False
    _settlement_continue_clicked_seen = False
    _settlement_coin_probe_armed = False
    _settlement_coin_overlay_latched = False
    _settlement_ui_strong_streak = 0
    _settlement_block_fight_streak = 0
    _settlement_vlm_miss_streak = 0
    _settlement_overlay_first_seen_at = 0.0
    _settlement_candidate_until = 0.0
    _settlement_retry_once_done = False
    if changed:
        logger.info("[state] settlement_gate=RESET reason=%s", reason)


def _clear_settlement_lock(*, reason: str) -> None:
    global _last_settlement_locked_frame, _last_settlement_locked_at
    if _last_settlement_locked_frame is not None:
        _last_settlement_locked_frame = None
        _last_settlement_locked_at = 0.0
        logger.info("[state] settlement_lock=OFF reason=%s", reason)


def _coin_delta_log_path() -> Path:
    return OMNI_OUTPUT_DIR / "coin_delta.log"


def _coin_delta_csv_path() -> Path:
    return OMNI_OUTPUT_DIR / "coin_delta.csv"


def _append_coin_delta_csv_row(
    *,
    my_delta: int | None,
    opponents: list[dict[str, Any]],
    source: str,
) -> None:
    """
    同步输出 CSV，字段固定：时间、我方本局、对手1、对手2、来源。
    """
    try:
        OMNI_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=8))).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        opp_parts: list[str] = []
        for item in opponents:
            name = str(item.get("name") or "").strip() or "对手"
            d = item.get("delta")
            if isinstance(d, int):
                opp_parts.append(f"{name}:{d:+d}")
        opp1 = opp_parts[0] if len(opp_parts) >= 1 else ""
        opp2 = opp_parts[1] if len(opp_parts) >= 2 else ""
        my_str = "" if my_delta is None else f"{my_delta:+d}"

        csv_path = _coin_delta_csv_path()
        is_new = not csv_path.exists()
        with csv_path.open("a", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            if is_new:
                w.writerow(["时间", "我方本局", "对手1", "对手2", "来源"])
            w.writerow([ts, my_str, opp1, opp2, source])
        logger.info(
            "[settlement] coin_delta.csv 已写入 source=%s my=%s opp1=%s opp2=%s",
            source,
            my_str or "-",
            opp1 or "-",
            opp2 or "-",
        )
    except Exception as e:
        logger.warning("[settlement] coin_delta.csv 写入失败: %s", e)


def _append_coin_delta_log_line(
    *,
    coin_text: str,
    coin_value: float,
    delta: float | None,
    note: str,
) -> None:
    """
    落盘结算金币变化记录（每次结算页仅一条）。
    """
    try:
        OMNI_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        # 强制使用 UTC+8 时间戳，避免受系统本地时区影响。
        ts = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=8))).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        delta_str = "" if delta is None else f"{delta:+.0f}"
        line = (
            f"{ts}\t我的金币={coin_text}\t数值={coin_value:.0f}\t"
            f"本局变动={delta_str}\t类型={note}\n"
        )
        _coin_delta_log_path().open("a", encoding="utf-8").write(line)
    except Exception as e:
        logger.warning("[settlement] coin_delta.log 写入失败: %s", e)


def _append_settlement_round_log_line(
    *,
    my_delta: int | None,
    opponents: list[dict[str, Any]],
    note: str,
) -> None:
    """
    结算面板语义落盘：我方本局 +/- 与对手 +/-。
    """
    try:
        OMNI_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=8))).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        my_str = "-" if my_delta is None else f"{my_delta:+d}"
        opp_parts: list[str] = []
        for item in opponents:
            name = str(item.get("name") or "").strip() or "对手"
            d = item.get("delta")
            if not isinstance(d, int):
                continue
            opp_parts.append(f"{name}:{d:+d}")
        opps_str = ";".join(opp_parts) if opp_parts else "-"
        line = f"{ts}\t本局我方={my_str}\t对手={opps_str}\t类型={note}\n"
        _coin_delta_log_path().open("a", encoding="utf-8").write(line)
        _append_coin_delta_csv_row(
            my_delta=my_delta,
            opponents=opponents,
            source=note,
        )
    except Exception as e:
        logger.warning("[settlement] 结算语义日志写入失败: %s", e)


def _auto_play_dry_run_enabled() -> bool:
    from tongits_coord_executor import auto_play_dry_run

    return bool(auto_play_dry_run())


def _fight_fallback_last_scatter_enabled() -> bool:
    return _env_bool("TONGITS_FIGHT_FALLBACK_LAST_SCATTER", True)


def _fight_defense_scatter_for_decision(
    *,
    yolo_scatter: int | None,
    cached_scatter: int | None,
) -> tuple[int, str]:
    """
    决斗应答用的保守散牌点评估：
    - 同时有 yolo + cache 时取 max，避免遮罩误检把高点数误判成低点数。
    - 仅有其一时用其值。
    - 都没有时给高风险兜底值（默认倾向 fold）。
    """
    if yolo_scatter is not None and cached_scatter is not None:
        return max(yolo_scatter, cached_scatter), "max(yolo,cache)"
    if yolo_scatter is not None:
        return yolo_scatter, "yolo"
    if cached_scatter is not None:
        return cached_scatter, "cache"
    return 99, "fallback"


def _hand_scatter_from_detections(dets: list[CardDetection]) -> int | None:
    from tongits_rules import label_to_hand_card, loose_scatter_points

    cards = []
    for d in dets:
        hc = label_to_hand_card(
            str(getattr(d, "class_name", "") or ""),
            center_x=int(getattr(d, "center_x", 0) or 0),
            center_y=int(getattr(d, "center_y", 0) or 0),
        )
        if hc is not None:
            cards.append(hc)
    if not cards:
        return None
    return int(loose_scatter_points(cards))


def _scatter_from_hand_labels(labels: list[str]) -> int | None:
    from tongits_rules import label_to_hand_card, loose_scatter_points

    cards = []
    for lb in labels:
        hc = label_to_hand_card(str(lb or ""))
        if hc is not None:
            cards.append(hc)
    if not cards:
        return None
    return int(loose_scatter_points(cards))


def _estimate_hand_scatter_yolo_only(scout: TurnScout, bgr: np.ndarray) -> int | None:
    if not hasattr(scout, "_infer_yolo_hand_zone"):
        return None
    try:
        zone_rois = _load_board_zone_rois(bgr)
        yolo_by_zone, _results, _ms = scout._infer_yolo_hand_zone(bgr, zone_rois)  # type: ignore[attr-defined]
        hand_dets = _exclude_non_hand_yolo_boxes(
            yolo_by_zone.get("player_hand", []),
            zone_rois,
        )
        return _hand_scatter_from_detections(hand_dets)
    except Exception:
        return None


def _estimate_fight_overlay_point(bgr: np.ndarray) -> int | None:
    from tongits_ui_probe import duel_point_local_ocr, duel_point_roi_xywh
    from vision_proxy_qwen import analyze_duel_point_with_qwen

    global _last_fight_point_cloud_try_at
    local_point = duel_point_local_ocr(bgr, log_details=False)
    cloud_point: int | None = None

    trust_local = _env_bool("TONGITS_FIGHT_POINT_TRUST_LOCAL", True)
    now = time.perf_counter()
    allow_cloud = (
        _fight_use_overlay_point_cloud_enabled()
        and (now - _last_fight_point_cloud_try_at) >= _fight_point_cloud_retry_sec()
        and (local_point is None or not trust_local)
    )
    if allow_cloud:
        _last_fight_point_cloud_try_at = now
        roi_xyxy = _xywh_to_xyxy(duel_point_roi_xywh(bgr.shape))
        crop, _ = _crop_frame_roi(bgr, roi_xyxy)
        h, w = crop.shape[:2] if crop.size != 0 else (0, 0)
        if w < 12 or h < 12:
            logger.warning("[fight] 跳过决斗POINT云端识别：ROI过小 %dx%d", w, h)
            crop = np.empty((0, 0, 3), dtype=np.uint8)
        if crop.size != 0:
            tmp_path = _save_vlm_crop_jpeg(crop)
            try:
                cloud_point = analyze_duel_point_with_qwen(
                    tmp_path,
                    model=_qwen_vlm_model(),
                    timeout_sec=_fight_overlay_point_timeout_sec(),
                )
            finally:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

    if local_point is not None and cloud_point is not None:
        if abs(local_point - cloud_point) <= 2:
            point = local_point
            source = "dual_agree"
        else:
            point = max(local_point, cloud_point)
            source = "dual_diverge_max"
    elif local_point is not None:
        point = local_point
        source = "local_ocr"
    elif cloud_point is not None:
        point = cloud_point
        source = "cloud_vlm"
    else:
        return None
    logger.info(
        "[fight] 决斗POINT双通道: point=%d source=%s local=%s cloud=%s",
        point,
        source,
        local_point if local_point is not None else "-",
        cloud_point if cloud_point is not None else "-",
    )
    return point


def _duel_point_ink_ratio(bgr: np.ndarray) -> float:
    from tongits_ui_probe import duel_point_roi_xywh

    roi_xyxy = _xywh_to_xyxy(duel_point_roi_xywh(bgr.shape))
    crop, _ = _crop_frame_roi(bgr, roi_xyxy)
    if crop.size == 0:
        return 0.0
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    _, bw = cv2.threshold(gray, 118, 255, cv2.THRESH_BINARY_INV)
    bw = cv2.medianBlur(bw, 3)
    return float(np.count_nonzero(bw) / max(1, bw.size))


def _waiting_overlay_roi(bgr: np.ndarray) -> tuple[int, int, int, int]:
    sh, sw = bgr.shape[:2]
    x1 = int(sw * 0.18)
    x2 = int(sw * 0.82)
    y1 = int(sh * 0.52)
    y2 = int(sh * 0.98)
    return (x1, y1, x2, y2)


def _classify_waiting_overlay_with_vlm(bgr: np.ndarray) -> str:
    from vision_proxy_qwen import analyze_waiting_overlay_type_with_qwen

    roi = _waiting_overlay_roi(bgr)
    crop, _ = _crop_frame_roi(bgr, roi)
    if crop.size == 0:
        return "none"
    tmp_path = _save_vlm_crop_jpeg(crop)
    try:
        return analyze_waiting_overlay_type_with_qwen(
            tmp_path,
            model=_qwen_vlm_model(),
            timeout_sec=_overlay_vlm_timeout_sec(),
        )
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def _overlay_type_matches_with_vlm(
    bgr: np.ndarray,
    *,
    expected: str,
    ui_confident: bool = False,
    allow_failopen: bool = True,
) -> bool:
    global _last_overlay_vlm_at, _last_overlay_vlm_type
    global _overlay_vlm_mismatch_streak, _overlay_vlm_failopen_until
    now = time.perf_counter()
    failopen_until = float(_overlay_vlm_failopen_until.get(expected, 0.0))
    if allow_failopen and ui_confident and now < failopen_until:
        return True
    if now - _last_overlay_vlm_at >= _overlay_vlm_poll_sec():
        _last_overlay_vlm_type = _classify_waiting_overlay_with_vlm(bgr)
        _last_overlay_vlm_at = now
    if _last_overlay_vlm_type == expected:
        _overlay_vlm_mismatch_streak[expected] = 0
        _overlay_vlm_failopen_until[expected] = 0.0
        return True
    if not ui_confident:
        _overlay_vlm_mismatch_streak[expected] = 0
        return False
    streak = int(_overlay_vlm_mismatch_streak.get(expected, 0)) + 1
    _overlay_vlm_mismatch_streak[expected] = streak
    if streak >= _overlay_vlm_failopen_after():
        if not allow_failopen:
            return False
        hold_sec = _overlay_vlm_failopen_sec()
        _overlay_vlm_failopen_until[expected] = now + hold_sec
        logger.warning(
            "[overlay] [StrategyShift] VLM 覆核连续不匹配 expected=%s got=%s streak=%d，"
            "进入 UI 强证据降级 %.1fs",
            expected,
            _last_overlay_vlm_type,
            streak,
            hold_sec,
        )
        return True
    return False


def _is_settlement_overlay_strict_for_gate(bgr: np.ndarray) -> bool:
    """
    用于“阻断决斗分支”的严格结算判定：
    - 强按钮阈值（continue/details）
    - 最小计时圈证据
    - 可选 CONTINUE 边框高亮
    """
    from tongits_ui_probe import continue_button_has_highlight_border, probe_round_settlement_stats

    stats = probe_round_settlement_stats(bgr)
    c_strong = float(os.environ.get("TONGITS_SETTLEMENT_CONTINUE_RATIO_STRONG") or "0.11")
    d_strong = float(os.environ.get("TONGITS_SETTLEMENT_DETAILS_RATIO_STRONG") or "0.11")
    timer_floor = float(os.environ.get("TONGITS_SETTLEMENT_TIMER_RATIO_FLOOR") or "0.008")
    base_ok = (
        stats["continue_ratio"] >= c_strong
        and stats["details_ratio"] >= d_strong
        and stats["timer_ratio"] >= timer_floor
    )
    if not base_ok:
        return False
    if not _settlement_block_fight_require_border():
        return True
    return continue_button_has_highlight_border(bgr, log_details=False)


def _is_duel_overlay_strict_for_gate(bgr: np.ndarray) -> bool:
    from tongits_ui_probe import probe_fight_offer_stats

    stats = probe_fight_offer_stats(bgr)
    c_strict = _fight_detect_challenge_ratio_min_strict()
    f_strict = _fight_detect_fold_ratio_min_strict()
    return (
        float(stats.get("challenge_ratio") or 0.0) >= c_strict
        and float(stats.get("fold_ratio") or 0.0) >= f_strict
    )


def _read_my_delta_from_frame(bgr: np.ndarray) -> tuple[int | None, str]:
    """
    从一帧结算图里读“我方本局增减”（左下座位旁的带符号数字 +1500 / -500）。
    顺序：本地 OCR（带符号，最快）→ VLM 读单个带符号数字。
    返回 (signed_delta | None, source_note)。不做任何状态写入。
    """
    from vision_proxy_qwen import analyze_signed_delta_with_qwen

    # 诊断：把“被判定为结算”的整帧也存一份，便于核对检测是否误判 / ROI 是否对准。
    _save_coin_crop("settlement_full", bgr)
    dx, dy, dw, dh = _my_settlement_delta_roi_xywh(bgr)
    delta_xyxy = _xywh_to_xyxy((dx, dy, dw, dh))
    delta_crop, _ = _crop_frame_roi(bgr, delta_xyxy)
    _save_coin_crop("my_delta_roi", delta_crop)
    if delta_crop.size == 0:
        return None, "empty_roi"

    ocr_my_delta = _local_ocr_signed_delta_from_panel(delta_crop, tight=True)
    if ocr_my_delta is not None and abs(ocr_my_delta) >= 1:
        return int(ocr_my_delta), "my_delta_ocr"

    # 便宜的内容预检：增减数字是大号亮黄(赢)/亮红(输)字。
    # 若 ROI 内几乎没有亮黄/亮红/亮白像素（=纯蓝桌，无数字），直接返回，
    # 避免在结算误判帧上每帧空跑 ~3s VLM。
    try:
        hsv = cv2.cvtColor(delta_crop, cv2.COLOR_BGR2HSV)
        yellow = cv2.inRange(hsv, (15, 110, 140), (45, 255, 255))
        red1 = cv2.inRange(hsv, (0, 110, 120), (10, 255, 255))
        red2 = cv2.inRange(hsv, (160, 110, 120), (180, 255, 255))
        white = cv2.inRange(hsv, (0, 0, 205), (180, 45, 255))
        num_mask = cv2.bitwise_or(cv2.bitwise_or(yellow, white), cv2.bitwise_or(red1, red2))
        num_ratio = float(np.count_nonzero(num_mask)) / max(1, num_mask.size)
    except cv2.error:
        num_ratio = 1.0
    num_floor = float(os.environ.get("TONGITS_MY_DELTA_NUM_RATIO_MIN") or "0.02")
    if num_ratio < num_floor:
        logger.info(
            "[settlement] 增减ROI无数字内容(ratio=%.3f<%.3f)，跳过VLM（多半是误判帧/非结算）",
            num_ratio,
            num_floor,
        )
        return None, "no_number"

    tmp_delta = _save_vlm_crop_jpeg(delta_crop)
    try:
        vlm_my_delta = analyze_signed_delta_with_qwen(
            tmp_delta,
            model=_qwen_vlm_model(),
            timeout_sec=_settlement_coin_vlm_timeout_sec(),
        )
    finally:
        try:
            os.unlink(tmp_delta)
        except OSError:
            pass
    if vlm_my_delta is not None and abs(vlm_my_delta) >= 1:
        return int(vlm_my_delta), "my_delta_vlm"
    return None, "unreadable"


def _commit_my_settlement_delta(my_delta: int, *, note: str, now: float | None = None) -> None:
    """
    记录“我方本局增减”并复位结算相关状态（每个结算页只应调用一次）。
    """
    global _last_my_coin_amount, _settlement_coin_overlay_latched
    ts = time.perf_counter() if now is None else now
    if _last_my_coin_amount is not None:
        _last_my_coin_amount += float(my_delta)
    logger.info("[settlement] 本局金币变化：我方%+d (%s)", my_delta, note)
    _append_settlement_round_log_line(my_delta=my_delta, opponents=[], note=note)
    # 只置 latch 防止同一结算页重复记账；不重置门闸（否则 overlay_seen 被清后下一帧
    # 又会把 latch 解锁，导致每帧重复记）。门闸在下一回合开始时统一重置。
    _settlement_coin_overlay_latched = True
    _set_pending_settlement(False, reason=note, now=ts)
    _clear_settlement_lock(reason=note)


def _try_read_my_delta_on_strong_settlement(bgr: np.ndarray, *, now: float) -> bool:
    """
    结算 UI 强证据成立时的“即时读金币”快路径：
    绕开 dump/duel/continue 门闸与多帧确认，单帧直接读“我方增减”并记录一次。
    结算页通常只显示数秒（带自动倒计时），这条路确保不会因门闸没满足而漏记。
    """
    global _settlement_coin_overlay_latched
    if _settlement_coin_overlay_latched:
        return False
    delta, note = _read_my_delta_from_frame(bgr)
    if delta is None:
        return False
    _commit_my_settlement_delta(int(delta), note=note, now=now)
    return True


def _try_log_settlement_coin_delta(scout: TurnScout, *, assume_after_duel: bool = False) -> bool:
    """
    结算金币记录。API 模式（默认）：仅同步 bridge/CDP 的 3016 → settlement.log，不截屏。
    视觉模式（TONGITS_SETTLEMENT_VISUAL=1）：旧 OCR/VLM 路径。
    """
    global _last_settlement_coin_probe_at, _settlement_coin_overlay_latched
    global _last_settlement_coin_seen_at
    global _last_my_coin_amount, _last_my_coin_text
    global _last_proto_coin_delta_at_seen
    global _pending_settlement_after_duel, _pending_settlement_since
    global _last_settlement_locked_frame, _last_settlement_locked_at

    if _settlement_api_only():
        _sync_api_settlement_from_proto()
        if _settlement_coin_overlay_latched:
            return True
        file_obj = _load_proto_status_file()
        # 3016/3021 刚到达时短暂占用 waiting，避免决斗分支抢跑（无需视觉 overlay）
        if _proto_signal_recent(file_obj, "settlement") or _proto_signal_recent(file_obj, "coin"):
            return True
        if assume_after_duel and _pending_settlement_after_duel:
            return True
        return False

    if not _settlement_coin_probe_armed:
        return False
    if _capture_busy.locked():
        return False
    now = time.perf_counter()
    # 若刚确认处于结算页，即使金币识别轮询冷却中，也要拦截决斗分支，防止状态抖动。
    if now - _last_settlement_coin_seen_at < 3.2:
        return True
    lock_valid = (
        _last_settlement_locked_frame is not None
        and (now - _last_settlement_locked_at) <= _settlement_lock_hold_sec()
    )
    if (not lock_valid) and (now - _last_settlement_coin_probe_at < _settlement_coin_probe_poll_sec()):
        return False
    _last_settlement_coin_probe_at = now

    if lock_valid:
        bgr = _last_settlement_locked_frame.copy()
    else:
        bgr, _capture_backend = _grab_turn_frame(scout)
        if bgr is None:
            return False
    if is_my_turn_on_frame(bgr):
        _settlement_coin_overlay_latched = False
        _clear_settlement_lock(reason="my_turn_resumed")
        return False

    from tongits_ui_probe import probe_fight_offer_stats, is_round_settlement_overlay

    active = is_round_settlement_overlay(bgr, log_inactive=False)
    if not active and assume_after_duel:
        # 业务假设：决斗结束后下一轮进入结算。这里使用静默判定，避免在待结算阶段反复刷“决斗中”日志。
        fight_stats = probe_fight_offer_stats(bgr)
        c_thr = _fight_detect_challenge_ratio_min_strict()
        f_thr = _fight_detect_fold_ratio_min_strict()
        duel_like = (
            float(fight_stats.get("challenge_ratio") or 0.0) >= c_thr
            and float(fight_stats.get("fold_ratio") or 0.0) >= f_thr
        )
        if not duel_like:
            active = True
    if not active:
        _settlement_coin_overlay_latched = False
        _clear_settlement_lock(reason="overlay_not_settlement")
        return False
    if not lock_valid:
        _last_settlement_locked_frame = bgr.copy()
        _last_settlement_locked_at = now
        logger.info("[settlement] 命中即锁帧：hold=%.1fs", _settlement_lock_hold_sec())
    else:
        _last_settlement_locked_at = now
    _last_settlement_coin_seen_at = now
    if _settlement_coin_overlay_latched:
        return True

    # 视觉模式 + CDP 双开时的兜底（API-only 默认不走此分支）
    if _coin_use_cdp_enabled() and not _settlement_api_only():
        wait_sec = _cdp_settlement_fallback_sec()
        since_seen = (now - _last_settlement_coin_seen_at) if _last_settlement_coin_seen_at > 0 else 0.0
        if not (active and since_seen >= wait_sec):
            return True
        logger.warning(
            "[settlement] CDP 在 overlay 后 %.1fs 内未记账，降级视觉读金币",
            since_seen,
        )

    def _record_my_delta(*, my_delta: int, note: str) -> bool:
        _commit_my_settlement_delta(int(my_delta), note=note, now=now)
        return True

    # 优先级1（可选）：协议主判（proto bridge coin_delta）。
    if _coin_use_proto_enabled():
        proto_obj = _load_proto_status_file()
        proto_delta, proto_delta_at = _proto_coin_delta_from_status(proto_obj)
        proto_recent = _proto_signal_recent(proto_obj, "coin") or _proto_signal_recent(proto_obj, "settlement")
        if proto_recent and proto_delta is not None:
            stamp = proto_delta_at or str(proto_obj.get("updated_at") or "")
            if stamp and stamp != _last_proto_coin_delta_at_seen:
                _last_proto_coin_delta_at_seen = stamp
                return _record_my_delta(my_delta=int(proto_delta), note="proto_primary")

    # 优先级2（视觉主判）：直接读“我方座位旁的本局增减数字”（左下角 +1500 / -500）。
    # 这是本人专属的带符号结果，不会读到对手数值，比整桌 OCR/余额差分可靠得多。
    my_delta_val, my_delta_note = _read_my_delta_from_frame(bgr)
    if my_delta_val is not None:
        logger.info("[settlement] 我方增减: %+d (%s)", my_delta_val, my_delta_note)
        return _record_my_delta(my_delta=int(my_delta_val), note=my_delta_note)

    # 读不到“座位带符号增减”就不记任何东西：
    # 绝不再用“余额差分/整桌 OCR”兜底——那会在误判帧把没变化记成错误的 +0/持平，污染数据。
    # 等下一帧重试（latch 仍为 False），或结算页消失后自然复位。
    logger.info("[settlement] 未读到我方带符号增减，本帧不记账（不使用余额差分兜底）")
    return True


def _estimate_fight_overlay_point_legacy_cloud_only(bgr: np.ndarray) -> int | None:
    """兼容占位（避免旧调用），现统一走 _estimate_fight_overlay_point。"""
    from tongits_ui_probe import duel_point_roi_xywh
    from vision_proxy_qwen import analyze_duel_point_with_qwen

    roi_xyxy = _xywh_to_xyxy(duel_point_roi_xywh(bgr.shape))
    crop, _ = _crop_frame_roi(bgr, roi_xyxy)
    h, w = crop.shape[:2] if crop.size != 0 else (0, 0)
    if w < 12 or h < 12:
        logger.warning("[fight] 兼容路径跳过决斗POINT云端识别：ROI过小 %dx%d", w, h)
        return None
    if crop.size == 0:
        return None
    tmp_path = _save_vlm_crop_jpeg(crop)
    try:
        return analyze_duel_point_with_qwen(
            tmp_path,
            model=_qwen_vlm_model(),
            timeout_sec=_fight_overlay_point_timeout_sec(),
        )
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def _try_handle_fight_offer_overlay(scout: TurnScout) -> bool:
    """
    非我方回合：若出现 CHALLENGE/FOLD 决斗弹窗，按散牌点自动应战或认输。
    散牌点低（<=阈值）点 CHALLENGE；否则点 FOLD。
    """
    global _last_fight_offer_click_at, _last_fight_offer_probe_at
    global _last_fight_offer_action, _last_fight_offer_action_at
    global _pending_settlement_after_duel, _pending_settlement_since
    global _settlement_block_fight_streak
    global _settlement_candidate_until
    if not _auto_play_enabled() or not _auto_fight_defense_enabled():
        return False
    if _capture_busy.locked():
        return False
    if _in_startup_grace():
        return False
    now = time.perf_counter()
    if now - _last_fight_offer_probe_at < _fight_offer_poll_sec():
        return False
    _last_fight_offer_probe_at = now
    if now - _last_fight_offer_click_at < _fight_offer_click_cooldown_sec():
        return False
    if now < _settlement_candidate_until:
        logger.info(
            "[fight] 结算候选窗口内，暂停决斗处理 remain=%.1fs",
            _settlement_candidate_until - now,
        )
        return False
    if _last_settlement_seen_at > 0 and (now - _last_settlement_seen_at) < _fight_skip_after_settlement_sec():
        return False

    bgr, capture_backend = _grab_turn_frame(scout)
    if bgr is None:
        return False
    if is_my_turn_on_frame(bgr):
        return False

    from tongits_ui_probe import (
        challenge_offer_click_xy,
        fold_offer_click_xy,
        probe_fight_offer_stats,
    )
    from tongits_rule_bot import physical_click_xy
    proto_obj = _load_proto_status_file()
    proto_duel_recent = _proto_signal_recent(proto_obj, "duel")
    proto_settlement_recent = _proto_signal_recent(proto_obj, "settlement")

    # 结算页硬拦截（严格版+连续确认）：在等待态不得执行决斗点击。
    settlement_gate_strict = _is_settlement_overlay_strict_for_gate(bgr)
    duel_gate_strict = _is_duel_overlay_strict_for_gate(bgr)
    if settlement_gate_strict:
        _settlement_block_fight_streak += 1
        need = _settlement_block_fight_confirm_frames()
        if _settlement_block_fight_streak >= need:
            _mark_settlement_overlay_seen(reason="ui_settlement_block_fight")
            _set_pending_settlement(True, reason="ui_settlement_block_fight", now=now)
            return False
        if duel_gate_strict:
            logger.info("[overlay] 冲突帧：duel/settlement 同时命中，优先结算确认")
        logger.info(
            "[settlement_gate] strict_confirm=%d/%d，暂不切换 pending_settlement",
            _settlement_block_fight_streak,
            need,
        )
        return False
    else:
        _settlement_block_fight_streak = 0
    # 协议主判：若最近明确是 settlement，且最近没有 duel 信号，则阻断决斗分支。
    if proto_settlement_recent and not proto_duel_recent:
        _set_pending_settlement(True, reason="proto_settlement_block_fight", now=now)
        logger.info(
            "[fight] 协议主判：recent settlement=%s，跳过决斗应答",
            str(proto_obj.get("settlement", "-")),
        )
        return False

    stats = probe_fight_offer_stats(bgr)
    c_strict = _fight_detect_challenge_ratio_min_strict()
    f_strict = _fight_detect_fold_ratio_min_strict()
    if stats["challenge_ratio"] < c_strict or stats["fold_ratio"] < f_strict:
        return False
    raw_fight_ultra_confident = (
        stats["challenge_ratio"] >= _fight_ultra_conf_challenge_ratio_min()
        and stats["fold_ratio"] >= _fight_ultra_conf_fold_ratio_min()
    )
    relax_allowed = (now >= _settlement_candidate_until) and (not _pending_settlement_after_duel)
    fight_ultra_confident = raw_fight_ultra_confident and relax_allowed
    if raw_fight_ultra_confident and (not relax_allowed):
        logger.info("[fight] 结算候选/待结算窗口内，禁用超高置信放宽")
    if (not proto_duel_recent) and _fight_require_point_evidence():
        if not fight_ultra_confident:
            ink_ratio = _fight_point_text_ratio(bgr)
            if ink_ratio < _fight_point_ink_ratio_min():
                logger.info(
                    "[fight] 跳过：决斗 POINT 证据不足 ink=%.3f need>=%.3f",
                    ink_ratio,
                    _fight_point_ink_ratio_min(),
                )
                return False
        else:
            logger.info(
                "[fight] 超高置信按钮证据(c=%.3f f=%.3f)，放宽 POINT 证据门",
                stats["challenge_ratio"],
                stats["fold_ratio"],
            )
    c_strong = float(os.environ.get("TONGITS_FIGHT_OFFER_CHALLENGE_RATIO_STRONG") or "0.09")
    f_strong = float(os.environ.get("TONGITS_FIGHT_OFFER_FOLD_RATIO_STRONG") or "0.09")
    fight_ui_confident = stats["challenge_ratio"] >= c_strong and stats["fold_ratio"] >= f_strong
    duel_confirmed = False
    pending_reason = "duel_confirmed"
    if proto_duel_recent:
        duel_confirmed = True
        pending_reason = "duel_confirmed_proto"
        logger.info(
            "[fight] 协议主判：recent duel=%s，跳过 VLM 覆核",
            str(proto_obj.get("duel", "-")),
        )
    else:
        if not fight_ultra_confident:
            if not _overlay_type_matches_with_vlm(
                bgr,
                expected="duel",
                ui_confident=fight_ui_confident,
                allow_failopen=False,
            ):
                # 仅当“本地结算探针也命中”时，才从决斗分支切到待结算；
                # 避免 VLM 偶发把 duel 误回成 settlement，导致错过决斗应答。
                if _last_overlay_vlm_type == "settlement" and _is_settlement_overlay_strict_for_gate(bgr):
                    _set_pending_settlement(True, reason="overlay_vlm_settlement+ui", now=now)
                    logger.info("[fight] 覆核为 settlement（UI同意），切换待结算态并暂停决斗应答")
                logger.info("[fight] 跳过：VLM 覆核非决斗弹窗")
                return False
        else:
            logger.info(
                "[fight] 超高置信按钮证据(c=%.3f f=%.3f)，放宽 VLM 覆核门",
                stats["challenge_ratio"],
                stats["fold_ratio"],
            )
        duel_confirmed = True
    if duel_confirmed:
        _set_pending_settlement(True, reason=pending_reason, now=now)

    # 应战决斗固定使用“上一轮缓存散牌点”，不读取当前页面 POINT。
    if _last_known_hand_scatter is None:
        default_action = _fight_default_action_no_cache()
        logger.info(
            "[fight] 无上轮散牌点缓存，使用默认应答：%s",
            default_action,
        )
        action = default_action
        decision_metric = "fallback_scatter=none"
        decision_source = "no_cache_default"
    else:
        threshold = _fight_defense_scatter_max()
        action = "challenge" if _last_known_hand_scatter <= threshold else "fold"
        decision_metric = f"fallback_scatter={_last_known_hand_scatter}"
        decision_source = "last_turn_scatter_only"
        logger.info(
            "[fight] 使用上轮散牌点决斗：scatter=%d thr=%d → %s",
            _last_known_hand_scatter,
            threshold,
            action,
        )
    threshold = _fight_defense_scatter_max()
    cx, cy = (
        challenge_offer_click_xy(bgr)
        if action == "challenge"
        else fold_offer_click_xy(bgr)
    )
    sh, sw = bgr.shape[:2]
    dry_run = _auto_play_dry_run_enabled()
    logger.info(
        "[fight] 决斗应答: action=%s %s thr=%d source=%s yolo=%s cache=%s "
        "(%s %dx%d c=%.3f f=%.3f)",
        action,
        decision_metric,
        threshold,
        decision_source,
        "-",
        "-",
        capture_backend,
        sw,
        sh,
        stats["challenge_ratio"],
        stats["fold_ratio"],
    )
    if (
        _last_fight_offer_action == action
        and (now - _last_fight_offer_action_at) < _fight_offer_repeat_action_cooldown_sec()
    ):
        logger.info(
            "[fight] 跳过重复应答: action=%s cooldown=%.1fs",
            action,
            _fight_offer_repeat_action_cooldown_sec(),
        )
        return True
    res = physical_click_xy(
        cx,
        cy,
        skip_real=dry_run,
        label=f"fight_{action}",
        screen_width=sw,
        screen_height=sh,
    )
    if bool(res.get("ok")):
        _last_fight_offer_click_at = now
        _last_fight_offer_action = action
        _last_fight_offer_action_at = now
        _mark_settlement_duel_seen(reason="passive_duel_response_clicked")
        return True
    logger.warning("[fight] 决斗应答点击失败: %s", res.get("error"))
    return False


def _try_handle_round_settlement_overlay(scout: TurnScout) -> bool:
    """
    非我方回合：识别结算页（WIN/LOSE/DEFEAT）→ 即时记账 + 抑制误判决斗。
    已不再点击 CONTINUE：记账不依赖确认按钮，结算页由游戏自带倒计时自动翻页。
    API 模式（默认）已禁用：结算仅走 3016 协议，不截屏/VLM。
    """
    if _settlement_api_only():
        return False
    global _last_settlement_probe_at
    global _last_settlement_seen_at
    global _settlement_confirm_streak, _settlement_not_seen_streak
    global _settlement_overlay_latched, _settlement_clicks_this_overlay, _settlement_overlay_started_at
    global _settlement_ui_strong_streak
    global _settlement_vlm_miss_streak
    global _settlement_overlay_first_seen_at
    global _settlement_candidate_until
    global _settlement_retry_once_done
    global _pending_settlement_after_duel, _settlement_duel_seen
    if not _auto_play_enabled():
        return False
    if _capture_busy.locked():
        return False
    if _in_startup_grace():
        return False
    now = time.perf_counter()
    if now - _last_settlement_probe_at < _settlement_poll_sec():
        return False
    _last_settlement_probe_at = now

    bgr, _capture_backend = _grab_turn_frame(scout)
    if bgr is None:
        return False
    if is_my_turn_on_frame(bgr):
        return False

    from tongits_ui_probe import (
        continue_button_has_highlight_border,
        is_round_settlement_overlay,
        probe_round_settlement_stats,
        normal_action_bar_present,
    )

    # 诊断（破死循环）：非我方回合且“正常四色动作栏不在场”时，存一张完整帧。
    # 正常对手回合有动作栏→跳过；真结算页无动作栏→会被存下，用于校准真实按钮/+ROI 位置。
    global _last_nonbar_diag_at
    if not normal_action_bar_present(bgr):
        if (now - _last_nonbar_diag_at) >= _nonbar_diag_interval_sec():
            _last_nonbar_diag_at = now
            _save_coin_crop("nonbar_full", bgr)
            logger.info("[settlement] 诊断：动作栏不在场，已存非常规全屏（疑似结算/过场）")

    active_overlay = is_round_settlement_overlay(bgr, log_inactive=False)
    if not active_overlay:
        # 决斗应答后兜底：允许用更宽松的 UI 条件识别结算页，避免卡在 pending 但不进结算分支。
        if _pending_settlement_after_duel and _settlement_duel_seen:
            post_stats = probe_round_settlement_stats(bgr)
            post_border_ok = continue_button_has_highlight_border(bgr, log_details=False)
            post_duel_settlement_like = (
                post_border_ok
                and post_stats["continue_ratio"] >= _settlement_post_duel_continue_ratio_min()
                and post_stats["details_ratio"] >= _settlement_post_duel_details_ratio_min()
                and post_stats["timer_ratio"] >= _settlement_post_duel_timer_ratio_min()
            )
            if post_duel_settlement_like:
                active_overlay = True
                logger.info(
                    "[settlement] 决斗后兜底命中：continue=%.3f details=%.3f timer=%.3f",
                    post_stats["continue_ratio"],
                    post_stats["details_ratio"],
                    post_stats["timer_ratio"],
                )
    if not active_overlay:
        _settlement_not_seen_streak += 1
        _settlement_confirm_streak = 0
        _settlement_ui_strong_streak = 0
        _settlement_vlm_miss_streak = 0
        _settlement_overlay_first_seen_at = 0.0
        _settlement_retry_once_done = False
        if _settlement_not_seen_streak >= _settlement_release_frames():
            _settlement_overlay_latched = False
            _settlement_clicks_this_overlay = 0
            _settlement_overlay_started_at = 0.0
        return False
    _settlement_candidate_until = max(
        _settlement_candidate_until,
        now + _settlement_conflict_grace_sec(),
    )
    _mark_settlement_overlay_seen(reason="round_settlement_overlay")
    # 诊断：只要检测判定为结算（无论强弱/真假），立即存一份完整帧，
    # 与后续 VLM 覆核/确认帧/记账门闸完全无关，确保总能拿到“机器人以为是结算”的整帧。
    if _settlement_overlay_first_seen_at <= 0.0:
        _save_coin_crop("settlement_full", bgr)
        _settlement_overlay_first_seen_at = now
    stats = probe_round_settlement_stats(bgr)
    c_strong = float(os.environ.get("TONGITS_SETTLEMENT_CONTINUE_RATIO_STRONG") or "0.11")
    d_strong = float(os.environ.get("TONGITS_SETTLEMENT_DETAILS_RATIO_STRONG") or "0.11")
    timer_floor = float(os.environ.get("TONGITS_SETTLEMENT_TIMER_RATIO_FLOOR") or "0.008")
    settlement_ui_confident = (
        stats["continue_ratio"] >= c_strong
        and stats["details_ratio"] >= d_strong
        and stats["timer_ratio"] >= timer_floor
    )
    border_ok = continue_button_has_highlight_border(bgr, log_details=False)
    if settlement_ui_confident and border_ok:
        _settlement_ui_strong_streak += 1
    else:
        _settlement_ui_strong_streak = 0
    # 即时读金币快路径：结算页通常只显示数秒，强证据一旦成立就立刻读“我方增减”，
    # 绕开 dump/duel/continue 门闸与多帧确认，避免门闸没满足就被自动翻页漏记。
    if settlement_ui_confident and not _settlement_coin_overlay_latched:
        if _try_read_my_delta_on_strong_settlement(bgr, now=now):
            logger.info("[settlement] 强证据即时记账成功（单帧，无需门闸）")
    _refresh_settlement_probe_arm(reason="settlement_ui_strong_probe")
    duel_conflict_like = _is_duel_overlay_strict_for_gate(bgr)
    if duel_conflict_like and (not settlement_ui_confident) and now >= _settlement_candidate_until:
        if _pending_settlement_after_duel:
            _set_pending_settlement(False, reason="overlay_conflict_duel_priority", now=now)
        logger.info("[settlement] 跳过：overlay 冲突，duel 证据优先")
        return False
    skip_vlm = _settlement_skip_vlm_on_strong_ui() and settlement_ui_confident and border_ok
    if skip_vlm:
        vlm_matched = True
        logger.info("[settlement] UI 强证据已成立，跳过 VLM 覆核")
    else:
        vlm_matched = _overlay_type_matches_with_vlm(
            bgr,
            expected="settlement",
            ui_confident=settlement_ui_confident,
            allow_failopen=_settlement_allow_vlm_failopen(),
        )
    ui_strong_ready = _settlement_ui_strong_streak >= _settlement_ui_strong_frames()
    if not vlm_matched:
        _settlement_vlm_miss_streak += 1
        miss_max = _settlement_release_on_vlm_miss_streak()
        if _pending_settlement_after_duel and _settlement_vlm_miss_streak >= miss_max:
            _set_pending_settlement(False, reason="settlement_vlm_miss_release", now=now)
            logger.info(
                "[settlement] 连续覆核失败 %d 次，释放 pending_settlement",
                _settlement_vlm_miss_streak,
            )
            _settlement_vlm_miss_streak = 0
        if ui_strong_ready and border_ok:
            logger.info(
                "[settlement] VLM 失配，但 UI 强证据连续 %d 帧，允许无VLM继续",
                _settlement_ui_strong_streak,
            )
        else:
            if not _settlement_retry_once_done:
                _settlement_retry_once_done = True
                _last_settlement_probe_at = 0.0
                logger.info("[settlement] 首轮处理失败，立即补抓一帧重试")
                return _try_handle_round_settlement_overlay(scout)
            logger.info("[settlement] 跳过：VLM 覆核非结算弹窗")
            return False
    _settlement_vlm_miss_streak = 0
    _settlement_not_seen_streak = 0
    _settlement_confirm_streak += 1
    if _settlement_confirm_streak < _settlement_confirm_frames():
        logger.info(
            "[settlement] 等待确认帧: %d/%d",
            _settlement_confirm_streak,
            _settlement_confirm_frames(),
        )
        return True
    if not _settlement_overlay_latched:
        _settlement_overlay_latched = True
        _settlement_clicks_this_overlay = 0
        _settlement_overlay_started_at = now
    _last_settlement_seen_at = now
    # 不再点击 CONTINUE：仅确认结算态。即时记账已在上方“强证据”处完成；
    # 若仍未记账（latch 未置），这里再补一次单帧记账。结算页由游戏自带倒计时自动翻页。
    if not _settlement_coin_overlay_latched:
        if _try_read_my_delta_on_strong_settlement(bgr, now=now):
            logger.info("[settlement] 确认帧后补记账成功（无点击）")
    return True


def _win_button_center_xy(frame_shape: tuple[int, int]) -> tuple[int, int]:
    sh, sw = frame_shape
    try:
        xr = float(os.environ.get("TONGITS_WIN_BUTTON_X_RATIO", "0.50"))
        yr = float(os.environ.get("TONGITS_WIN_BUTTON_Y_RATIO", "0.50"))
    except ValueError:
        xr, yr = 0.50, 0.50
    cx = int(round(sw * min(0.95, max(0.05, xr))))
    cy = int(round(sh * min(0.95, max(0.05, yr))))
    return cx, cy


def _try_click_center_win_button(bgr: np.ndarray) -> bool:
    """
    结算态点击中央 WIN 按钮（一次性/冷却保护）。
    """
    global _last_win_click_at
    if not _auto_click_win_enabled():
        return False
    if bgr is None or bgr.size == 0:
        return False

    now = time.perf_counter()
    if now - _last_win_click_at < _win_click_cooldown_sec():
        return False

    sh, sw = bgr.shape[:2]
    cx, cy = _win_button_center_xy((sh, sw))
    half_w = max(40, int(round(sw * 0.07)))
    half_h = max(28, int(round(sh * 0.06)))
    x1, x2 = max(0, cx - half_w), min(sw, cx + half_w)
    y1, y2 = max(0, cy - half_h), min(sh, cy + half_h)
    roi = bgr[y1:y2, x1:x2]
    if roi.size == 0:
        return False

    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    yellow = cv2.inRange(hsv, (16, 95, 120), (42, 255, 255))
    yellow_ratio = cv2.countNonZero(yellow) / max(1, yellow.shape[0] * yellow.shape[1])
    if yellow_ratio < 0.12:
        return False

    try:
        from tongits_rule_bot import physical_click_xy

        res = physical_click_xy(
            cx,
            cy,
            skip_real=False,
            label="WIN结算按钮",
            screen_width=sw,
            screen_height=sh,
        )
        if bool(res.get("ok")):
            _last_win_click_at = now
            logger.info(
                "[WIN] 已点击中央 WIN 按钮 @(%d,%d) yellow=%.3f",
                cx,
                cy,
                yellow_ratio,
            )
            return True
        logger.warning("[WIN] 点击中央 WIN 按钮失败: %s", res.get("error"))
    except Exception as e:
        logger.warning("[WIN] 点击中央 WIN 按钮异常: %s", e)
    return False


def _turn_log(msg: str) -> None:
    print(f"{datetime.now().strftime('%H:%M:%S')} {msg}", flush=True)


def _startup_grace_sec() -> float:
    try:
        return float(os.environ.get("TONGITS_STARTUP_GRACE_SEC", str(STARTUP_GRACE_SEC)))
    except ValueError:
        return STARTUP_GRACE_SEC


def _capture_retry_count() -> int:
    try:
        return max(0, int(os.environ.get("TONGITS_CAPTURE_RETRY_COUNT", str(CAPTURE_RETRY_COUNT))))
    except ValueError:
        return CAPTURE_RETRY_COUNT


def _capture_retry_delay_sec() -> float:
    try:
        return float(
            os.environ.get("TONGITS_CAPTURE_RETRY_DELAY_SEC", str(CAPTURE_RETRY_DELAY_SEC))
        )
    except ValueError:
        return CAPTURE_RETRY_DELAY_SEC


def _in_startup_grace() -> bool:
    grace = _startup_grace_sec()
    if grace <= 0 or _loop_started_at <= 0:
        return False
    return (time.perf_counter() - _loop_started_at) < grace


def _capture_warmup_enabled() -> bool:
    return (os.environ.get("TONGITS_CAPTURE_WARMUP") or "1").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def _looks_like_game_table(bgr: np.ndarray) -> tuple[bool, float]:
    """
    中央区域是否像 Tongits 牌桌（蓝/青绿色毡面）。

    用于过滤「截到桌面壁纸 / PowerShell」的误帧。
    """
    sh, sw = bgr.shape[:2]
    if sh < 100 or sw < 100:
        return False, 0.0

    x1, y1 = int(sw * 0.22), int(sh * 0.12)
    x2, y2 = int(sw * 0.78), int(sh * 0.62)
    center = bgr[y1:y2, x1:x2]
    if center.size == 0:
        return False, 0.0

    hsv = cv2.cvtColor(center, cv2.COLOR_BGR2HSV)
    teal = cv2.inRange(hsv, (78, 25, 35), (105, 255, 255))
    blue = cv2.inRange(hsv, (95, 20, 30), (135, 255, 255))
    mask = cv2.bitwise_or(teal, blue)
    ratio = cv2.countNonZero(mask) / max(1, mask.size)
    min_ratio = float(
        os.environ.get("TONGITS_GAME_TABLE_BLUE_RATIO_MIN", str(GAME_TABLE_BLUE_RATIO_MIN))
    )
    return ratio >= min_ratio, ratio


def _grab_turn_frame(scout: TurnScout | None) -> tuple[np.ndarray | None, str]:
    """回合内截屏：mss → ImageGrab → pyautogui 链式回退。"""
    if scout is not None:
        try:
            frame = scout.capturer.grab()
            return frame, scout.capturer._last_backend
        except Exception as e:
            logger.warning("YOLO 截屏链失败，回退超时 pyautogui: %s", e)
    frame = _capture_screen_bgr()
    return frame, "pyautogui(timeout)" if frame is not None else "failed"


def _is_dealt_frame(bgr: np.ndarray) -> tuple[bool, dict[str, float]]:
    stats = probe_hand_cards_stats(bgr)
    ok = (
        stats["card_ratio"] >= HAND_CARD_RATIO_MIN
        and stats["edge_ratio"] >= HAND_EDGE_RATIO_MIN
    )
    return ok, stats


def _dealt_force_last_retry_enabled() -> bool:
    return (os.environ.get("TONGITS_DEALT_FORCE_LAST_RETRY") or "1").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _dealt_edge_margin() -> float:
    try:
        return max(0.0, float(os.environ.get("TONGITS_DEALT_EDGE_MARGIN", "0.004")))
    except ValueError:
        return 0.004


def _dealt_card_margin() -> float:
    try:
        return max(0.0, float(os.environ.get("TONGITS_DEALT_CARD_MARGIN", "0.004")))
    except ValueError:
        return 0.004


def _looks_like_dealt_near_miss(stats: dict[str, float]) -> bool:
    """
    发牌判定边界容错：
    card/edge 只要接近阈值（在 margin 内）且都不是极低值，允许末次重试时兜底继续侦察。
    """
    card = float(stats.get("card_ratio", 0.0))
    edge = float(stats.get("edge_ratio", 0.0))
    card_ok = card >= (HAND_CARD_RATIO_MIN - _dealt_card_margin())
    edge_ok = edge >= (HAND_EDGE_RATIO_MIN - _dealt_edge_margin())
    floor_ok = card >= max(0.01, HAND_CARD_RATIO_MIN * 0.6) and edge >= max(
        0.01, HAND_EDGE_RATIO_MIN * 0.6
    )
    return card_ok and edge_ok and floor_ok


def _auto_play_enabled() -> bool:
    from tongits_coord_executor import auto_play_enabled

    return auto_play_enabled()


def _run_coord_auto_play(
    scout: TurnScout,
    scout_result: TurnScoutResult,
    bgr: np.ndarray,
    *,
    turn_started_at: float | None = None,
) -> None:
    global _last_known_hand_scatter
    from tongits_coord_executor import auto_play_dry_run, execute_scout_coord_turn

    try:
        if not is_my_turn_on_frame(bgr):
            logger.info("[出牌] 绿圈已消失，跳过自动出牌")
            return
        result = execute_scout_coord_turn(
            scout,
            scout_result,
            bgr,
            grab_frame=lambda: _grab_turn_frame(scout),
            dry_run=auto_play_dry_run(),
            log_fn=logger.info,
            turn_started_at=turn_started_at,
        )
        if not result.get("ok"):
            if result.get("aborted"):
                logger.info("[出牌] 已中止（回合结束或绿圈消失）")
            else:
                logger.warning("[出牌] 自动出牌未完成: %s", result.get("error"))
        else:
            actions = [str(a) for a in (result.get("actions") or [])]
            if "dump" in actions:
                _mark_settlement_dump_seen(reason="turn_dump_completed")
            if "fight" in actions:
                _mark_settlement_duel_seen(reason="active_fight_clicked")
            # 回合执行成功后，用“本轮出牌后”可估计的散牌点刷新缓存，
            # 供后续决斗点数不可读时兜底。
            hand_labels = [str(x) for x in (result.get("hand") or []) if x]
            dump = result.get("dump") or {}
            target = dump.get("target") if isinstance(dump, dict) else None
            dumped_label = str(getattr(target, "label", "") or "")
            if hand_labels and dumped_label:
                try:
                    hand_labels.remove(dumped_label)
                except ValueError:
                    pass
            post_scatter = _scatter_from_hand_labels(hand_labels)
            if post_scatter is not None:
                _last_known_hand_scatter = post_scatter
                logger.info("[fight] 更新上轮散牌点缓存=%d（用于决斗兜底）", post_scatter)
    except Exception as e:
        logger.error("[出牌] 自动出牌失败: %s", e)


def _on_yolo_turn_worker(scout: TurnScout) -> None:
    """后台：延迟 → 截屏 → 校验已发牌 → YOLO 推理 → 战报 → 存 omnioutput 原图。"""
    global _pending_turn_scout, _last_known_hand_scatter
    if not _capture_busy.acquire(blocking=False):
        logger.info("上一回合侦察未完成，跳过")
        if is_my_turn():
            _pending_turn_scout = True
            logger.info("已登记回合补跑：当前侦察结束后自动再跑一轮")
        return
    _pending_turn_scout = False
    turn_started_at = time.perf_counter()
    try:
        if _in_startup_grace():
            logger.info(
                "启动预热中（%.0fs 内不截屏），请先切到游戏窗口",
                _startup_grace_sec(),
            )
            return

        if TURN_CAPTURE_DELAY_SEC > 0:
            logger.info("等待 %.1fs 后侦察 …", TURN_CAPTURE_DELAY_SEC)
            time.sleep(TURN_CAPTURE_DELAY_SEC)

        if not is_my_turn():
            logger.info("跳过：回合已结束（绿圈消失），不标记")
            return

        retries = _capture_retry_count()
        bgr: np.ndarray | None = None
        capture_backend = "failed"
        scout_result: TurnScoutResult | None = None

        for attempt in range(retries + 1):
            if attempt > 0:
                logger.info(
                    "截屏重试 %d/%d（等待 %.1fs）…",
                    attempt,
                    retries,
                    _capture_retry_delay_sec(),
                )
                time.sleep(_capture_retry_delay_sec())

            logger.info("正在截取全屏 …")
            bgr, capture_backend = _grab_turn_frame(scout)
            if bgr is None:
                continue

            if _is_round_end_win_screen(bgr):
                _log_win_skip_reason(bgr)
                _try_click_center_win_button(bgr)
                return

            if not is_my_turn_on_frame(bgr):
                logger.info("跳过：截屏时绿圈已消失，不标记")
                return

            table_ok, table_ratio = _looks_like_game_table(bgr)
            if not table_ok:
                sh, sw = bgr.shape[:2]
                logger.warning(
                    "截屏不像牌桌（蓝区比=%.3f，%dx%d %s），可能仍为桌面/终端",
                    table_ratio,
                    sw,
                    sh,
                    capture_backend,
                )
                continue

            dealt, stats = _is_dealt_frame(bgr)
            if not dealt:
                near_miss_force = (
                    _dealt_force_last_retry_enabled()
                    and attempt >= retries
                    and is_my_turn_on_frame(bgr)
                    and _looks_like_dealt_near_miss(stats)
                )
                force_scout = (
                    _scout_mode() == "florence_local"
                    and attempt >= retries
                    and is_my_turn_on_frame(bgr)
                )
                if force_scout or near_miss_force:
                    logger.warning(
                        "本帧未发牌（card=%.3f edge=%.3f），但仍在我的回合：%s 兜底继续侦察",
                        stats["card_ratio"],
                        stats["edge_ratio"],
                        "Florence" if force_scout else "边界容错",
                    )
                    scout_result = scout.infer_turn_frame(bgr)
                    break
                logger.info(
                    "本帧未发牌（card=%.3f edge=%.3f），重试",
                    stats["card_ratio"],
                    stats["edge_ratio"],
                )
                continue

            scout_result = scout.infer_turn_frame(bgr)
            if scout_result.raw_detection_count > 0:
                break

            sh, sw = bgr.shape[:2]
            logger.warning(
                "模型 0 检出（%dx%d %s），将重试",
                sw,
                sh,
                capture_backend,
            )

        if bgr is None or scout_result is None:
            logger.warning("本轮截屏失败：未获得有效牌局画面")
            return

        scatter = _hand_scatter_from_detections(scout_result.by_zone.get("player_hand", []))
        if scatter is not None:
            _last_known_hand_scatter = scatter

        _print_scout_report(
            scout_result,
            capture_backend=capture_backend,
            frame_shape=bgr.shape[:2],
        )
        if scout_result.raw_detection_count == 0:
            sh, sw = bgr.shape[:2]
            logger.warning(
                "未识别到牌：请保持游戏全屏且在最前（脚本不会自动切窗口）（%dx%d %s）",
                sw,
                sh,
                capture_backend,
            )
        elif not scout_result.all_detections:
            logger.warning(
                "模型检出 %d 张但未落入战区，请检查 ROI 或分辨率是否匹配",
                scout_result.raw_detection_count,
            )

        _save_turn_board_screenshot(bgr)

        if _auto_play_enabled() and scout_result.deck_valid:
            _run_coord_auto_play(
                scout, scout_result, bgr, turn_started_at=turn_started_at
            )
        elif _auto_play_enabled() and not scout_result.deck_valid:
            logger.warning("[出牌] 一副牌约束未通过，跳过自动出牌")
    except Exception as e:
        logger.error("回合侦察失败: %s", e)
    finally:
        _capture_busy.release()
        if _pending_turn_scout and is_my_turn():
            _pending_turn_scout = False
            logger.info("补跑回合侦察：仍在我的回合")
            threading.Thread(
                target=_on_yolo_turn_worker,
                args=(scout,),
                daemon=True,
            ).start()


def _on_save_only_turn_worker() -> None:
    """--save-only：仅保存截图，不做 YOLO。"""
    if not _capture_busy.acquire(blocking=False):
        _turn_log("[截图] 上一张截图未完成，跳过本回合")
        return
    try:
        if TURN_CAPTURE_DELAY_SEC > 0:
            _turn_log(f"[截图] 等待 {TURN_CAPTURE_DELAY_SEC:.1f}s 后截屏 …")
            time.sleep(TURN_CAPTURE_DELAY_SEC)

        _turn_log("[截图] 正在截取全屏 …")
        bgr = _capture_screen_bgr()
        if bgr is None:
            return

        dealt, stats = _is_dealt_frame(bgr)
        if not dealt:
            _turn_log(
                f"[截图] 跳过：未发牌（card={stats['card_ratio']:.3f} "
                f"edge={stats['edge_ratio']:.3f}）"
            )
            return

        OMNI_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        save_path = _board_save_path()
        cv2.imwrite(str(save_path), bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
        _turn_log(f"[截图] 已保存 → {save_path.resolve()}")
    except Exception as e:
        _turn_log(f"截图失败：{e}")
    finally:
        _capture_busy.release()


def on_yolo_turn_started(scout: TurnScout) -> None:
    """到我的回合：异步触发一次 YOLO 侦察。"""
    logger.info("到我的回合了")
    threading.Thread(
        target=_on_yolo_turn_worker,
        args=(scout,),
        daemon=True,
    ).start()


def on_save_only_turn_started() -> None:
    _turn_log("到我的回合了")
    threading.Thread(target=_on_save_only_turn_worker, daemon=True).start()


def on_turn_ended(*, use_logger: bool = False) -> None:
    global _pending_turn_scout
    _pending_turn_scout = False
    try:
        from tongits_turn_guard import abort_active_play_session

        gen = abort_active_play_session()
        msg = f"回合结束（abort 出牌 session→{gen}）"
    except Exception:
        msg = "回合结束"
    if use_logger:
        logger.info(msg)
    else:
        _turn_log(msg)


class TurnCyclePhase(str, Enum):
    WAITING = "waiting"
    MY_TURN = "my_turn"


@dataclass
class TurnCycleTracker:
    phase: TurnCyclePhase = TurnCyclePhase.WAITING
    enter_streak: int = 0
    exit_streak: int = 0
    bootstrapped: bool = False

    def update(self, active: bool) -> tuple[bool, bool]:
        if not self.bootstrapped:
            # 首帧仅同步状态，不触发「到我的回合」（避免启动瞬间误截桌面）
            self.bootstrapped = True
            self.phase = (
                TurnCyclePhase.MY_TURN if active else TurnCyclePhase.WAITING
            )
            self.enter_streak = 0
            self.exit_streak = 0
            return False, False

        started = ended = False

        if self.phase == TurnCyclePhase.WAITING:
            if active:
                self.enter_streak += 1
                if self.enter_streak >= TURN_ENTER_FRAMES:
                    self.phase = TurnCyclePhase.MY_TURN
                    self.enter_streak = 0
                    self.exit_streak = 0
                    started = True
            else:
                self.enter_streak = 0
        else:
            if not active:
                self.exit_streak += 1
                if self.exit_streak >= TURN_EXIT_FRAMES:
                    self.phase = TurnCyclePhase.WAITING
                    self.exit_streak = 0
                    self.enter_streak = 0
                    ended = True
            else:
                self.exit_streak = 0

        return started, ended


def _turn_poll_loop(
    *,
    on_started: Callable[[], None],
    on_ended: Callable[[], None] | None = None,
    on_waiting: Callable[[], None] | None = None,
) -> None:
    """绿圈轮询主循环：检测到「我的回合」上升沿时回调一次。"""
    load_turn_runtime_config()
    tracker = TurnCycleTracker()
    try:
        while True:
            try:
                active = is_my_turn()
                started, ended = tracker.update(active)
                if started:
                    on_started()
                if ended and on_ended:
                    on_ended()
                if (not active) and on_waiting:
                    on_waiting()
                time.sleep(POLL_INTERVAL_SEC)
            except KeyboardInterrupt:
                raise
            except Exception:
                time.sleep(max(POLL_INTERVAL_SEC, 0.5))
    except KeyboardInterrupt:
        pass


def yolo_turn_main_loop(
    *,
    weights_path: Path,
    conf: float,
    monitor_index: int,
) -> None:
    """
    默认挂机循环：绿圈探回合 → 轮到我时 YOLO 侦察一次。

    主线程仅轮询绿圈（~0.2s），YOLO 推理在后台线程执行，不阻塞下一回合检测。
    """
    global _loop_started_at

    _setup_scout_logging()
    load_turn_runtime_config()
    _loop_started_at = time.perf_counter()
    mode = _scout_mode()
    scout = _create_turn_scout(
        weights_path,
        conf=conf,
        monitor_index=monitor_index,
    )
    warmup_fn = getattr(scout, "warmup_model", None)
    if callable(warmup_fn):
        try:
            warmup_fn()
        except Exception as e:
            logger.warning("回合模型热身失败（可忽略）: %s", e)
    mode_labels = {
        "florence_local": "Florence OCR+HSV 五战区",
        "qwen_full": "手牌坐标(YOLO+VLM)+明牌/对手/弃牌VLM标签",
        "hybrid": "混合(YOLO裁区+对手Qwen并行)",
        "yolo_full": "全屏YOLO",
    }
    marked_hint = (
        f"标记图→{YOLO_MARKED_DIR}"
        if _yolo_save_marked_enabled() and mode not in ("qwen_full", "florence_local")
        else (
            "标记图→手牌YOLO"
            if mode == "qwen_full" and _yolo_save_marked_enabled()
            else "标记图=关"
        )
    )
    hard_hint = (
        f"影子特训→{HARD_EXAMPLES_DIR}"
        if _hard_examples_enabled()
        else "影子特训=关"
    )
    screenshot_hint = (
        f"回合截图→{OMNI_OUTPUT_DIR.resolve()}"
        if _turn_screenshot_save_enabled()
        else "回合截图=关"
    )
    melds_crop_hint = (
        f"明牌裁图→{MY_MELDS_CROP_DIR.resolve()}"
        if mode in ("qwen_full", "florence_local") and _my_melds_crop_save_enabled()
        else ""
    )
    startup_parts = [
        screenshot_hint,
        melds_crop_hint,
        marked_hint,
        hard_hint,
        (f"金币裁图→{COIN_CROPS_DIR.resolve()}" if _coin_crops_enabled() else "金币裁图=关"),
        ("结算=协议3016(bridge/CDP)" if _settlement_api_only() else "结算=视觉"),
    ]
    startup_detail = " | ".join(p for p in startup_parts if p)
    logger.info(
        "绿圈 + %s 回合侦察已启动 | %s | 预热 %.0fs | 按 Ctrl+C 终止",
        mode_labels.get(mode, mode),
        startup_detail,
        _startup_grace_sec(),
    )
    _init_coin_crops_dir()
    _log_proto_status(reason="startup", force=True)
    _start_proto_settlement_service_if_enabled()

    def _started() -> None:
        _set_pending_settlement(False, reason="my_turn_started")
        _reset_settlement_coin_gate(reason="my_turn_started")
        _clear_settlement_lock(reason="my_turn_started")
        _log_proto_status(reason="turn_started", force=True)
        on_yolo_turn_started(scout)

    def _waiting() -> None:
        global _pending_settlement_after_duel, _pending_settlement_since
        _log_proto_status(reason="waiting_tick")
        if _settlement_api_only():
            if _try_log_settlement_coin_delta(
                scout,
                assume_after_duel=_pending_settlement_after_duel,
            ):
                return
            _try_handle_fight_offer_overlay(scout)
            return
        # --- 以下仅 TONGITS_SETTLEMENT_VISUAL=1 ---
        handled_settlement_overlay = _try_handle_round_settlement_overlay(scout)
        if _try_log_settlement_coin_delta(
            scout,
            assume_after_duel=_pending_settlement_after_duel,
        ):
            return
        if handled_settlement_overlay:
            return
        if _pending_settlement_after_duel:
            now = time.perf_counter()
            if (now - _pending_settlement_since) > _settlement_after_duel_hold_sec():
                logger.info(
                    "[settlement] 决斗后待结算超时 %.1fs，解除强制结算态",
                    _settlement_after_duel_hold_sec(),
                )
                _set_pending_settlement(False, reason="pending_hold_timeout", now=now)
            else:
                if _try_log_settlement_coin_delta(scout, assume_after_duel=True):
                    return
                return
        if _try_log_settlement_coin_delta(scout):
            return
        _try_handle_fight_offer_overlay(scout)

    try:
        _turn_poll_loop(
            on_started=_started,
            on_ended=lambda: on_turn_ended(use_logger=True),
            on_waiting=_waiting,
        )
    except KeyboardInterrupt:
        logger.info("收到 Ctrl+C，主循环终止")
    finally:
        _stop_proto_settlement_service()
        scout.close()


def save_only_main_loop() -> None:
    """仅截图模式（--save-only）。"""
    _turn_poll_loop(on_started=on_save_only_turn_started, on_ended=on_turn_ended)


def turn_probe_once() -> int:
    load_turn_runtime_config()
    score = _green_border_score(_capture_avatar_bgr())
    active = score > GREEN_PIXEL_THRESHOLD
    bgr = _capture_screen_bgr()
    if bgr is None:
        print("capture=timeout", flush=True)
        return 1
    stats = probe_hand_cards_stats(bgr)
    dealt = (
        stats["card_ratio"] >= HAND_CARD_RATIO_MIN
        and stats["edge_ratio"] >= HAND_EDGE_RATIO_MIN
    )
    print(
        f"border_green={score} threshold={GREEN_PIXEL_THRESHOLD} "
        f"{'我的回合' if active else '非我的回合'} | "
        f"hand card={stats['card_ratio']:.3f} edge={stats['edge_ratio']:.3f} "
        f"{'已发牌' if dealt else '未发牌'}",
        flush=True,
    )
    return 0 if active else 1


def turn_debug_mode() -> None:
    load_turn_runtime_config()
    pyautogui = _require_pyautogui()
    print(
        f"[debug] AVATAR_ROI={AVATAR_ROI} threshold={GREEN_PIXEL_THRESHOLD} | Q=退出",
        flush=True,
    )
    try:
        while True:
            full_bgr = _prepare_frame_bgr(
                np.array(pyautogui.screenshot()),
                "pyautogui",
                from_native=True,
            )
            score = _green_border_score(_capture_avatar_bgr())
            active = score > GREEN_PIXEL_THRESHOLD
            hand_stats = probe_hand_cards_stats(full_bgr)
            dealt = (
                hand_stats["card_ratio"] >= HAND_CARD_RATIO_MIN
                and hand_stats["edge_ratio"] >= HAND_EDGE_RATIO_MIN
            )
            preview = full_bgr.copy()
            left, top, width, height = AVATAR_ROI
            cv2.rectangle(
                preview, (left, top), (left + width, top + height), (0, 0, 255), 2
            )
            x1, y1, x2, y2 = hand_stats["roi"]
            cv2.rectangle(
                preview, (x1, y1), (x2, y2), (0, 255, 0) if dealt else (0, 165, 255), 2
            )
            cv2.imshow("turn debug (Q=quit)", preview)
            if cv2.waitKey(200) & 0xFF in (ord("q"), ord("Q")):
                break
    finally:
        cv2.destroyAllWindows()


# =============================================================================
# CLI 入口
# =============================================================================


def _resolve_yolo_config(args: argparse.Namespace) -> tuple[Path, float, float, int]:
    weights = Path(
        args.model
        or os.environ.get("TONGITS_YOLO_MODEL")
        or str(DEFAULT_YOLO_WEIGHTS)
    )
    conf = float(args.conf if args.conf is not None else os.environ.get("TONGITS_YOLO_CONF", YOLO_CONF_THRESHOLD))
    if args.iou is not None:
        os.environ["TONGITS_YOLO_IOU"] = str(args.iou)
    elif "TONGITS_YOLO_IOU" not in os.environ:
        os.environ.setdefault("TONGITS_YOLO_IOU", str(YOLO_IOU_THRESHOLD))
    interval = float(
        args.interval
        if args.interval is not None
        else os.environ.get("TONGITS_SCOUT_INTERVAL", SCOUT_INTERVAL_SEC)
    )
    monitor = int(os.environ.get("TONGITS_MONITOR_INDEX", str(MONITOR_INDEX)))
    return weights, conf, interval, monitor


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Tongits 主循环 — 绿圈触发 YOLO 回合侦察（默认）"
    )
    ap.add_argument(
        "--save-only",
        action="store_true",
        help="仅截图存 omnioutput，不做 YOLO",
    )
    ap.add_argument(
        "--no-save-screenshot",
        action="store_true",
        help="不保存回合截图到 omnioutput（默认每回合保存，便于对日志）",
    )
    ap.add_argument(
        "--continuous",
        action="store_true",
        help="调试：每秒连续 YOLO 侦察（非回合触发）",
    )
    ap.add_argument("--debug", action="store_true", help="绿圈 / 手牌 ROI 校准窗口")
    ap.add_argument("--once", action="store_true", help="单次探测绿圈与发牌状态")
    ap.add_argument(
        "--yolo-once",
        action="store_true",
        help="单次全屏 YOLO 侦察（不依赖绿圈，调试用）",
    )
    ap.add_argument(
        "--model",
        default=None,
        help=f"YOLO 权重路径（默认 {DEFAULT_YOLO_WEIGHTS}）",
    )
    ap.add_argument(
        "--conf",
        type=float,
        default=None,
        help=f"YOLO 置信度阈值（默认 {YOLO_CONF_THRESHOLD}，低于该值的预测将被过滤）",
    )
    ap.add_argument(
        "--iou",
        type=float,
        default=None,
        help=f"YOLO NMS iou 阈值（默认 {YOLO_IOU_THRESHOLD}，角标重叠去重）",
    )
    ap.add_argument(
        "--interval",
        type=float,
        default=None,
        help=f"连续侦察间隔秒数（仅 --continuous，默认 {SCOUT_INTERVAL_SEC}）",
    )
    ap.add_argument(
        "--hybrid",
        action="store_true",
        help="混合侦察：手牌/明牌/弃牌 YOLO 裁区 + 左右对手 Qwen 并行（默认已开启）",
    )
    ap.add_argument(
        "--full-yolo",
        action="store_true",
        help="全屏一次 YOLO（关闭混合模式）",
    )
    ap.add_argument(
        "--florence-local",
        action="store_true",
        help="L2 本地 Florence-2 OCR+HSV 五战区认牌（默认 L2 推荐）",
    )
    ap.add_argument(
        "--qwen-full",
        action="store_true",
        help="遗留：云端 Qwen VLM+YOLO 手牌坐标（需 DASHSCOPE_API_KEY）",
    )
    ap.add_argument(
        "--auto-play",
        action="store_true",
        help="侦察后自动摸牌/吃牌/弃牌（默认 dry-run；配合 --auto-play-live 真实点击）",
    )
    ap.add_argument(
        "--auto-play-live",
        action="store_true",
        help="与 --auto-play 合用：真实鼠标点击（慎用）",
    )
    args = ap.parse_args()

    if args.auto_play or args.auto_play_live:
        os.environ["TONGITS_AUTO_PLAY"] = "1"
        os.environ["TONGITS_AUTO_PLAY_DRY_RUN"] = (
            "0" if args.auto_play_live else "1"
        )

    if args.florence_local:
        os.environ["TONGITS_SCOUT_MODE"] = "florence_local"
    elif args.qwen_full:
        os.environ["TONGITS_SCOUT_MODE"] = "qwen_full"
    elif args.full_yolo:
        os.environ["TONGITS_SCOUT_MODE"] = "yolo_full"
        os.environ["TONGITS_HYBRID_SCOUT"] = "0"
    elif args.hybrid:
        os.environ["TONGITS_SCOUT_MODE"] = "hybrid"
        os.environ["TONGITS_HYBRID_SCOUT"] = "1"

    if args.debug:
        turn_debug_mode()
        return 0
    if args.once:
        return turn_probe_once()

    weights, conf, interval, monitor = _resolve_yolo_config(args)

    if args.save_only:
        save_only_main_loop()
        return 0

    try:
        if args.yolo_once:
            return yolo_scout_once(
                weights_path=weights,
                conf=conf,
                monitor_index=monitor,
            )
        if args.continuous:
            yolo_scout_loop(
                weights_path=weights,
                conf=conf,
                interval_sec=interval,
                monitor_index=monitor,
            )
            return 0
        if args.no_save_screenshot:
            os.environ["TONGITS_TURN_SAVE_SCREENSHOT"] = "0"
        yolo_turn_main_loop(
            weights_path=weights,
            conf=conf,
            monitor_index=monitor,
        )
    except RuntimeError as e:
        print(str(e), file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
