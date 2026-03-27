"""
HR 透析镜 MCP 工具：根据岗位要求分析简历，输出 Markdown 报告。

包装 Wasm 技能 com.jachin.hr.analyzer4，供 Agent 以 MCP 工具形式调用。
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _hr_stderr_quiet() -> bool:
    return os.environ.get("HR_ANALYZER_QUIET", "").strip().lower() in ("1", "true", "yes")


def _hr_log(msg: str) -> None:
    if _hr_stderr_quiet():
        return
    try:
        print(msg, file=sys.stderr, flush=True)
    except Exception:
        pass


def _hr_max_collect_files() -> int:
    try:
        return max(1, min(500, int(os.environ.get("HR_ANALYZER_MAX_FILES", "200"))))
    except ValueError:
        return 200

HR_SKILL_ID = "jpp:com.jachin.hr.analyzer4"


def hr_analyze_resume(
    target_dir: str,
    jd_template: str,
    target_role: str = "backend_engineer",
    focus_keywords: str = "",
    strictness: str = "standard",
    output_dir: str = "",
) -> str:
    """
    根据岗位 JD 分析指定目录下的简历，输出 Markdown 评估报告。

    Args:
        target_dir: 简历目录（多为 …/hr_recruitment/<岗位>/pending）；若 pending 为空会自动在同职位下
            processed、副本 等目录中 rglob 查找。
        jd_template: 岗位 JD 全文
        target_role: 目标角色（如 backend_engineer）
        focus_keywords: 重点关注关键词，逗号分隔
        strictness: 严格程度 standard|strict|relaxed
        output_dir: 输出目录，空则使用技能配置默认值

    多文件默认：**每份简历一次 Wasm → 一次大模型 → 落盘一份 `{{stem}}_analysis.md`**，stderr 会打印进度（设 HR_ANALYZER_QUIET=1 可关）。
    单次批量（易在第 N 份 trap）仅当 `HR_ANALYZER_BATCH_WASM=1`。简历数量上限见 `HR_ANALYZER_MAX_FILES`（默认 200）。

    Returns:
        分析报告文本，失败时返回错误信息
    """
    try:
        from l3_node.skills import run_tool
    except ImportError as e:
        logger.warning("[hr_analyze_resume] 无法导入 run_tool: %s", e)
        return f"错误：L3 技能加载器不可用，{e}"

    target_dir = (target_dir or "").strip() or "hr_resumes"
    jd_template = (jd_template or "").strip()
    if not jd_template:
        return "错误：jd_template 不能为空，请传入岗位 JD"

    # 解析目录 + pending 空则读 processed/副本（rglob，绝对路径喂给 Wasm）
    try:
        base = Path(target_dir)
        if not base.is_absolute():
            # 相对路径：仅解析到 ~/.jachin/workspace/...，禁止落到项目仓库目录
            try:
                from .config import get_data_root, get_resume_root

                td_norm = target_dir.replace("\\", "/").strip().strip("/")
                if td_norm in ("hr_resumes", "data/hr_resumes") or "hr_resumes" in td_norm:
                    base = get_resume_root()
                elif "/" in target_dir or "\\" in target_dir:
                    base = get_data_root() / td_norm
                else:
                    base = get_data_root() / target_dir
            except ImportError:
                jroot = Path(os.environ.get("JACHIN_HOME", str(Path.home() / ".jachin"))).expanduser().resolve()
                custom = os.environ.get("JACHIN_HR_DATA_ROOT", "").strip()
                if custom:
                    p = Path(custom).expanduser().resolve()
                    hr_root = p if p.is_absolute() else (jroot / p)
                else:
                    hr_root = jroot / "workspace" / "hr_recruitment"
                rcustom = os.environ.get("JACHIN_HR_RESUME_ROOT", "").strip()
                if rcustom:
                    rp = Path(rcustom).expanduser().resolve()
                    resume_root = rp if rp.is_absolute() else (jroot / rp)
                else:
                    resume_root = jroot / "workspace" / "hr_resumes"
                td_norm = target_dir.replace("\\", "/").strip().strip("/")
                if td_norm in ("hr_resumes", "data/hr_resumes") or "hr_resumes" in td_norm:
                    base = resume_root
                elif "/" in target_dir or "\\" in target_dir:
                    base = hr_root / td_norm
                else:
                    base = hr_root / target_dir
        if not base.exists() or not base.is_dir():
            return f"错误：简历目录不存在 {base}"

        from .hr_data_paths import collect_resume_paths_for_analysis

        files, anchor = collect_resume_paths_for_analysis(primary_dir=base, max_files=_hr_max_collect_files())
        paths_str = "|||".join(str(f.resolve()).replace("\\", "/") for f in files)
        if not paths_str:
            return (
                f"错误：在 {base} 及同职位 processed/副本 等目录下未找到简历"
                f"（支持 .md/.pdf/.docx/.txt）"
            )
        base = anchor
    except Exception as e:
        logger.warning("[hr_analyze_resume] 解析目录失败: %s", e)
        return f"错误：解析简历目录失败，{e}"

    force_batch = os.environ.get("HR_ANALYZER_BATCH_WASM", "").strip().lower() in ("1", "true", "yes")
    env_seq = os.environ.get("HR_ANALYZER_SEQUENTIAL_WASM", "").strip().lower() in ("1", "true", "yes")
    # 多文件且未强制批量：逐份 Wasm，降低单实例内 NDJSON/堆压力与 table OOB
    use_sequential = env_seq or (not force_batch and len(files) > 1)

    def _build_input(paths_fragment: str) -> dict[str, Any]:
        data: dict[str, Any] = {
            "target_dir": str(base.resolve()),
            "_hr_files": paths_fragment,
            "jd_template": jd_template,
            "strictness": (strictness or "standard").strip(),
            "target_role": (target_role or "backend_engineer").strip(),
        }
        if focus_keywords and str(focus_keywords).strip():
            data["focus_keywords"] = str(focus_keywords).strip()
        if output_dir and str(output_dir).strip():
            data["output_dir"] = str(output_dir).strip()
        return data

    def _one_wasm(paths_fragment: str) -> str:
        inp = json.dumps({**_build_input(paths_fragment), "capability": "execute"}, ensure_ascii=False)
        return run_tool(HR_SKILL_ID, inp, allowed_skills=None) or ""

    try:
        if use_sequential and len(files) > 1:
            out_p = (Path(output_dir).expanduser().resolve() if (output_dir or "").strip() else None)
            last_ok = ""
            _hr_log(f"[HR 透析] 逐份模式：共 {len(files)} 份简历，每份独立 Wasm + 落盘（HR_ANALYZER_BATCH_WASM=1 可改回单次批量）")
            for i, f in enumerate(files, 1):
                one = str(f.resolve()).replace("\\", "/")
                stem = f.stem
                logger.info("[hr_analyze_resume] sequential wasm %d/%d path=%s", i, len(files), one)
                _hr_log(f"[HR 透析] ── ({i}/{len(files)}) 开始: {f.name}")
                t0 = time.perf_counter()
                r = _one_wasm(one)
                dt = time.perf_counter() - t0
                rs = (r or "").strip()
                md_sz = -1
                if out_p and out_p.is_dir():
                    cand = out_p / f"{stem}_analysis.md"
                    if cand.is_file():
                        try:
                            md_sz = cand.stat().st_size
                        except OSError:
                            md_sz = -1
                if rs.startswith("错误") or rs.startswith("[Wasm"):
                    _hr_log(f"[HR 透析] ── ({i}/{len(files)}) 失败 耗时 {dt:.1f}s → {f.name}")
                    return (
                        f"⚠️ 第 {i}/{len(files)} 份分析失败（前 {i - 1} 份若已成功应已写入 output_dir）。\n{r[:2000]}"
                    )
                _hr_log(
                    f"[HR 透析] ── ({i}/{len(files)}) 完成 耗时 {dt:.1f}s 落盘 "
                    f"{stem}_analysis.md ({md_sz if md_sz >= 0 else '?' } bytes)"
                )
                last_ok = r
            tail = (last_ok or "")[:1800]
            return (
                f"✅ 顺序完成 {len(files)} 份简历（每份独立 Wasm），报告已写入 output_dir。\n"
                f"最后一次工具返回摘要：\n{tail}{'…' if len(last_ok or '') > 1800 else ''}"
            )

        if len(files) == 1:
            _hr_log(f"[HR 透析] 单份模式：{files[0].name}")
        inp = json.dumps({**_build_input(paths_str), "capability": "execute"}, ensure_ascii=False)
        t0 = time.perf_counter()
        result = run_tool(HR_SKILL_ID, inp, allowed_skills=None)
        _hr_log(f"[HR 透析] 单/批量一次 Wasm 完成 耗时 {time.perf_counter() - t0:.1f}s")
        return result or "分析完成，无输出"
    except Exception as e:
        logger.exception("[hr_analyze_resume] 执行失败")
        return f"错误：透析镜执行失败，{e}"
