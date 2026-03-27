#!/usr/bin/env python3
"""测试 L3 本地 boss_harvester / 收网逻辑（直接调用，不经过 MCP）。

前置：
  - Chrome 以远程调试启动，例如：chrome.exe --remote-debugging-port=9222
  - 已登录 Boss，并打开「沟通」页

示例（Python 岗位、只处理 5 个会话）：
  python scripts/test_boss_harvester_l3_local.py \\
    --job "python工程师_杭州 15-25K" --max 5

说明：job 须与 Boss 左侧「全部职位」下拉中展示文案尽量一致（可含下划线/空格差异）。

日志：关键步骤均打 INFO；加 -v 可打开 atom_inbox / boss_utils 的 DEBUG。
超时：默认整段收网最多等待 --timeout-sec，超时后仍打印 JSON 摘要并退出（避免无限卡住）。
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import threading
import traceback
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

# Windows 控制台 UTF-8 输出
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent.parent
PLUGIN_MCP = ROOT / "skills_repo" / "plugin" / "com.jachin.hr.recruitment"
L3_VOLUME_ROOT = Path.home() / ".jachin" / "client_volumes"

_LOG = logging.getLogger("test_boss_harvester")


def _setup_logging(*, verbose: bool, log_file: str | None) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    if log_file:
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    logging.basicConfig(level=level, format=fmt, datefmt=datefmt, handlers=handlers, force=True)
    if verbose:
        for name in (
            "tools.atom_inbox_harvester",
            "tools.boss_utils",
            "tools.human_utils",
            "tools.atom_request_resume",
        ):
            logging.getLogger(name).setLevel(logging.DEBUG)
    else:
        logging.getLogger("urllib3").setLevel(logging.WARNING)


def _cdp_preflight(cdp_url: str, timeout_sec: float = 3.0) -> tuple[bool, str]:
    """探测 CDP 是否可连（不保证已打开 Boss 页）。"""
    base = (cdp_url or "").strip().rstrip("/")
    if not base:
        return False, "cdp_url 为空"
    version_url = f"{base}/json/version"
    try:
        with urllib.request.urlopen(version_url, timeout=timeout_sec) as resp:
            raw = resp.read(800).decode("utf-8", errors="replace")
        return True, raw[:200] if raw else "(empty body)"
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}: {e.reason}"
    except urllib.error.URLError as e:
        return False, f"网络/连接失败: {e.reason!s}"
    except Exception as e:
        return False, str(e)


def _run_harvest_in_thread(
    *,
    cdp_url: str,
    job_text: str,
    max_items: int,
    save_dir: str,
    request_if_no_resume: bool,
    use_all_positions: bool,
    out_box: dict[str, Any],
    err_box: dict[str, Any],
) -> None:
    """在子线程中执行收网，结果写入 out_box['result'] 或 err_box。"""
    try:
        _LOG.info(
            "[步骤-收网] 开始 atom_inbox_harvester_full_flow: cdp=%s job=%r max_items=%d save_dir=%s use_all_positions=%s request_if_no_resume=%s",
            cdp_url,
            job_text,
            max_items,
            save_dir,
            use_all_positions,
            request_if_no_resume,
        )
        from tools.atom_inbox_harvester import atom_inbox_harvester_full_flow

        r = atom_inbox_harvester_full_flow(
            cdp_url=cdp_url,
            job_text=job_text,
            max_items=max_items,
            save_dir=save_dir,
            filter_tab="全部",
            request_if_no_resume=request_if_no_resume,
            use_all_positions=use_all_positions,
        )
        out_box["result"] = r
        _LOG.info(
            "[步骤-收网] 结束: success=%s downloaded=%s requested=%s error=%r",
            r.get("success"),
            r.get("downloaded"),
            r.get("requested_count"),
            (r.get("error") or "")[:300],
        )
    except Exception as e:
        err_box["exc"] = e
        err_box["tb"] = traceback.format_exc()
        _LOG.exception("[步骤-收网] 未捕获异常: %s", e)


def main() -> int:
    p = argparse.ArgumentParser(description="收网：按职位选岗并最多处理 N 个左侧会话（带全程日志与超时）")
    p.add_argument(
        "--job",
        default="python工程师_杭州 15-25K",
        help="与 Boss「全部职位」匹配的文案",
    )
    p.add_argument("--max", type=int, default=5, help="最多处理多少个左侧会话")
    p.add_argument("--cdp", default="http://127.0.0.1:9222", help="Chrome CDP 地址")
    p.add_argument("--no-request", action="store_true", help="无附件简历时不点击求简历")
    p.add_argument(
        "--use-all-positions",
        action="store_true",
        help="选「全部职位」、忽略 --job（仅短时联调）",
    )
    p.add_argument(
        "--timeout-sec",
        type=int,
        default=900,
        help="整段收网最大等待秒数，超时后打印超时结果并退出（默认 900）",
    )
    p.add_argument("-v", "--verbose", action="store_true", help="DEBUG 日志（atom_inbox / boss_utils 等）")
    p.add_argument(
        "--log-file",
        default="",
        help="额外写入日志文件路径（UTF-8）",
    )
    args = p.parse_args()

    log_file = (args.log_file or "").strip() or None
    _setup_logging(verbose=args.verbose, log_file=log_file)

    exit_code = 0
    summary: dict[str, Any] = {
        "test": "boss_harvester_l3_local",
        "phase": "init",
        "timeout_sec": args.timeout_sec,
    }

    _LOG.info("========== 测试开始 ==========")
    _LOG.info("[步骤-1] 解析参数: job=%r max=%d cdp=%s use_all_positions=%s no_request=%s",
              args.job, args.max, args.cdp, args.use_all_positions, args.no_request)

    try:
        _LOG.info("[步骤-2] 工程路径: ROOT=%s PLUGIN_MCP=%s", ROOT, PLUGIN_MCP)
        if not PLUGIN_MCP.is_dir():
            _LOG.error("[步骤-2] 失败: 插件目录不存在: %s", PLUGIN_MCP)
            summary.update({"phase": "error", "error": "plugin_dir_missing", "path": str(PLUGIN_MCP)})
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return 2

        if str(PLUGIN_MCP) not in sys.path:
            sys.path.insert(0, str(PLUGIN_MCP))
            _LOG.info("[步骤-3] 已加入 sys.path: %s", PLUGIN_MCP)
        else:
            _LOG.info("[步骤-3] sys.path 已含插件目录，跳过插入")

        save_dir = L3_VOLUME_ROOT / "global_resume_pool"
        _LOG.info("[步骤-4] 数据目录: %s", save_dir)
        save_dir.mkdir(parents=True, exist_ok=True)
        _LOG.info("[步骤-4] 数据目录已就绪")

        _LOG.info("[步骤-5] CDP 预检: GET %s/json/version", args.cdp.rstrip("/"))
        ok_cdp, cdp_detail = _cdp_preflight(args.cdp)
        if ok_cdp:
            _LOG.info("[步骤-5] CDP 可访问（节选）: %s", cdp_detail.replace("\n", " ")[:180])
        else:
            _LOG.warning("[步骤-5] CDP 预检未通过（仍可继续试连 Playwright）: %s", cdp_detail)

        out_box: dict[str, Any] = {}
        err_box: dict[str, Any] = {}
        t = threading.Thread(
            target=_run_harvest_in_thread,
            kwargs={
                "cdp_url": args.cdp,
                "job_text": args.job,
                "max_items": args.max,
                "save_dir": str(save_dir),
                "request_if_no_resume": not args.no_request,
                "use_all_positions": args.use_all_positions,
                "out_box": out_box,
                "err_box": err_box,
            },
            name="harvest-worker",
            daemon=True,
        )

        _LOG.info("[步骤-6] 启动收网工作线程（超时=%ds）…", args.timeout_sec)
        t.start()
        t.join(timeout=float(args.timeout_sec))

        if t.is_alive():
            _LOG.error(
                "[步骤-7] 超时: 已等待 %s 秒，工作线程仍未结束。请检查 Boss 是否被弹窗挡住、或 Playwright 卡在点击。",
                args.timeout_sec,
            )
            summary.update(
                {
                    "phase": "timeout",
                    "success": False,
                    "error": f"收网超过 {args.timeout_sec} 秒未完成；子线程仍在后台（daemon 会在进程退出时结束）。",
                    "hint": "可先手动关闭「我知道了」等引导，或减小 --max；需要更久可调大 --timeout-sec。",
                }
            )
            exit_code = 3
        elif err_box.get("exc") is not None:
            _LOG.error("[步骤-7] 收网线程异常: %s", err_box.get("exc"))
            _LOG.debug("Traceback:\n%s", err_box.get("tb", ""))
            summary.update(
                {
                    "phase": "exception",
                    "success": False,
                    "error": str(err_box.get("exc")),
                    "traceback": err_box.get("tb", ""),
                }
            )
            exit_code = 2
        else:
            r = out_box.get("result")
            summary["phase"] = "completed"
            summary["harvest"] = r
            if not isinstance(r, dict):
                _LOG.warning("[步骤-7] 返回类型非 dict: %r", type(r))
                exit_code = 2
            else:
                if not r.get("success") and (r.get("error") or "").strip():
                    exit_code = 1
                    _LOG.warning("[步骤-7] 收网 success=False, error=%s", r.get("error"))
                else:
                    _LOG.info("[步骤-7] 收网流程按 Playwright 侧返回结束")

    except KeyboardInterrupt:
        _LOG.warning("[中断] 收到 KeyboardInterrupt，仍输出当前摘要")
        summary.update({"phase": "keyboard_interrupt", "success": False, "error": "KeyboardInterrupt"})
        exit_code = 130
    except Exception as e:
        _LOG.exception("[致命] 测试脚本异常: %s", e)
        summary.update({"phase": "fatal", "success": False, "error": str(e), "traceback": traceback.format_exc()})
        exit_code = 2
    finally:
        _LOG.info("[步骤-8] 最终 JSON 摘要（stdout）→")
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        _LOG.info("========== 测试结束 exit_code=%s ==========", exit_code)

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
