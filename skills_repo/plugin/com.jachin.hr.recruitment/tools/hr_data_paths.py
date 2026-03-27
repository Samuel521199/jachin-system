"""
HR 插件统一数据路径 - 按职位分目录存储（全部在 ~/.jachin/workspace/hr_recruitment 下，不写项目目录）

根目录: config.get_data_root()，默认 ~/.jachin/workspace/hr_recruitment/
结构: {职位城市薪资文件夹}/（由 Boss 选岗 canonical 行派生，如 ``Python工程师_杭州_15-25K``，避免「Java 功能工程师」与「Java 开发」混用同一目录）
  - pending/      刚抓取未对比的简历 PDF
  - processed/    对比完成的简历
  - result/       每个简历的 AI 分析报告 (*_analysis.md)
  - jd.json       专属该职位的 JD 配置（含 ``show_in_hr_briefing``：简报是否列出本岗，缺省 true）
  - 排行榜_Summary.md
"""
from __future__ import annotations

import json
import logging
import re
import shutil
from pathlib import Path

from .boss_utils import (
    canonicalize_boss_job_select,
    primary_job_title_from_boss_select_line,
    strip_leading_recruitment_verbs_for_job_chat,
)
from .config import get_data_root, get_plugin_package_root

logger = logging.getLogger(__name__)

PLUGIN_DATA_ROOT = get_data_root()

# 透析镜/分析列举简历时子目录顺序：pending → processed → 副本（pending 无文件时读备份目录）
HR_RESUME_SCAN_SUBDIRS: tuple[str, ...] = ("pending", "processed", "副本")

DEFAULT_RESUME_ANALYZE_EXTS: frozenset[str] = frozenset({".pdf", ".md", ".docx", ".txt"})

# JD 模板：包内或 data 根下
_JD_TEMPLATE_CANDIDATES = [
    get_plugin_package_root() / "data" / "jd_to_publish.example.json",
    PLUGIN_DATA_ROOT / "jd_to_publish.example.json",
]


def _get_jd_template_path() -> Path:
    for p in _JD_TEMPLATE_CANDIDATES:
        if p.exists():
            return p
    return _JD_TEMPLATE_CANDIDATES[0]


def resolve_hr_job_root_from_path(path: Path) -> Path | None:
    """
    从 pending/processed/副本 或其子路径反推「职位根」目录（含 pending 子目录的那一层）。
    非标准布局（如扁平 hr_resumes）返回 None。
    """
    try:
        p = path.resolve()
    except OSError:
        return None
    if p.is_file():
        p = p.parent
    cur: Path = p
    for _ in range(12):
        if cur.name != "pending" and (cur / "pending").is_dir():
            return cur
        if cur.name in HR_RESUME_SCAN_SUBDIRS:
            par = cur.parent
            if par == cur:
                break
            for x in HR_RESUME_SCAN_SUBDIRS:
                if (par / x).is_dir():
                    return par
        nxt = cur.parent
        if nxt == cur:
            break
        cur = nxt
    return None


