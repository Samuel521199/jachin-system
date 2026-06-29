#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Florence-2 自然语言 → 截屏 → 定位 → 模拟鼠标点击（独立测试脚本）

流程（LLM + Florence + 点击前验证闭环，默认）：
  1. 你输入自然语言
  2. LLM 规划：意图、Florence 短语、验证标准、布局关系（如 + 在 = 上方）
  3. Florence 收集候选框
  4. **点击前验证**：对每个候选裁局部图，VL 判断「点这里对不对」（+ 不能是 =）
  5. 全失败则 **LLM 纠错重规划** 换策略，再搜再验（有限轮次）
  6. 通过验证后才点击

用法（仓库根，建议 jachin-dev 环境）::

  # 交互模式：输入描述后自动点击（Ctrl+C 退出）
  python scripts/test_florence2_nl_click.py

  # 只测定位、不点鼠标
  python scripts/test_florence2_nl_click.py --dry-run --once "Play Now button"

  # 对已有截图试一句（不截屏、不点击）
  python scripts/test_florence2_nl_click.py --image data/my_game_screenshot.png --dry-run --once "Spin button"

  # 点击前等待 5 秒，方便你切到游戏窗口
  python scripts/test_florence2_nl_click.py --once "Continue with Guest" --countdown 5

  # 限定只在屏幕某矩形内截屏 / 查找 / 点击（左,上,宽,高 — 屏幕绝对像素）
  python scripts/test_florence2_nl_click.py --region 120,80,1600,900

  # 启动时用鼠标框选允许区域（左上 → 右下，各按一次 Enter），并保存供下次复用
  python scripts/test_florence2_nl_click.py --pick-region --save-region-file data/florence2_test_out/nl_click/my_game_region.json

  # 下次直接读已保存区域
  python scripts/test_florence2_nl_click.py --region-file data/florence2_test_out/nl_click/my_game_region.json

  # 只框选区域并保存，不加载模型
  python scripts/test_florence2_nl_click.py --pick-region-only --save-region-file data/florence2_test_out/nl_click/my_game_region.json

  # 默认会在屏幕上用红色荧光笔标出鼠标移动轨迹与点击点；关闭 overlay 用 --no-trail
  python scripts/test_florence2_nl_click.py --trail-hold 12 --region-file data/florence2_test_out/nl_click/my_game_region.json

依赖::

  pip install pyautogui mss pillow torch openai python-dotenv "transformers==4.46.3" "huggingface-hub>=0.34.0,<1.0"
  # LLM 读 .env 中 DASHSCOPE_API_KEY；--no-llm 可跳过
  # 模型默认 data/models/Florence-2-base；缺失时见 scripts/test_florence2_phrase_grounding.py --download-modelscope

安全::
  - 默认开启 pyautogui FAILSAFE：鼠标快速移到屏幕左上角可中止
  - 建议先用 --dry-run 确认坐标，再去掉 dry-run 真点
  - 用 --pick-region / --region / --region-file 限定允许截屏与点击的矩形，避免误点屏幕其它区域
  - 默认开启红色荧光轨迹 overlay（--no-trail 可关）；每次操作后 overlay 停留 --trail-hold 秒，并落盘 *_trail.png