def collect_resume_paths_for_analysis(
    *,
    primary_dir: Path,
    max_files: int = 50,
    extensions: frozenset[str] | None = None,
) -> tuple[list[Path], Path]:
    """
    按 pending → processed → 副本 顺序 rglob 简历文件（去重），避免仅盯 pending 导致「找不到简历」。

    Returns:
        (绝对路径列表, target_dir 建议值) — 建议 target_dir 为职位根，便于与绝对路径 _hr_files 搭配。
    """
    exts = extensions or DEFAULT_RESUME_ANALYZE_EXTS
    try:
        primary = primary_dir.resolve()
    except OSError:
        return [], primary_dir

    job_root = resolve_hr_job_root_from_path(primary)

    scan_dirs: list[Path] = []
    seen_dir: set[str] = set()

    def add_scan(d: Path) -> None:
        try:
            dr = d.resolve()
        except OSError:
            return
        if not dr.is_dir():
            return
        k = str(dr)
        if k in seen_dir:
            return
        seen_dir.add(k)
        scan_dirs.append(dr)

    if job_root:
        jr = job_root.resolve()
        for name in HR_RESUME_SCAN_SUBDIRS:
            add_scan(jr / name)
    else:
        add_scan(primary)

    pending_dir = (job_root / "pending").resolve() if job_root else primary
    seen_file: set[str] = set()
    files: list[Path] = []

    for d in scan_dirs:
        if not d.is_dir():
            continue
        try:
            for f in sorted(d.rglob("*")):
                if len(files) >= max_files:
                    break
                if not f.is_file():
                    continue
                if f.suffix.lower() not in exts:
                    continue
                k = str(f.resolve())
                if k in seen_file:
                    continue
                seen_file.add(k)
                files.append(Path(k))
        except OSError as e:
            logger.debug("[HR] 扫描简历目录跳过 %s: %s", d, e)
        if len(files) >= max_files:
            break

    if files and job_root:
        n_under_pending = 0
        for f in files:
            try:
                f.resolve().relative_to(pending_dir)
                n_under_pending += 1
            except ValueError:
                pass
        if n_under_pending == 0:
            logger.info(
                "[HR] pending 中无可用简历，已从 processed/副本 等目录检索 %d 份用于分析",
                len(files),
            )
        elif n_under_pending < len(files):
            logger.info(
                "[HR] 已合并 pending 与其它目录简历，共 %d 份（其中 pending 内 %d 份）",
                len(files),
                n_under_pending,
            )

    anchor = job_root.resolve() if job_root else primary
    return files, anchor


def sanitize_job_folder(job_name: str, max_len: int = 96) -> str:
    """将岗位名或数据目录键转为安全文件夹名（加长以容纳 职位+城市+薪资）。"""
    illegal = r'\/:*?"<>|'
    for c in illegal:
        job_name = job_name.replace(c, "_")
    s = "".join(c if c.isalnum() or c in " _-（）【】" else "_" for c in job_name)
    return s.strip("_")[:max_len] or "未分类"


def hr_data_folder_key_from_canonical_jd_select(canon: str, max_len: int = 96) -> str:
    """
    由 Boss 选岗 canonical 行生成**唯一**数据目录键（职位 + 城市 + 薪资），
    避免仅按职位名目录导致不同城市/薪资互相覆盖。
    """
    c = (canon or "").strip()
    if not c or " _ " not in c:
        return ""
    left, right = c.split(" _ ", 1)
    left_c = re.sub(r"\s+", "", (left or "").strip())
    right_c = re.sub(r"\s+", "", (right or "").strip())
    if not left_c or not right_c:
        return ""
    raw = f"{left_c}_{right_c}"
    return sanitize_job_folder(raw, max_len=max_len)


def resolve_recruitment_data_folder_key(
    *,
    jd_select_canon: str = "",
    job_title: str = "",
    jd_doc: dict | None = None,
) -> str:
    """
    解析应用作 ``hr_recruitment/{key}/`` 的目录键（**仅**「职位 + 地区 + 薪资」派生键）。

    依据完整 Boss 选岗行 ``jd_select``（须含 `` _ `` 与城市/薪资段）或 ``job_title`` + ``job_location``
    + ``salary_min``/``salary_max`` 拼出的 canonical 行。**不再**退回「纯职位名」目录，避免
    ``Python 工程师`` 与 ``Python工程师_杭州15-25K`` 分裂。
    """
    c = (jd_select_canon or "").strip()
    doc = jd_doc if isinstance(jd_doc, dict) else None
    if not c and doc:
        sel0 = strip_leading_recruitment_verbs_for_job_chat((doc.get("jd_select") or "").strip())
        c = (canonicalize_boss_job_select(sel0) or sel0).strip()
    if (not c or " _ " not in c) and doc:
        t = strip_leading_recruitment_verbs_for_job_chat(
            (doc.get("job_title") or job_title or "").strip()
        )
        city = (doc.get("job_location") or "杭州").strip()
        sm, sx = doc.get("salary_min"), doc.get("salary_max")
        if t and sm is not None and sx is not None:
            c = canonicalize_boss_job_select(f"{t} _ {city} {int(sm)}-{int(sx)}K") or ""
        elif t and sm is not None:
            c = canonicalize_boss_job_select(f"{t} _ {city} {int(sm)}K") or ""
    fk = hr_data_folder_key_from_canonical_jd_select(c) if c and " _ " in c else ""
    if fk:
        return fk
    return ""


def discover_jd_json_paths_for_job_title(job_title: str) -> list[Path]:
    """
    在 ``PLUGIN_DATA_ROOT`` 下扫描各子目录的 ``jd.json``，按 ``job_title`` / ``jd_select`` 左侧职位段
    与 ``job_title`` 参数匹配。用于仅有显示名、须唯一定位目录时的辅助（多岗同名则返回多条，由调用方消歧）。
    """
    want = strip_leading_recruitment_verbs_for_job_chat((job_title or "").strip())
    if not want:
        return []
    out: list[Path] = []
    root = PLUGIN_DATA_ROOT
    if not root.is_dir():
        return out
    try:
        for sub in sorted(root.iterdir(), key=lambda p: p.name):
            if not sub.is_dir() or sub.name.startswith("."):
                continue
            jp = sub / "jd.json"
            if not jp.is_file():
                continue
            try:
                doc = json.loads(jp.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(doc, dict):
                continue
            jt = strip_leading_recruitment_verbs_for_job_chat((doc.get("job_title") or "").strip())
            sel = (doc.get("jd_select") or "").strip()
            left = primary_job_title_from_boss_select_line(sel) if sel else ""
            left = strip_leading_recruitment_verbs_for_job_chat(left) if left else ""
            if jt == want or left == want:
                out.append(jp)
    except OSError as e:
        logger.debug("discover_jd_json_paths_for_job_title 扫描失败: %s", e)
    return out


def infer_folder_key_from_job_display_name(job_title: str, jd_doc: dict | None = None) -> str:
    """先 ``resolve_recruitment_data_folder_key``，失败则唯一 jd.json 命中时取其父目录名。"""
    fk = resolve_recruitment_data_folder_key(
        job_title=(job_title or "").strip(),
        jd_doc=jd_doc if isinstance(jd_doc, dict) else None,
    )
    if fk:
        return fk
    cands = discover_jd_json_paths_for_job_title(job_title)
    if len(cands) == 1:
        return sanitize_job_folder(cands[0].parent.name)
    return ""


def get_job_dir_by_folder_key(folder_key: str) -> Path:
    return PLUGIN_DATA_ROOT / sanitize_job_folder((folder_key or "").strip() or "未分类")


def get_job_jd_path_by_folder_key(folder_key: str) -> Path:
    return get_job_dir_by_folder_key(folder_key) / "jd.json"


def ensure_job_dirs_by_folder_key(folder_key: str) -> Path:
    base = get_job_dir_by_folder_key(folder_key)
    (base / "pending").mkdir(parents=True, exist_ok=True)
    (base / "processed").mkdir(parents=True, exist_ok=True)
    (base / "result").mkdir(parents=True, exist_ok=True)
    return base


def delete_all_hr_position_directories() -> tuple[int, list[str]]:
    """
    删除 ``PLUGIN_DATA_ROOT`` 下所有岗位子目录（pending/jd.json 等一并删除）。
    供「清除全部岗位记忆」与彻底重置使用；调用方须已停止调度器任务。
    """
    root = PLUGIN_DATA_ROOT
    removed: list[str] = []
    if not root.is_dir():
        return 0, removed
    for sub in list(root.iterdir()):
        try:
            if sub.is_dir() and not sub.name.startswith("."):
                shutil.rmtree(sub, ignore_errors=True)
                removed.append(sub.name)
        except OSError as e:
            logger.warning("删除岗位目录失败 %s: %s", sub, e)
    if removed:
        logger.info("[HR] 已删除 %d 个岗位数据目录: %s", len(removed), removed[:12])
    return len(removed), removed


def get_job_dir(job_name: str, jd_doc: dict | None = None) -> Path:
    """职位根目录；目录键由 ``infer_folder_key_from_job_display_name`` 解析（非纯标题路径）。"""
    fk = infer_folder_key_from_job_display_name(job_name, jd_doc=jd_doc)
    if fk:
        return get_job_dir_by_folder_key(fk)
    return PLUGIN_DATA_ROOT / ".unresolved_job" / sanitize_job_folder(job_name or "未命名")


def get_job_jd_path(job_name: str, jd_doc: dict | None = None) -> Path:
    """``jd.json`` 路径；须能解析出「职位+地区+薪资」键或磁盘上唯一匹配，否则落在不存在的占位路径。"""
    fk = infer_folder_key_from_job_display_name(job_name, jd_doc=jd_doc)
    if fk:
        return get_job_jd_path_by_folder_key(fk)
    return PLUGIN_DATA_ROOT / ".unresolved_job" / sanitize_job_folder(job_name or "未命名") / "jd.json"


def get_job_pending_dir(job_name: str, jd_doc: dict | None = None) -> Path:
    """待对比简历目录（会 mkdir）。"""
    d = get_job_dir(job_name, jd_doc=jd_doc) / "pending"
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_job_processed_dir(job_name: str, jd_doc: dict | None = None) -> Path:
    """已对比简历 hr_recruitment/{job_folder}/processed/"""
    d = get_job_dir(job_name, jd_doc=jd_doc) / "processed"
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_job_result_dir(job_name: str, jd_doc: dict | None = None) -> Path:
    """AI 分析报告 hr_recruitment/{job_folder}/result/"""
    d = get_job_dir(job_name, jd_doc=jd_doc) / "result"
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_job_summary_md_path(job_name: str, jd_doc: dict | None = None) -> Path:
    """职位专属排行榜 MD hr_recruitment/{job_folder}/排行榜_Summary.md"""
    return get_job_dir(job_name, jd_doc=jd_doc) / "排行榜_Summary.md"


def ensure_job_dirs(job_name: str, jd_doc: dict | None = None) -> Path:
    """创建职位目录结构，返回职位根目录（须能解析出数据目录键）。"""
    base = get_job_dir(job_name, jd_doc=jd_doc)
    (base / "pending").mkdir(parents=True, exist_ok=True)
    (base / "processed").mkdir(parents=True, exist_ok=True)
    (base / "result").mkdir(parents=True, exist_ok=True)
    return base


# 飞书「招聘助手」简报 L3：是否在列表中展示该岗位（清除记忆时写 false；绑定/换岗写 true；缺省 true）
JD_JSON_KEY_SHOW_IN_HR_BRIEFING = "show_in_hr_briefing"


def jd_show_in_hr_briefing(doc: dict | None) -> bool:
    """
    jd.json 是否应在 HR 状态简报里作为「其他岗位」列出。
    缺省 True；显式 ``false`` 时不展示（例如已执行「清除岗位记忆」）。
    """
    if not doc or not isinstance(doc, dict):
        return True
    v = doc.get(JD_JSON_KEY_SHOW_IN_HR_BRIEFING)
    if v is None:
        return True
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() not in ("0", "false", "no", "否", "关", "off")
    if isinstance(v, (int, float)):
        return int(v) != 0
    return bool(v)


def write_jd_show_in_hr_briefing(jd_path: Path, show: bool) -> bool:
    """合并写入 ``show_in_hr_briefing``。若文件不存在返回 False。"""
    p = Path(jd_path)
    if not p.is_file():
        return False
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(doc, dict):
            doc = {}
        doc[JD_JSON_KEY_SHOW_IN_HR_BRIEFING] = bool(show)
        p.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    except Exception as e:
        logger.warning("write_jd_show_in_hr_briefing 失败 %s: %s", p, e)
        return False


def set_jd_show_in_hr_briefing_for_job_folder(job_folder: str, show: bool) -> bool:
    """按职位目录名（sanitize 后文件夹名）写 jd.json 展示开关。"""
    jf = sanitize_job_folder((job_folder or "").strip())
    if not jf or jf == "未分类":
        return False
    return write_jd_show_in_hr_briefing(PLUGIN_DATA_ROOT / jf / "jd.json", show)


def set_all_jd_show_in_hr_briefing(show: bool) -> int:
    """扫描 hr_recruitment 根下各子目录的 jd.json，批量写入展示开关。返回成功写入的文件数。"""
    root = PLUGIN_DATA_ROOT
    n = 0
    if not root.is_dir():
        return 0
    try:
        for sub in root.iterdir():
            if not sub.is_dir():
                continue
            if write_jd_show_in_hr_briefing(sub / "jd.json", show):
                n += 1
    except OSError as e:
        logger.warning("set_all_jd_show_in_hr_briefing 扫描失败: %s", e)
    return n


def repair_jd_identity_dict(doc: dict) -> tuple[dict, bool]:
    """
    纠正 jd.json 中 job_title / jd_select 的「抓取」等动词前缀，并 canonicalize 选岗行、对齐 job_title。
    返回 (新 dict, 是否相对入参有改动)。
    """
    from .boss_utils import (
        canonicalize_boss_job_select,
        primary_job_title_from_boss_select_line,
        strip_leading_recruitment_verbs_for_job_chat,
    )

    if not isinstance(doc, dict):
        return {}, False
    d = dict(doc)
    changed = False
    t0 = (d.get("job_title") or "").strip()
    t = strip_leading_recruitment_verbs_for_job_chat(t0)
    if t != t0:
        d["job_title"] = t
        changed = True

    sel_raw = (d.get("jd_select") or "").strip()
    if sel_raw:
        ss = strip_leading_recruitment_verbs_for_job_chat(sel_raw)
        cs = canonicalize_boss_job_select(ss) or ss
        if cs != (d.get("jd_select") or "").strip():
            d["jd_select"] = cs
            changed = True
        left = primary_job_title_from_boss_select_line(cs)
        if left and left != (d.get("job_title") or "").strip():
            d["job_title"] = left
            changed = True
    return d, changed


def repair_jd_identity_dict_and_persist(jd_path: Path) -> bool:
    """读 jd.json，必要时修复身份字段并写回。文件不存在或失败返回 False。"""
    p = Path(jd_path)
    if not p.is_file():
        return False
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return False
        fixed, changed = repair_jd_identity_dict(raw)
        if changed:
            p.write_text(json.dumps(fixed, ensure_ascii=False, indent=2), encoding="utf-8")
            logger.info("[HR] 已修复 jd 身份字段: %s", p)
        return changed
    except Exception as e:
        logger.debug("repair_jd_identity_dict_and_persist 跳过: %s", e)
        return False


def jd_boss_post_marked_published(jd: dict | None) -> bool:
    """jd.json 是否已记录「Boss 侧职位已发布成功」，用于禁止默认重复发帖。"""
    if not jd or not isinstance(jd, dict):
        return False
    v = jd.get("boss_post_published")
    if v is True:
        return True
    if isinstance(v, str) and v.strip().lower() in ("1", "true", "yes", "是"):
        return True
    if isinstance(v, (int, float)) and int(v) == 1:
        return True
    return False


def mark_jd_boss_post_published(jd_path: Path) -> None:
    """在 jd.json 写入 boss_post_published 与时间戳。"""
    import time

    p = Path(jd_path)
    if not p.exists():
        return
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(doc, dict):
            doc = {}
        doc["boss_post_published"] = True
        doc["boss_post_published_at"] = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())
        p.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("已标记 jd.json boss_post_published: %s", p)
    except Exception as e:
        logger.warning("mark_jd_boss_post_published 失败: %s", e)