"""
from __future__ import annotations

import argparse
import ctypes
import importlib.util
import json
import os
import queue
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env", encoding="utf-8")
except ImportError:
    pass
except OSError:
    pass

from florence2_nl_click_planner import (
    GroundingCandidate,
    GroundingPlan,
    VerifyResult,
    bbox_area_ratio,
    draw_numbered_candidates,
    plan_grounding_intent,
    replan_after_verification_failures,
    score_candidates_heuristic,
    select_candidate_by_patch_verification,
)

DEFAULT_LOCAL_MODEL = ROOT / "data" / "models" / "Florence-2-base"
DEFAULT_OUT = ROOT / "data" / "florence2_test_out" / "nl_click"
DEFAULT_REGION_FILE = DEFAULT_OUT / "operation_region.json"
GROUNDING_SCRIPT = ROOT / "scripts" / "test_florence2_phrase_grounding.py"


def _enable_windows_dpi_awareness() -> None:
    """让截图像素坐标与 pyautogui 点击坐标尽量 1:1（Windows 高 DPI）。"""
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def _load_grounding_module():
    """复用 test_florence2_phrase_grounding 里的模型加载与 grounding 逻辑。"""
    if not GROUNDING_SCRIPT.is_file():
        raise FileNotFoundError(f"缺少 {GROUNDING_SCRIPT}")
    spec = importlib.util.spec_from_file_location("florence2_grounding", GROUNDING_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("无法加载 Florence grounding 模块")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Florence-2 自然语言定位 + pyautogui 点击")
    ap.add_argument(
        "--model",
        default=str(DEFAULT_LOCAL_MODEL)
        if DEFAULT_LOCAL_MODEL.is_dir()
        else "microsoft/Florence-2-base",
        help="Florence-2 本地目录或 HF id",
    )
    ap.add_argument(
        "--device",
        default="auto",
        choices=("auto", "cpu", "cuda"),
    )
    ap.add_argument(
        "--image",
        help="使用已有截图（不截屏）；与 --dry-run 联用可离线验证",
    )
    ap.add_argument(
        "--once",
        metavar="PHRASE",
        help="只执行一句描述后退出（非交互）",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="只定位并保存标注图，不移动/点击鼠标",
    )
    ap.add_argument(
        "--countdown",
        type=int,
        default=3,
        metavar="SEC",
        help="真点击前倒计时秒数（默认 3；0 表示不等待）",
    )
    ap.add_argument(
        "--monitor",
        type=int,
        default=1,
        help="mss 显示器序号，从 1 开始（默认主屏）",
    )
    ap.add_argument(
        "--out-dir",
        default=str(DEFAULT_OUT),
        help="标注图与 json 报告输出目录",
    )
    ap.add_argument(
        "--no-failsafe",
        action="store_true",
        help="关闭 pyautogui FAILSAFE（不推荐）",
    )
    ap.add_argument(
        "--region",
        metavar="LEFT,TOP,WIDTH,HEIGHT",
        help="允许查找与点击的屏幕矩形（绝对像素：左,上,宽,高）；仅在此区域内截屏",
    )
    ap.add_argument(
        "--region-file",
        metavar="PATH",
        help="从 JSON 读取操作区域（见 --save-region-file 产出格式）",
    )
    ap.add_argument(
        "--pick-region",
        action="store_true",
        help="启动前交互框选：鼠标移到左上角按 Enter，再移到右下角按 Enter",
    )
    ap.add_argument(
        "--pick-region-only",
        action="store_true",
        help="仅框选并保存区域后退出（不加载 Florence 模型）",
    )
    ap.add_argument(
        "--save-region-file",
        metavar="PATH",
        help="--pick-region 完成后写入 JSON；默认 data/florence2_test_out/nl_click/operation_region.json",
    )
    ap.add_argument(
        "--no-trail",
        action="store_true",
        help="关闭屏幕上的红色荧光鼠标轨迹 overlay",
    )
    ap.add_argument(
        "--trail-hold",
        type=float,
        default=8.0,
        metavar="SEC",
        help="每次移动/点击后轨迹在屏幕上停留秒数（默认 8）",
    )
    ap.add_argument(
        "--no-llm",
        action="store_true",
        help="跳过 LLM 意图规划，用户描述直通 Florence（旧行为）",
    )
    ap.add_argument(
        "--llm-model",
        default=os.environ.get("LLM_MODEL", "qwen3.5-plus"),
        help="意图规划文本模型（默认 LLM_MODEL 或 qwen3.5-plus）",
    )
    ap.add_argument(
        "--vl-model",
        default=os.environ.get("NL_CLICK_VL_MODEL", "qwen-vl-max"),
        help="多候选视觉重排模型（默认 qwen-vl-max）",
    )
    ap.add_argument(
        "--no-caption",
        action="store_true",
        help="跳过 Florence 画面描述（规划略快，复杂场景建议保留）",
    )
    ap.add_argument(
        "--max-adapt-rounds",
        type=int,
        default=1,
        metavar="N",
        help="验证全失败后的 LLM 纠错重规划轮数（默认 1，即最多 2 轮策略）",
    )
    ap.add_argument(
        "--no-verify",
        action="store_true",
        help="跳过点击前 patch 验证（不推荐，仅调试 Florence）",
    )
    ap.add_argument(
        "--quiet-planner",
        action="store_true",
        help="不打印意图规划 LLM 的 thinking/content 详细日志",
    )
    ap.add_argument(
        "--planner-thinking",
        action="store_true",
        help="开启 qwen3 系列 thinking（更慢；默认关闭以加速 JSON 规划）",
    )
    return ap.parse_args()


@dataclass(frozen=True)
class OperationRegion:
    """脚本允许截屏、定位、点击的屏幕矩形（绝对像素坐标）。"""

    left: int
    top: int
    width: int
    height: int

    @property
    def right(self) -> int:
        return self.left + self.width

    @property
    def bottom(self) -> int:
        return self.top + self.height

    def contains(self, x: int | float, y: int | float) -> bool:
        return (
            self.left <= x < self.right
            and self.top <= y < self.bottom
        )

    def to_dict(self) -> dict[str, int]:
        return {
            "left": self.left,
            "top": self.top,
            "width": self.width,
            "height": self.height,
        }

    @classmethod
    def from_dict(cls, data: dict) -> OperationRegion:
        if not isinstance(data, dict):
            raise ValueError("region JSON 必须是对象")
        # 兼容 x1,y1,x2,y2 写法
        if all(k in data for k in ("x1", "y1", "x2", "y2")):
            x1, y1, x2, y2 = int(data["x1"]), int(data["y1"]), int(data["x2"]), int(data["y2"])
            left, top = min(x1, x2), min(y1, y2)
            return cls(left=left, top=top, width=abs(x2 - x1), height=abs(y2 - y1))
        keys = ("left", "top", "width", "height")
        if not all(k in data for k in keys):
            raise ValueError(f"region JSON 需含 {keys} 或 x1,y1,x2,y2")
        left, top, width, height = (int(data[k]) for k in keys)
        return cls(left=left, top=top, width=width, height=height)

    @classmethod
    def parse_csv(cls, raw: str) -> OperationRegion:
        parts = [p.strip() for p in raw.split(",")]
        if len(parts) != 4:
            raise ValueError("--region 格式应为 LEFT,TOP,WIDTH,HEIGHT")
        left, top, w, h = (int(p) for p in parts)
        return cls(left=left, top=top, width=w, height=h)

    def validate(self) -> None:
        if self.width < 20 or self.height < 20:
            raise ValueError(f"操作区域过小: {self.width}x{self.height}（至少 20x20）")

    def format_line(self) -> str:
        return (
            f"left={self.left} top={self.top} "
            f"width={self.width} height={self.height} "
            f"(right={self.right} bottom={self.bottom})"
        )


def _save_region_file(region: OperationRegion, path: Path) -> None:
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": 1, **region.to_dict()}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[region] 已保存 -> {path}", flush=True)


def _load_region_file(path: Path) -> OperationRegion:
    path = path.expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"区域文件不存在: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    region = OperationRegion.from_dict(data)
    region.validate()
    return region


def _pick_region_interactive() -> OperationRegion:
    import pyautogui

    print(
        "\n=== 框选允许操作区域 ===\n"
        "脚本只会在此矩形内：截屏 → Florence 查找 → 鼠标点击。\n"
        "屏幕其余区域不会被截取，也不会点击。\n",
        flush=True,
    )
    print("1) 将鼠标移到【左上角】，按 Enter", flush=True)
    try:
        input()
    except (EOFError, KeyboardInterrupt):
        raise SystemExit("\n[exit] 已取消框选") from None
    x1, y1 = pyautogui.position()
    print(f"   左上 = ({x1}, {y1})", flush=True)

    print("2) 将鼠标移到【右下角】，按 Enter", flush=True)
    try:
        input()
    except (EOFError, KeyboardInterrupt):
        raise SystemExit("\n[exit] 已取消框选") from None
    x2, y2 = pyautogui.position()
    print(f"   右下 = ({x2}, {y2})", flush=True)

    left, top = min(x1, x2), min(y1, y2)
    region = OperationRegion(
        left=left,
        top=top,
        width=abs(x2 - x1),
        height=abs(y2 - y1),
    )
    region.validate()
    print(f"[region] {region.format_line()}", flush=True)
    return region


def _resolve_operation_region(args: argparse.Namespace) -> OperationRegion | None:
    save_path = (
        Path(args.save_region_file).expanduser()
        if args.save_region_file
        else DEFAULT_REGION_FILE
    )

    if args.pick_region or args.pick_region_only:
        region = _pick_region_interactive()
        _save_region_file(region, save_path)
        return None if args.pick_region_only else region

    if args.region:
        region = OperationRegion.parse_csv(args.region)
        region.validate()
        return region

    if args.region_file:
        return _load_region_file(Path(args.region_file))

    return None


@dataclass
class CaptureMeta:
    """一次截屏的元数据，用于屏幕坐标 ↔ 图像坐标映射。"""

    offset_x: int = 0
    offset_y: int = 0
    source: str = "screen"
    region: OperationRegion | None = None


def _capture_screen(
    monitor_index: int,
    region: OperationRegion | None = None,
) -> tuple[Any, CaptureMeta]:
    from PIL import Image

    try:
        import mss
    except ImportError as e:
        raise RuntimeError("请安装 mss: pip install mss") from e

    with mss.MSS() as sct:
        if region is not None:
            grab_box = {
                "left": region.left,
                "top": region.top,
                "width": region.width,
                "height": region.height,
            }
            shot = sct.grab(grab_box)
            img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
            meta = CaptureMeta(
                offset_x=region.left,
                offset_y=region.top,
                source=f"region({region.left},{region.top},{region.width},{region.height})",
                region=region,
            )
            return img, meta

        monitors = sct.monitors
        if monitor_index < 1 or monitor_index >= len(monitors):
            raise ValueError(
                f"无效 --monitor={monitor_index}，可用 1..{len(monitors) - 1}"
            )
        mon = monitors[monitor_index]
        shot = sct.grab(mon)
        img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
        meta = CaptureMeta(
            offset_x=int(mon["left"]),
            offset_y=int(mon["top"]),
            source=f"monitor_{monitor_index}",
            region=None,
        )
        return img, meta


def _load_image_file(path: Path) -> tuple[Any, CaptureMeta]:
    from PIL import Image

    img = Image.open(path).convert("RGB")
    return img, CaptureMeta(source=str(path))


def _normalize_phrase(raw: str) -> str:
    """去掉常见中文前缀，把用户口语转成 Florence 短语。"""
    s = (raw or "").strip()
    for prefix in (
        "请点击",
        "请点",
        "点击",
        "点一下",
        "点",
        "click ",
        "Click ",
    ):
        if s.lower().startswith(prefix.lower()):
            s = s[len(prefix) :].strip()
            break
    return s


def _pick_best_hit(
    hits: list[dict],
    meta: CaptureMeta,
    region: OperationRegion | None,
) -> dict | None:
    if not hits:
        return None

    def area(h: dict) -> float:
        x1, y1, x2, y2 = h["bbox"]
        return max(0.0, x2 - x1) * max(0.0, y2 - y1)

    effective_region = region or meta.region
    candidates: list[dict] = []
    for h in hits:
        sx, sy = _image_to_screen(h["cx"], h["cy"], meta)
        if effective_region is None or effective_region.contains(sx, sy):
            candidates.append(h)

    if not candidates:
        return None
    return max(candidates, key=area)


def _image_to_screen(cx: float, cy: float, meta: CaptureMeta) -> tuple[int, int]:
    return int(round(cx + meta.offset_x)), int(round(cy + meta.offset_y))


def _screen_to_image(sx: int, sy: int, meta: CaptureMeta) -> tuple[int, int]:
    return int(round(sx - meta.offset_x)), int(round(sy - meta.offset_y))


# 荧光笔轨迹层：外暗内亮，模拟红色荧光标记
_TRAIL_LAYERS: tuple[tuple[str, int], ...] = (
    ("#660018", 16),
    ("#990022", 12),
    ("#CC0028", 9),
    ("#FF1133", 6),
    ("#FF5577", 3),
    ("#FFFFFF", 1),
)


def _overlay_bounds(region: OperationRegion | None) -> tuple[int, int, int, int]:
    """返回 overlay 的 left, top, width, height（屏幕绝对像素）。"""
    if region is not None:
        return region.left, region.top, region.width, region.height
    import pyautogui

    w, h = pyautogui.size()
    return 0, 0, int(w), int(h)


def _set_win_click_through_toplevel(root) -> None:
    if sys.platform != "win32":
        return
    try:
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
        if hwnd == 0:
            hwnd = root.winfo_id()
        gwl_exstyle = -20
        ws_ex_layered = 0x00080000
        ws_ex_transparent = 0x00000020
        ws_ex_topmost = 0x00000008
        style = ctypes.windll.user32.GetWindowLongW(hwnd, gwl_exstyle)
        ctypes.windll.user32.SetWindowLongW(
            hwnd,
            gwl_exstyle,
            style | ws_ex_layered | ws_ex_transparent | ws_ex_topmost,
        )
    except Exception:
        pass


@dataclass
class _TrailSegment:
    """一段轨迹（屏幕绝对坐标）。"""

    points: list[tuple[int, int]] = field(default_factory=list)
    click: tuple[int, int] | None = None
    click_index: int = 0
    dry_run: bool = False


class MouseTrailOverlay:
    """置顶透明 overlay：红色荧光笔轨迹 + 点击十字标记（鼠标可穿透）。"""

    def __init__(
        self,
        region: OperationRegion | None,
        *,
        hold_sec: float = 8.0,
    ) -> None:
        self.region = region
        self.hold_sec = max(0.0, hold_sec)
        self._left, self._top, self._width, self._height = _overlay_bounds(region)
        self._queue: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._segments: list[_TrailSegment] = []
        self._click_total = 0
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._tk_main, name="mouse-trail-overlay", daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout=3.0):
            print("[WARN] 轨迹 overlay 启动超时，仍会继续运行", flush=True)

    def _tk_main(self) -> None:
        import tkinter as tk

        root = tk.Tk()
        root.overrideredirect(True)
        root.configure(bg="black")
        root.attributes("-topmost", True)
        root.attributes("-transparentcolor", "black")
        root.geometry(f"{self._width}x{self._height}+{self._left}+{self._top}")

        canvas = tk.Canvas(
            root,
            width=self._width,
            height=self._height,
            bg="black",
            highlightthickness=0,
        )
        canvas.pack(fill=tk.BOTH, expand=True)

        root.update_idletasks()
        _set_win_click_through_toplevel(root)
        self._root = root
        self._canvas = canvas
        self._ready.set()

        def _poll() -> None:
            while True:
                try:
                    cmd, payload = self._queue.get_nowait()
                except queue.Empty:
                    break
                if cmd == "redraw":
                    self._draw_all(canvas)
                elif cmd == "clear":
                    self._segments.clear()
                    canvas.delete("all")
                elif cmd == "shutdown":
                    root.destroy()
                    return
            root.after(16, _poll)

        root.after(16, _poll)
        root.mainloop()

    def _to_local(self, sx: int, sy: int) -> tuple[int, int]:
        return sx - self._left, sy - self._top

    def _draw_fluorescent_polyline(self, canvas, pts: list[tuple[int, int]], *, dashed: bool) -> None:
        if len(pts) < 2:
            return
        flat = [coord for pt in pts for coord in pt]
        dash = (6, 4) if dashed else None
        for color, width in _TRAIL_LAYERS:
            canvas.create_line(
                *flat,
                fill=color,
                width=width,
                capstyle="round",
                joinstyle="round",
                smooth=True,
                dash=dash,
            )

    def _draw_click_marker(self, canvas, lx: int, ly: int, index: int) -> None:
        for radius, color, width in (
            (22, "#660018", 2),
            (16, "#FF1133", 3),
            (10, "#FF5577", 2),
            (5, "#FFFFFF", 2),
        ):
            canvas.create_oval(
                lx - radius,
                ly - radius,
                lx + radius,
                ly + radius,
                outline=color,
                width=width,
            )
        arm = 18
        canvas.create_line(lx - arm, ly, lx + arm, ly, fill="#FFFFFF", width=2)
        canvas.create_line(lx, ly - arm, lx, ly + arm, fill="#FFFFFF", width=2)
        canvas.create_text(
            lx,
            ly - 28,
            text=f"CLICK #{index}",
            fill="#FF2244",
            font=("Arial", 11, "bold"),
        )

    def _draw_start_marker(self, canvas, lx: int, ly: int) -> None:
        canvas.create_oval(lx - 7, ly - 7, lx + 7, ly + 7, outline="#FF8899", width=2)
        canvas.create_text(lx, ly - 16, text="START", fill="#FF8899", font=("Arial", 9, "bold"))

    def _draw_all(self, canvas) -> None:
        canvas.delete("all")
        for seg in self._segments:
            if seg.points:
                local_pts = [self._to_local(x, y) for x, y in seg.points]
                self._draw_fluorescent_polyline(canvas, local_pts, dashed=seg.dry_run)
                sx, sy = seg.points[0]
                lsx, lsy = self._to_local(sx, sy)
                self._draw_start_marker(canvas, lsx, lsy)
            if seg.click is not None:
                lx, ly = self._to_local(*seg.click)
                self._draw_click_marker(canvas, lx, ly, seg.click_index)

    def _push_segment(self, segment: _TrailSegment) -> None:
        self._segments.append(segment)
        self._queue.put(("redraw", None))

    def _schedule_clear(self) -> None:
        if self.hold_sec <= 0:
            return

        def _clear_later() -> None:
            time.sleep(self.hold_sec)
            self._queue.put(("clear", None))

        threading.Thread(target=_clear_later, name="trail-clear", daemon=True).start()

    def show_path(
        self,
        points: list[tuple[int, int]],
        click: tuple[int, int] | None,
        *,
        dry_run: bool = False,
    ) -> None:
        if not points and click is None:
            return
        if click is not None:
            self._click_total += 1
        seg = _TrailSegment(
            points=list(points),
            click=click,
            click_index=self._click_total,
            dry_run=dry_run,
        )
        self._push_segment(seg)
        self._schedule_clear()

    def shutdown(self) -> None:
        self._queue.put(("shutdown", None))


@dataclass
class TrailRunRecord:
    """单次移动/点击的轨迹记录（屏幕绝对坐标）。"""

    points: list[tuple[int, int]] = field(default_factory=list)
    click: tuple[int, int] | None = None
    dry_run: bool = False


class TrailController:
    """协调实时 overlay 与截图上的轨迹落盘。"""

    def __init__(
        self,
        region: OperationRegion | None,
        *,
        hold_sec: float = 8.0,
        enabled: bool = True,
    ) -> None:
        self.enabled = enabled
        self._overlay: MouseTrailOverlay | None = None
        if enabled:
            try:
                self._overlay = MouseTrailOverlay(region, hold_sec=hold_sec)
                print(
                    f"[trail] 红色荧光轨迹 overlay 已开启（停留 {hold_sec:.0f}s，"
                    "同时会保存到截图报告）",
                    flush=True,
                )
            except Exception as e:
                print(f"[WARN] 轨迹 overlay 启动失败: {e}", flush=True)
                self.enabled = False

    def shutdown(self) -> None:
        if self._overlay is not None:
            self._overlay.shutdown()

    @staticmethod
    def _dedupe_points(points: list[tuple[int, int]]) -> list[tuple[int, int]]:
        if not points:
            return []
        out = [points[0]]
        for pt in points[1:]:
            if abs(pt[0] - out[-1][0]) + abs(pt[1] - out[-1][1]) >= 2:
                out.append(pt)
        return out

    def _sample_while_moving(
        self,
        stop_event: threading.Event,
        sink: list[tuple[int, int]],
    ) -> None:
        import pyautogui

        while not stop_event.is_set():
            pos = pyautogui.position()
            sink.append((int(pos.x), int(pos.y)))
            time.sleep(0.016)

    def move_and_click(
        self,
        x: int,
        y: int,
        *,
        duration: float = 0.25,
        dry_run: bool = False,
    ) -> TrailRunRecord:
        import pyautogui

        start = pyautogui.position()
        record = TrailRunRecord(dry_run=dry_run)
        record.points.append((int(start.x), int(start.y)))

        if dry_run:
            record.points.append((x, y))
            record.click = (x, y)
            if self.enabled and self._overlay is not None:
                self._overlay.show_path(record.points, record.click, dry_run=True)
            return record

        sampled: list[tuple[int, int]] = []
        stop_event = threading.Event()
        sampler = threading.Thread(
            target=self._sample_while_moving,
            args=(stop_event, sampled),
            daemon=True,
        )
        sampler.start()
        pyautogui.moveTo(x, y, duration=duration)
        stop_event.set()
        sampler.join(timeout=0.5)
        record.points.extend(sampled)
        record.points.append((x, y))
        record.points = self._dedupe_points(record.points)
        pyautogui.click(x, y)
        record.click = (x, y)

        if self.enabled and self._overlay is not None:
            self._overlay.show_path(record.points, record.click, dry_run=False)
        return record

    @staticmethod
    def save_trail_on_image(
        image,
        meta: CaptureMeta,
        record: TrailRunRecord,
        out_path: Path,
    ) -> None:
        from PIL import ImageDraw, ImageFont

        img = image.copy()
        draw = ImageDraw.Draw(img)

        def to_img(sx: int, sy: int) -> tuple[int, int]:
            return _screen_to_image(sx, sy, meta)

        local_pts = [to_img(x, y) for x, y in record.points]
        if len(local_pts) >= 2:
            for color, width in _TRAIL_LAYERS:
                draw.line(local_pts, fill=color, width=width, joint="curve")
            sx, sy = record.points[0]
            ix, iy = to_img(sx, sy)
            draw.ellipse([ix - 6, iy - 6, ix + 6, iy + 6], outline="#FF8899", width=2)
            draw.text((ix - 12, iy - 22), "START", fill="#FF8899")

        if record.click is not None:
            cx, cy = to_img(*record.click)
            for radius, color, width in (
                (22, "#660018", 2),
                (16, "#FF1133", 3),
                (10, "#FF5577", 2),
                (5, "#FFFFFF", 2),
            ):
                draw.ellipse(
                    [cx - radius, cy - radius, cx + radius, cy + radius],
                    outline=color,
                    width=width,
                )
            arm = 18
            draw.line([cx - arm, cy, cx + arm, cy], fill="#FFFFFF", width=2)
            draw.line([cx, cy - arm, cx, cy + arm], fill="#FFFFFF", width=2)
            label = "CLICK (dry-run)" if record.dry_run else "CLICK"
            try:
                font = ImageFont.truetype("arial.ttf", 14)
            except OSError:
                font = ImageFont.load_default()
            draw.text((cx - 20, cy - 32), label, fill="#FF2244", font=font)

        out_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(out_path)


def _ground_phrase(
    fg_mod,
    processor,
    model,
    device,
    image,
    phrase: str,
) -> tuple[list[dict], float, object]:
    parsed, ms = fg_mod._run_task(
        processor,
        model,
        device,
        image,
        "<CAPTION_TO_PHRASE_GROUNDING>",
        phrase,
    )
    boxes = fg_mod._extract_grounding_boxes(parsed)
    return boxes, ms, parsed


def _get_scene_caption(
    fg_mod,
    processor,
    model,
    device,
    image,
    *,
    skip: bool,
) -> tuple[str | None, float]:
    if skip:
        return None, 0.0
    try:
        cap, ms = fg_mod._run_task(
            processor, model, device, image, "<DETAILED_CAPTION>",
        )
        if isinstance(cap, dict):
            text = json.dumps(cap, ensure_ascii=False)
        else:
            text = str(cap)
        return text[:1500], ms
    except Exception as e:
        print(f"[WARN] Florence 画面描述失败: {e}", flush=True)
        return None, 0.0


def _collect_florence_candidates(
    fg_mod,
    processor,
    model,
    device,
    image,
    phrases: list[str],
    meta: CaptureMeta,
    region: OperationRegion | None,
) -> tuple[list[GroundingCandidate], float]:
    """对多条短语分别接地，去重合并候选。"""
    candidates: list[GroundingCandidate] = []
    seen: set[tuple[int, int]] = set()
    total_ms = 0.0

    for phrase in phrases:
        hits, ms, raw = _ground_phrase(fg_mod, processor, model, device, image, phrase)
        total_ms += ms
        for h in hits:
            sx, sy = _image_to_screen(h["cx"], h["cy"], meta)
            if region is not None and not region.contains(sx, sy):
                continue
            key = (int(round(h["cx"] / 8)), int(round(h["cy"] / 8)))
            if key in seen:
                continue
            seen.add(key)
            candidates.append(
                GroundingCandidate(
                    phrase=phrase,
                    bbox=[float(x) for x in h["bbox"][:4]],
                    cx=float(h["cx"]),
                    cy=float(h["cy"]),
                    label=str(h.get("label") or ""),
                    florence_raw=raw,
                )
            )
        print(f"  [florence] phrase={phrase!r} -> {len(hits)} box(es)", flush=True)
    return candidates, total_ms


def _candidate_as_hit(c: GroundingCandidate) -> dict:
    return {
        "bbox": c.bbox,
        "cx": c.cx,
        "cy": c.cy,
        "label": c.label,
        "phrase": c.phrase,
        "heuristic_score": c.heuristic_score,
    }


def _print_plan(plan: GroundingPlan) -> None:
    print(f"  摘要: {plan.intent_summary}", flush=True)
    print(f"  空间: {plan.spatial_hint or '（无）'}", flush=True)
    if plan.layout_relation:
        print(f"  布局: {plan.layout_relation}", flush=True)
    if plan.verification_criteria:
        print(f"  验证: {plan.verification_criteria}", flush=True)
    print(f"  目标: {plan.target_label!r}  避免: {plan.avoid_labels}", flush=True)
    print(f"  Florence 短语: {plan.florence_phrases}", flush=True)
    if plan.adaptation_note:
        print(f"  纠错: {plan.adaptation_note}", flush=True)


def _resolve_grounding_target(
    fg_mod,
    processor,
    model,
    device,
    image,
    meta: CaptureMeta,
    region: OperationRegion | None,
    user_query: str,
    *,
    use_llm: bool,
    llm_model: str,
    vl_model: str,
    skip_caption: bool,
    max_adapt_rounds: int = 1,
    enable_verify: bool = True,
    planner_verbose: bool = True,
    planner_enable_thinking: bool = False,
) -> tuple[dict | None, GroundingPlan | None, list[GroundingCandidate], float, dict[str, Any]]:
    """闭环：规划 → Florence 候选 → 点击前 patch 验证 → 失败则 LLM 重规划。"""
    import time

    t0 = time.perf_counter()
    extra: dict[str, Any] = {"verify_log": [], "adapt_rounds": []}

    caption, cap_ms = _get_scene_caption(
        fg_mod, processor, model, device, image, skip=skip_caption,
    )
    if caption:
        print(f"[caption] Florence 画面描述 ({cap_ms:.0f}ms)", flush=True)
        extra["scene_caption_ms"] = cap_ms

    plan: GroundingPlan | None = None
    if use_llm:
        print(f"[llm] 意图规划 model={llm_model!r} ...", flush=True)
        plan = plan_grounding_intent(
            user_query,
            image_width=image.width,
            image_height=image.height,
            scene_caption=caption,
            llm_model=llm_model,
            planner_verbose=planner_verbose,
            enable_thinking=planner_enable_thinking,
        )
        if plan.planner_latency_ms:
            print(f"  [llm][plan] 耗时 {plan.planner_latency_ms:.0f}ms", flush=True)
        _print_plan(plan)
    else:
        phrases = [_normalize_phrase(user_query)]
        print(f"[ground] 直通 Florence phrase={phrases[0]!r}", flush=True)

    all_candidates: list[GroundingCandidate] = []
    total_florence_ms = 0.0
    chosen_idx: int | None = None
    verify_log: list[VerifyResult] = []

    max_rounds = max(0, max_adapt_rounds) + 1
    for round_i in range(max_rounds):
        if round_i > 0 and plan is not None:
            print(f"[adapt] 第 {round_i} 轮纠错重规划 ...", flush=True)
            plan = replan_after_verification_failures(
                plan,
                verify_log,
                all_candidates,
                image_width=image.width,
                image_height=image.height,
                llm_model=llm_model,
                planner_verbose=planner_verbose,
                enable_thinking=planner_enable_thinking,
            )
            if plan.planner_latency_ms:
                print(f"  [llm][replan] 耗时 {plan.planner_latency_ms:.0f}ms", flush=True)
            _print_plan(plan)
            extra["adapt_rounds"].append(plan.adaptation_note or f"round_{round_i}")

        phrases = plan.florence_phrases if plan else [_normalize_phrase(user_query)]
        candidates, florence_ms = _collect_florence_candidates(
            fg_mod, processor, model, device, image, phrases, meta, region,
        )
        total_florence_ms += florence_ms
        all_candidates = candidates

        if not candidates:
            continue

        if plan is not None:
            score_candidates_heuristic(
                plan, candidates, image_width=image.width, image_height=image.height,
            )
        for i, c in enumerate(candidates, 1):
            ar = bbox_area_ratio(c.bbox, image.width, image.height)
            print(
                f"  候选 #{i} phrase={c.phrase!r} label={c.label!r} "
                f"center=({c.cx:.0f},{c.cy:.0f}) area={ar:.1%} score={c.heuristic_score:.2f}",
                flush=True,
            )

        if not enable_verify or plan is None:
            chosen_idx = max(
                range(len(candidates)),
                key=lambda i: candidates[i].heuristic_score,
            )
            extra["selection"] = "heuristic_no_verify"
            break

        print(f"[verify] 点击前局部验证 model={vl_model!r} ...", flush=True)
        chosen_idx, verify_log = select_candidate_by_patch_verification(
            plan, candidates, image, vl_model=vl_model,
        )
        extra["verify_log"] = [v.to_dict() for v in verify_log]
        if chosen_idx is not None:
            extra["selection"] = f"patch_verified_round_{round_i}"
            print(f"  [verify] 采纳候选 #{chosen_idx + 1}", flush=True)
            break

        print("[adapt] 本轮候选均未通过验证", flush=True)

    extra["florence_total_ms"] = total_florence_ms

    if chosen_idx is None or not all_candidates:
        extra["execution_brief"] = (
            "所有策略均未通过点击前验证。建议：换更具体的描述、缩小 --region、"
            "或查看 candidates/verify 日志确认 Florence 框是否偏到相邻键。"
        )
        print(f"[ExecutionBrief] {extra['execution_brief']}", flush=True)
        total_ms = (time.perf_counter() - t0) * 1000.0
        return None, plan, all_candidates, total_ms, extra

    hit = _candidate_as_hit(all_candidates[chosen_idx])
    extra["chosen_index"] = chosen_idx
    total_ms = (time.perf_counter() - t0) * 1000.0
    return hit, plan, all_candidates, total_ms, extra


def _save_run_artifacts(
    fg_mod,
    image,
    user_query: str,
    hit: dict | None,
    out_dir: Path,
    *,
    screen_xy: tuple[int, int] | None,
    latency_ms: float,
    dry_run: bool,
    region: OperationRegion | None = None,
    meta: CaptureMeta | None = None,
    trail_record: TrailRunRecord | None = None,
    plan: GroundingPlan | None = None,
    candidates: list[GroundingCandidate] | None = None,
    pipeline_extra: dict[str, Any] | None = None,
) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    safe = "".join(c if c.isalnum() else "_" for c in user_query[:32])
    ann_path = out_dir / f"{stamp}_{safe}_annotated.png"
    trail_path = out_dir / f"{stamp}_{safe}_trail.png"
    candidates_path = out_dir / f"{stamp}_{safe}_candidates.png"
    report_path = out_dir / f"{stamp}_{safe}_report.json"

    hits_for_draw: list[dict] = []
    if hit:
        hits_for_draw.append({**hit, "phrase": hit.get("phrase", user_query)})
    if hits_for_draw:
        fg_mod._draw_annotations(image, hits_for_draw, ann_path)

    if candidates:
        draw_numbered_candidates(image, candidates).save(candidates_path)

    saved_trail = None
    if trail_record is not None and meta is not None:
        TrailController.save_trail_on_image(image, meta, trail_record, trail_path)
        saved_trail = trail_path

    report: dict[str, Any] = {
        "user_query": user_query,
        "latency_ms": round(latency_ms, 1),
        "hit": hit,
        "screen_xy": list(screen_xy) if screen_xy else None,
        "operation_region": region.to_dict() if region else None,
        "dry_run": dry_run,
        "annotated_image": str(ann_path) if hits_for_draw else None,
        "candidates_image": str(candidates_path) if candidates else None,
        "trail_image": str(saved_trail) if saved_trail else None,
        "trail_points": trail_record.points if trail_record else None,
        "trail_click": list(trail_record.click) if trail_record and trail_record.click else None,
        "grounding_plan": plan.to_dict() if plan else None,
        "candidates": [c.to_dict() for c in candidates] if candidates else None,
        "pipeline": pipeline_extra or {},
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report_path


def _perform_click(
    x: int,
    y: int,
    *,
    dry_run: bool,
    countdown: int,
    region: OperationRegion | None = None,
    trail: TrailController | None = None,
) -> TrailRunRecord | None:
    if region is not None and not region.contains(x, y):
        print(
            f"  [blocked] 点击坐标 ({x}, {y}) 超出允许区域 — 已拒绝操作",
            flush=True,
        )
        return None

    import pyautogui

    if dry_run:
        print(f"  [dry-run] 将点击屏幕坐标 ({x}, {y})，未移动鼠标", flush=True)
        if trail is not None and trail.enabled:
            rec = trail.move_and_click(x, y, dry_run=True)
            print(f"  [trail] 已在 overlay 显示模拟轨迹（{len(rec.points)} 采样点）", flush=True)
            return rec
        return TrailRunRecord(
            points=[(int(pyautogui.position().x), int(pyautogui.position().y)), (x, y)],
            click=(x, y),
            dry_run=True,
        )

    if countdown > 0:
        print(f"  将在 {countdown}s 后点击 ({x}, {y}) — 请切到目标窗口（左上角可中止）", flush=True)
        for i in range(countdown, 0, -1):
            print(f"    {i}...", flush=True)
            time.sleep(1)

    if trail is not None and trail.enabled:
        rec = trail.move_and_click(x, y, dry_run=False)
        print(f"  [click] ({x}, {y})  [trail] {len(rec.points)} 采样点已标红", flush=True)
        return rec

    start_pos = pyautogui.position()
    pyautogui.moveTo(x, y, duration=0.25)
    pyautogui.click(x, y)
    print(f"  [click] ({x}, {y})", flush=True)
    return TrailRunRecord(
        points=[(int(start_pos.x), int(start_pos.y)), (x, y)],
        click=(x, y),
        dry_run=False,
    )


def _run_one(
    fg_mod,
    processor,
    model,
    device,
    *,
    phrase: str,
    image_path: Path | None,
    monitor: int,
    dry_run: bool,
    countdown: int,
    out_dir: Path,
    region: OperationRegion | None = None,
    trail: TrailController | None = None,
    use_llm: bool = True,
    llm_model: str = "qwen3.5-plus",
    vl_model: str = "qwen-vl-max",
    skip_caption: bool = False,
    max_adapt_rounds: int = 1,
    enable_verify: bool = True,
    planner_verbose: bool = True,
    planner_enable_thinking: bool = False,
) -> bool:
    user_query = (phrase or "").strip()
    if not user_query:
        print("[WARN] 空描述，跳过", flush=True)
        return False

    if image_path:
        if region is not None:
            print(
                "[WARN] --image 模式下忽略 --region：静态图本身即为查找范围",
                flush=True,
            )
        image, meta = _load_image_file(image_path)
        print(f"[image] {image_path} size={image.width}x{image.height}", flush=True)
    else:
        if region is not None:
            print(f"[capture] region {region.format_line()}", flush=True)
            image, meta = _capture_screen(monitor, region=region)
        else:
            print(f"[capture] monitor={monitor} (全屏)", flush=True)
            image, meta = _capture_screen(monitor)
        print(
            f"[capture] size={image.width}x{image.height} offset=({meta.offset_x},{meta.offset_y})",
            flush=True,
        )

    try:
        hit, plan, candidates, total_ms, pipeline_extra = _resolve_grounding_target(
            fg_mod, processor, model, device, image, meta, region, user_query,
            use_llm=use_llm, llm_model=llm_model, vl_model=vl_model,
            skip_caption=skip_caption,
            max_adapt_rounds=max_adapt_rounds,
            enable_verify=enable_verify,
            planner_verbose=planner_verbose,
            planner_enable_thinking=planner_enable_thinking,
        )
    except Exception as e:
        print(f"[ERROR] 定位管线失败: {e}", file=sys.stderr)
        return False

    if not hit:
        print("  未找到匹配区域 — 可换描述或检查 LLM 生成的 Florence 短语", flush=True)
        _save_run_artifacts(
            fg_mod, image, user_query, None, out_dir,
            screen_xy=None, latency_ms=total_ms, dry_run=dry_run,
            region=region, meta=meta, plan=plan, candidates=candidates or None,
            pipeline_extra=pipeline_extra,
        )
        return False

    cx, cy = hit["cx"], hit["cy"]
    sx, sy = _image_to_screen(cx, cy, meta)

    print(f"  选中 phrase={hit.get('phrase')!r} label={hit.get('label')!r}")
    print(f"  bbox={hit['bbox']}  image_center=({cx:.1f}, {cy:.1f})")
    print(f"  screen=({sx}, {sy})  pipeline_ms={total_ms:.0f}")
    if region is not None:
        print(f"  region_ok={region.contains(sx, sy)}", flush=True)

    if image_path and not dry_run:
        print(
            "  [WARN] 使用了 --image 静态图，坐标来自图像像素而非实时截屏；"
            "真点击请去掉 --image",
            flush=True,
        )

    trail_record = _perform_click(
        sx, sy, dry_run=dry_run, countdown=countdown, region=region, trail=trail,
    )

    report_path = _save_run_artifacts(
        fg_mod, image, user_query, hit, out_dir,
        screen_xy=(sx, sy), latency_ms=total_ms, dry_run=dry_run,
        region=region, meta=meta, trail_record=trail_record,
        plan=plan, candidates=candidates or None, pipeline_extra=pipeline_extra,
    )
    print(f"  report -> {report_path}", flush=True)
    trail_png = report_path.with_name(report_path.name.replace("_report.json", "_trail.png"))
    cand_png = report_path.with_name(report_path.name.replace("_report.json", "_candidates.png"))
    if trail_png.is_file():
        print(f"  trail  -> {trail_png}", flush=True)
    if cand_png.is_file():
        print(f"  candidates -> {cand_png}", flush=True)
    return True


def _interactive_loop(
    fg_mod,
    processor,
    model,
    device,
    args: argparse.Namespace,
    region: OperationRegion | None,
    trail: TrailController | None,
) -> None:
    out_dir = Path(args.out_dir)
    image_path = Path(args.image).expanduser().resolve() if args.image else None

    region_line = (
        f"操作区域: {region.format_line()}\n"
        if region is not None
        else "操作区域: 未限定（整屏 monitor）\n"
    )

    print(
        "\n=== LLM + Florence 自然语言点击（交互）===\n"
        f"{region_line}"
        "输入自然语言（中文即可），LLM 会拆解意图，Florence 负责找框：\n"
        '  点击计算器里的 8\n'
        '  Play Now button\n'
        '  右上角红色关闭按钮\n'
        "空行退出；Ctrl+C 中断。\n",
        flush=True,
    )

    while True:
        try:
            raw = input("描述> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n[exit]", flush=True)
            break
        if not raw:
            break
        _run_one(
            fg_mod,
            processor,
            model,
            device,
            phrase=raw,
            image_path=image_path,
            monitor=args.monitor,
            dry_run=args.dry_run,
            countdown=args.countdown,
            out_dir=out_dir,
            region=region,
            trail=trail,
            use_llm=not args.no_llm,
            llm_model=args.llm_model,
            vl_model=args.vl_model,
            skip_caption=args.no_caption,
            max_adapt_rounds=args.max_adapt_rounds,
            enable_verify=not args.no_verify,
            planner_verbose=not args.quiet_planner,
            planner_enable_thinking=args.planner_thinking,
        )
        print()


def main() -> int:
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
            sys.stderr.reconfigure(encoding="utf-8")
        except Exception:
            pass

    _enable_windows_dpi_awareness()
    args = _parse_args()

    try:
        import pyautogui  # noqa: F401
    except ImportError:
        print("[ERROR] 请安装 pyautogui: pip install pyautogui", file=sys.stderr)
        return 2

    import pyautogui

    pyautogui.FAILSAFE = not args.no_failsafe
    if pyautogui.FAILSAFE:
        print("[info] pyautogui FAILSAFE 已开启：鼠标甩到左上角可紧急停止", flush=True)

    try:
        region = _resolve_operation_region(args)
    except (ValueError, FileNotFoundError) as e:
        print(f"[ERROR] 操作区域无效: {e}", file=sys.stderr)
        return 2

    if args.pick_region_only:
        return 0

    if region is not None:
        print(f"[region] 已启用 {region.format_line()}", flush=True)
    elif not args.image:
        print("[region] 未限定操作区域 — 将使用整屏 monitor 截屏", flush=True)

    if not args.no_llm:
        thinking = "on" if args.planner_thinking else "off"
        verbose = "off" if args.quiet_planner else "on"
        print(
            f"[llm] 闭环已开启 text={args.llm_model!r} vl={args.vl_model!r} "
            f"verify={'on' if not args.no_verify else 'off'} "
            f"adapt_rounds={args.max_adapt_rounds} "
            f"planner_log={verbose} thinking={thinking}",
            flush=True,
        )
        if not args.planner_thinking:
            print(
                "[llm] 意图规划默认 enable_thinking=False（qwen3 加速）；"
                "需看思考链请加 --planner-thinking",
                flush=True,
            )
    else:
        print("[llm] 已关闭（--no-llm，用户描述直通 Florence）", flush=True)

    fg_mod = _load_grounding_module()
    device = fg_mod._resolve_device(args.device)

    trail = TrailController(
        region,
        hold_sec=args.trail_hold,
        enabled=not args.no_trail,
    )

    print(f"[load] Florence-2 model={args.model!r} device={device}", flush=True)
    try:
        processor, model = fg_mod._load_model(args.model, device)
    except Exception as e:
        trail.shutdown()
        print(f"[ERROR] 模型加载失败: {e}", file=sys.stderr)
        print(
            "  修复: python scripts/test_florence2_phrase_grounding.py --download-modelscope --image <任意图>",
            file=sys.stderr,
        )
        return 2

    out_dir = Path(args.out_dir)
    image_path = Path(args.image).expanduser().resolve() if args.image else None
    if image_path and not image_path.is_file():
        trail.shutdown()
        print(f"[ERROR] 图片不存在: {image_path}", file=sys.stderr)
        return 2

    try:
        if args.once:
            ok = _run_one(
                fg_mod,
                processor,
                model,
                device,
                phrase=args.once,
                image_path=image_path,
                monitor=args.monitor,
                dry_run=args.dry_run,
                countdown=args.countdown,
                out_dir=out_dir,
                region=region,
                trail=trail,
                use_llm=not args.no_llm,
                llm_model=args.llm_model,
                vl_model=args.vl_model,
                skip_caption=args.no_caption,
                max_adapt_rounds=args.max_adapt_rounds,
                enable_verify=not args.no_verify,
                planner_verbose=not args.quiet_planner,
                planner_enable_thinking=args.planner_thinking,
            )
            return 0 if ok else 1

        _interactive_loop(fg_mod, processor, model, device, args, region, trail)
        return 0
    finally:
        trail.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