def clear_jd_boss_post_published_flag(jd_path: Path) -> None:
    """HR 明确要求重新发帖（force_republish）时清除标记。"""
    p = Path(jd_path)
    if not p.exists():
        return
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(doc, dict):
            return
        doc.pop("boss_post_published", None)
        doc.pop("boss_post_published_at", None)
        p.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning("clear_jd_boss_post_published_flag 失败: %s", e)


def init_job_jd_from_template(
    job_name: str, overrides: dict | None = None, *, data_folder_key: str = ""
) -> Path:
    """
    HR 确认后自动执行：写入 ``hr_recruitment/{数据目录键}/jd.json``。

    目录键**仅**来自 ``data_folder_key``（若与 jd 内容推导一致）或 ``resolve_recruitment_data_folder_key``
   （``jd_select`` / 城市 / 薪资），**不再**使用「纯职位名」文件夹。

    若该文件已存在，在**现有内容**上合并 overrides；不存在时才从模板复制。
    """
    ov_in = dict(overrides) if isinstance(overrides, dict) else {}
    ov_in.pop("data_folder_key", None)

    key_explicit = (data_folder_key or "").strip()
    preview: dict = {}
    template = _get_jd_template_path()
    if template.exists():
        try:
            raw_t = json.loads(template.read_text(encoding="utf-8"))
            if isinstance(raw_t, dict):
                preview = dict(raw_t)
        except Exception:
            preview = {}
    preview.pop("_comment", None)
    for k, v in ov_in.items():
        if v is not None and k not in ("_comment", "force_republish", "skip_boss_post"):
            preview[k] = v
    if not preview.get("job_title") and job_name:
        preview["job_title"] = job_name
    if not preview.get("jd_select") and preview.get("job_title"):
        title = (preview.get("job_title") or "").strip()
        city = (preview.get("job_location") or "杭州").strip()
        sal_min, sal_max = preview.get("salary_min"), preview.get("salary_max")
        if sal_min is not None and sal_max is not None:
            preview["jd_select"] = canonicalize_boss_job_select(
                f"{title} _ {city} {int(sal_min)}-{int(sal_max)}K"
            )
        elif sal_min is not None:
            preview["jd_select"] = canonicalize_boss_job_select(f"{title} _ {city} {int(sal_min)}K")

    key_resolved = resolve_recruitment_data_folder_key(
        job_title=(preview.get("job_title") or job_name or "").strip(),
        jd_doc=preview,
    )
    if key_explicit and key_resolved and key_explicit != key_resolved:
        logger.warning(
            "[HR] data_folder_key=%r 与 jd 推导目录键 %r 不一致，以推导为准",
            key_explicit,
            key_resolved,
        )
    if key_resolved:
        key = key_resolved
    elif key_explicit:
        key = key_explicit
    else:
        msg = (
            "无法解析岗位数据目录键：请在 jd 中提供完整 jd_select（含「职位 _ 城市 薪资」）或 "
            "job_title + job_location + salary_min/salary_max"
        )
        logger.error("[HR] init_job_jd_from_template: %s job_name=%r", msg, job_name)
        raise ValueError(msg)

    base = get_job_dir_by_folder_key(key)
    base.mkdir(parents=True, exist_ok=True)
    (base / "pending").mkdir(parents=True, exist_ok=True)
    (base / "processed").mkdir(parents=True, exist_ok=True)
    (base / "result").mkdir(parents=True, exist_ok=True)

    jd_path = base / "jd.json"
    base_cfg: dict = {}
    if jd_path.exists():
        try:
            raw = json.loads(jd_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                base_cfg = raw
        except Exception as e:
            logger.warning("读取已有 jd.json 失败，将按模板新建: %s", e)
    if not base_cfg:
        if template.exists():
            shutil.copy2(template, jd_path)
            base_cfg = json.loads(jd_path.read_text(encoding="utf-8"))
            if isinstance(base_cfg, dict) and "_comment" in base_cfg:
                base_cfg.pop("_comment", None)
        else:
            base_cfg = {}

    merged = {**base_cfg}
    for k, v in ov_in.items():
        if v is not None and k not in ("_comment", "force_republish", "skip_boss_post"):
            merged[k] = v
    if not merged.get("job_title") and job_name:
        merged["job_title"] = job_name
    if not merged.get("jd_select") and merged.get("job_title"):
        title = (merged.get("job_title") or "").strip()
        city = (merged.get("job_location") or "杭州").strip()
        sal_min, sal_max = merged.get("salary_min"), merged.get("salary_max")
        if sal_min is not None and sal_max is not None:
            merged["jd_select"] = canonicalize_boss_job_select(
                f"{title} _ {city} {int(sal_min)}-{int(sal_max)}K"
            )
        elif sal_min is not None:
            merged["jd_select"] = canonicalize_boss_job_select(f"{title} _ {city} {int(sal_min)}K")

    merged.setdefault(JD_JSON_KEY_SHOW_IN_HR_BRIEFING, True)
    if ov_in and "enable_greet_recommend" not in ov_in:
        merged["enable_greet_recommend"] = True
    merged.setdefault("enable_greet_recommend", True)
    merged["data_folder_key"] = sanitize_job_folder(key)

    jd_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    summary_md = base / "排行榜_Summary.md"
    if not summary_md.exists():
        summary_md.write_text(
            f"# {job_name} - AI 招聘决断排行榜\n\n待分析完成后更新。\n",
            encoding="utf-8",
        )
    return jd_path
