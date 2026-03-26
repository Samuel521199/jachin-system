"""Boss 直聘工具共享：Cookie 加载、导航等（含拟人化等待）"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from .config import _get_jachin_root
from .human_utils import human_wait

logger = logging.getLogger(__name__)

# 顶栏「15-25K / 15～25K / 15–25K」等与 Boss 展示一致（含 en dash、em dash、全角减号）
_BOSS_TOOLBAR_SALARY_K_RE = re.compile(
    r"\d+\s*[-–—~～\u2013\u2014\uFF0D]\s*\d+\s*[KkＫ]",
    re.I,
)

COOKIE_PATHS = [
    Path(__file__).resolve().parent.parent / ".cookie" / "boss_zhipin_cookies.json",
    Path.home() / ".hr_plugin" / "config" / "boss_zhipin_cookies.json",
    _get_jachin_root() / "config" / "boss_zhipin_cookies.json",
]

BOSS_JOB_SEARCH_INPUT_SELECTORS = [
    "input.chat-job-search",
    "input[placeholder*='职位']",
    "input[placeholder*='职位名称']",
    "input[placeholder*='请输入']",
    # 新版推荐/沟通顶栏：下拉展开后输入框可能在列表容器内
    "div[class*='dropmenu-list'] input",
    "div[class*='dropmenu'] input[type='text']",
    "div[class*='boss-drop'] input",
    "[class*='job-search'] input[type='text']",
    "div[class*='select-job'] input",
]
# 禁止裸用 div.ui-dropmenu-label：站内多处复用该类名，易点到右上角头像/个人中心下拉。
BOSS_JOB_TRIGGER_SELECTORS = [
    "input.chat-job-search",
    "span.chat-select-job",
    "div.ui-dropmenu-label span.chat-select-job",
    "div.ui-dropmenu-label:has(span.chat-select-job)",
    ".chat-select-job",
    "[class*='chat-select-job']",
    "div[class*='dropmenu']:has(span.chat-select-job)",
    # 收窄：带「选职位」语义的顶栏区域（避免匹配 header 右侧账号区）
    "[class*='chat-top'] span.chat-select-job",
    "[class*='chat-top'] div.ui-dropmenu-label:has(span.chat-select-job)",
    # 勿用裸 [class*='select-job']：常与右侧「城市/区县」控件 class 撞车导致误点
    "[class*='job-toolbar'] div.ui-dropmenu-label:has(span.chat-select-job)",
]
BOSS_JOB_DROPDOWN_SELECTORS = BOSS_JOB_TRIGGER_SELECTORS

BOSS_CHAT_ITEM_SELECTORS = [
    "div.geek-item",
    ".geek-item",
    "[class*='geek-item']",
]


def load_cookies() -> list[dict[str, Any]]:
    for p in COOKIE_PATHS:
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception as e:
                logger.warning("Load cookies failed %s: %s", p, e)
    return []


def _normalize_job_text(s: str) -> str:
    if not s:
        return ""
    return " ".join(s.split())


def strip_leading_recruitment_verbs_for_job_chat(text: str) -> str:
    """
    去掉句首「抓取/收网/下载…」等动词，避免与职位名粘连成「抓取Python 工程师」。

    例：「抓取Python 工程师 _ 杭州 15-25K，10份」→ 以「Python 工程师 _ …」参与解析。
    """
    t = (text or "").strip()
    if not t:
        return t
    t = re.sub(
        r"^\s*(?:请|帮我|麻烦|辛苦|我要|我想|需要|希望)?\s*(?:去|来|先)?\s*"
        r"(?:抓取|抓一下|抓点|收网下载|收网|下载|帮忙抓|帮忙收)\s*(?:简历|沟通里|沟通)?\s*",
        "",
        t,
        flags=re.I,
    ).strip()
    t = re.sub(r"^\s*抓取\s*", "", t, flags=re.I).strip()
    return t


def strip_trailing_job_reference_noise(text: str) -> str:
    """去掉「…15-25K这个岗位」等尾巴，避免 canonical 薪资段被污染。"""
    t = (text or "").strip()
    if not t:
        return t
    t = re.sub(r"(?:这个|那个)?岗位\s*$", "", t, flags=re.I).strip()
    return t


def strip_hr_recruitment_command_suffix(text: str) -> str:
    """
    去掉飞书常见尾巴：「开始抓取简历」「收网」等，便于解析「python工程师 杭州 15-25k开始抓取」。
    """
    t = (text or "").strip()
    if not t:
        return ""
    prev = None
    while prev != t:
        prev = t
        t = re.sub(
            r"\s*(开始抓取简历|开始抓取|开始收网|开始招聘|抓取简历|抓简历|收网|继续抓取|麻烦抓|帮我抓)\s*$",
            "",
            t,
            flags=re.IGNORECASE,
        ).strip()
    return t


def extract_job_select_line_for_boss_from_hr_chat(text: str) -> str:
    """
    从 HR/飞书整句中提取 Boss「全部职位」下拉匹配用 canonical 文案。
    例：「python工程师 杭州 15-25k开始抓取简历」→ ``python工程师 _ 杭州 15-25K``
    例：「职位大数据高级java开发工程师_杭州 20-35K，抓取10份」→ ``大数据高级java开发工程师 _ 杭州 20-35K``

    说明：``_infer_boss_job_select_from_space_separated`` 在整句含 ``_`` 时会直接放弃；
    此类「职位_城市 薪资」需走 ``canonicalize_boss_job_select`` 或句内子串匹配。
    """
    raw = (text or "").strip()
    if not raw:
        return ""
    t = strip_hr_recruitment_command_suffix(raw)
    t = strip_leading_recruitment_verbs_for_job_chat(t)
    t = strip_trailing_job_reference_noise(t)
    t_trim = re.sub(r"^[，,。\s]*(?:职位|岗位)\s*[:：]?\s*", "", t, flags=re.I).strip()

    inferred = _infer_boss_job_select_from_space_separated(t_trim)
    if inferred:
        return canonicalize_boss_job_select(inferred)

    # 勿对整句直接 canonicalize：句中第一个 ``_`` 可能落在错误分段（非 Boss 选岗行）。

    def _try_segment(seg0: str) -> str:
        seg0 = strip_trailing_job_reference_noise(
            strip_leading_recruitment_verbs_for_job_chat((seg0 or "").strip())
        )
        if len(seg0) < 6:
            return ""
        seg0 = re.sub(r"^(?:职位|岗位)\s*[:：]?\s*", "", seg0, flags=re.I).strip()
        if not seg0:
            return ""
        inf2 = _infer_boss_job_select_from_space_separated(seg0)
        if inf2:
            return canonicalize_boss_job_select(inf2) or ""
        c2 = canonicalize_boss_job_select(seg0)
        if c2 and " _ " in c2:
            return c2
        return ""

    # 先按逗号/句号切段：「Java 开发工程师_杭州 20-35K，只做打招呼」须整段参与 canonicalize，
    # 若先跑句内子串正则，``[^\s，,]+_`` 会从「开发工程师_」起匹配，丢掉前缀 ``Java ``（Bug）。
    for seg in re.split(r"[，,。\n;；]+", t):
        got = _try_segment(seg)
        if got:
            return got

    # 长句里的「标题_城市 20-35K」（infer 因含 _ 不会对整句推断）。
    # 职位名可含空格：``.+?`` 对齐到**第一个** ``_``，避免 ``[^\s，,]+_`` 截断英文/空格职位名。
    m = re.search(
        r"(.+?)_\s*([^\s，,]+)\s+((?:\d+\s*[-–—~～]\s*\d+|\d+))\s*[Kk]",
        t,
        re.I,
    )
    if m:
        sub = re.sub(r"^(?:职位|岗位)", "", m.group(0).strip(), flags=re.I).strip()
        c = canonicalize_boss_job_select(sub)
        if c and " _ " in c:
            return c

    return ""


def _infer_boss_job_select_from_space_separated(t: str) -> str | None:
    """
    从 HR 自然语言解析为 canonical 行（无下划线），例如：
    ``python工程师 杭州 15-25k``、``Python 工程师 杭州 15-25 K``、``Java 开发工程师 杭州 20-35K``。

    规则：末尾为 ``数字-数字K`` 或 ``数字K``，倒数第二段视为城市，前面整体为职位名。
    """
    t = (t or "").strip()
    if not t or "_" in t:
        return None
    end_m = re.search(r"((?:\d+\s*-\s*\d+|\d+))\s*[Kk]\s*$", t)
    if not end_m:
        return None
    sal_token = end_m.group(1).strip()
    prefix = t[: end_m.start()].strip()
    parts = prefix.split()
    if len(parts) < 2:
        return None
    city = parts[-1]
    title = " ".join(parts[:-1])
    if not title or not city:
        return None
    mm = re.fullmatch(r"\s*(\d+)\s*-\s*(\d+)\s*", sal_token)
    if mm:
        sal_norm = f"{int(mm.group(1))}-{int(mm.group(2))}K"
    else:
        mm1 = re.fullmatch(r"\s*(\d+)\s*", sal_token)
        if not mm1:
            return None
        sal_norm = f"{int(mm1.group(1))}K"
    return f"{title} _ {city} {sal_norm}"


def canonicalize_boss_job_select(s: str) -> str:
    """
    Boss「全部职位」列表行格式统一为：「职位 _ 工作地点 薪资」
    （与客户端一致：下划线两侧常带空格，如 ``python工程师 _ 杭州 15-25K``）。

    支持输入形式：

    - ``python工程师_杭州 15-25K`` / ``python工程师 _ 杭州 15-25K``（含下划线）
    - **飞书 / Lark 常见**：``python工程师 杭州 15-25k``（无下划线，空格分隔）
    """
    s = (s or "").strip()
    if not s:
        return ""
    s = " ".join(s.split())
    m = re.match(r"^(.+?)\s*_\s*(.+)$", s)
    if m:
        return f"{m.group(1).strip()} _ {m.group(2).strip()}"
    inferred = _infer_boss_job_select_from_space_separated(s)
    if inferred:
        return inferred
    return s


def _split_boss_job_line(s: str) -> tuple[str, str]:
    """拆成 (职位标题段, 城市+薪资段)；无下划线时第二段为空。"""
    c = canonicalize_boss_job_select(s)
    if " _ " in c:
        a, b = c.split(" _ ", 1)
        return a.strip(), b.strip()
    return c.strip(), ""


def primary_job_title_from_boss_select_line(text: str) -> str:
    """
    Boss「全部职位」选岗完整行 → 用于数据目录 / ``job_title`` 的职位名片段。

    即 canonical 后 ``职位 _ 城市 薪资`` 的左侧；与 ``jd.json`` 目录名（sanitize）对应。
    供 MCP / 飞书在 HR 说了新选岗行时切换到正确岗位文件夹，避免仍用指针里的旧岗名。
    """
    raw = strip_leading_recruitment_verbs_for_job_chat((text or "").strip())
    if not raw:
        return ""
    c = canonicalize_boss_job_select(raw)
    if " _ " not in c:
        return ""
    title, _rest = c.split(" _ ", 1)
    return title.strip()


def _title_compact_for_match(title: str) -> str:
    """忽略英文大小写、中英文之间的空格差异（Python 工程师 vs python工程师）。"""
    t = (title or "").strip().lower()
    return re.sub(r"\s+", "", t)


def _rest_compact_for_match(rest: str) -> str:
    """城市 + 薪资：去空格、小写后比较（杭州 15-25K vs 杭州15-25k）。"""
    r = (rest or "").strip().lower()
    return re.sub(r"\s+", "", r)


def _jd_select_line_matches(target: str, option_line: str) -> bool:
    """目标 jd_select 与下拉项文案是否同一职位（求简历 / 沟通页选岗）。"""
    if not target or not option_line:
        return False
    tt, tr = _split_boss_job_line(target)
    ot, or_ = _split_boss_job_line(option_line)
    if not tt or not ot:
        return canonicalize_boss_job_select(target).lower() == canonicalize_boss_job_select(
            option_line
        ).lower()
    if _title_compact_for_match(tt) != _title_compact_for_match(ot):
        return False
    # 目标含城市+薪资时，选项也必须带齐且一致；禁止仅凭「职位名」判真（易把会话区/误读节点当成已选对岗）
    if tr:
        if not or_:
            return False
        return _rest_compact_for_match(tr) == _rest_compact_for_match(or_)
    return True


def _normalize_for_match(s: str) -> str:
    if not s:
        return ""
    return " ".join(s.replace("_", " ").split())


def _strip_for_compare(s: str) -> str:
    if not s:
        return ""
    for c in " _（）()":
        s = s.replace(c, "")
    return s.lower()


def _to_boss_search_term(raw: str, full_job_text: str) -> str:
    r, f = (raw or "").strip().lower(), (full_job_text or "").lower()
    if ("go" in r or "go" in f) and "golang" not in r and "golang" not in f:
        if "后端" in (raw or "") or "后端" in (full_job_text or "") or "开发" in (raw or ""):
            return "golang"
    if r.startswith("python") or (full_job_text or "").lower().startswith("python"):
        tail = (raw or "")[6:] if len(raw or "") > 6 and (raw or "").lower().startswith("python") else ""
        return "Python" + tail
    return raw


def dismiss_boss_onboarding_overlays(page) -> int:
    """关闭新手引导 / 气泡（如「意向沟通」「我知道了」），避免遮挡选岗。"""
    closed = 0
    try:
        page.keyboard.press("Escape")
        human_wait(page, 0.2, 0.4)
        closed += 1
    except Exception:
        pass
    for name in ("我知道了", "知道了", "跳过", "不再提示", "关闭", "好的"):
        try:
            btn = page.get_by_role("button", name=name).first
            if btn.count() > 0 and btn.is_visible():
                btn.click(timeout=2500)
                human_wait(page, 0.35, 0.7)
                closed += 1
        except Exception:
            pass
    try:
        for sel in (
            "[class*='guide'] [class*='close']",
            "[class*='coach'] [class*='close']",
            ".boss-guide-close",
        ):
            el = page.locator(sel).first
            if el.count() > 0 and el.is_visible():
                el.click(timeout=2500)
                human_wait(page, 0.3, 0.6)
                closed += 1
    except Exception:
        pass
    return closed


def dismiss_boss_recommend_extra_filters(page) -> None:
    """
    推荐牛人页：用 Escape 收起「筛选 / 城市·区县」等浮层，避免挡列表或误触。
    不主动修改城市、不点「筛选」「确认」：只通过顶栏「在招职位」下拉切换岗位。
    """
    try:
        for _ in range(4):
            page.keyboard.press("Escape")
            human_wait(page, 0.08, 0.16)
    except Exception:
        pass


def _salary_hint_re() -> re.Pattern[str]:
    return re.compile(r"(K|k|万|面议|薪|\d+\s*-\s*\d+)")


def _locate_job_dropdown_options(page, search_value: str):
    sal = _salary_hint_re()
    for ps in (
        "div.ui-dropmenu-list",
        "div[class*='dropmenu-list']",
        "div[class*='DropMenu-list']",
        "div[class*='boss-drop-list']",
        "div[class*='options-list']",
    ):
        try:
            panel = page.locator(ps).first
            if panel.count() == 0 or not panel.is_visible():
                continue
            cand = panel.get_by_text(search_value, exact=False).filter(has_text=sal)
            if cand.count() > 0:
                return cand
        except Exception:
            continue
    return None


def _click_visible_job_option_outside_geek_list(page, search_value: str) -> bool:
    sal = _salary_hint_re()
    try:
        locs = page.get_by_text(search_value, exact=False).filter(has_text=sal)
        n = min(locs.count(), 30)
        for i in range(n):
            el = locs.nth(i)
            try:
                if not el.is_visible():
                    continue
                in_geek = el.evaluate(
                    """e => {
                      if (!e || !e.closest) return false;
                      return !!e.closest('.geek-item, [class*="geek-item"], .conversation-list .geek-item');
                    }"""
                )
                if in_geek:
                    continue
                el.click(timeout=5000)
                return True
            except Exception:
                continue
    except Exception:
        pass
    return False


_JOB_LABEL_FALSE_POSITIVE_MARKERS = (
    "沟通职位",
    "沟通岗位",
    "意向沟通",
    "正在输入",
)


def _clean_job_label_text(t: str) -> str:
    return (t or "").replace("\n", " ").strip()


def _is_false_positive_job_label(t: str) -> bool:
    s = _clean_job_label_text(t)
    if not s:
        return True
    return any(m in s for m in _JOB_LABEL_FALSE_POSITIVE_MARKERS)


def _get_current_job_label_viewport_toolbar_scan(page) -> str:
    """
    推荐牛人等新版顶栏可能不用 ui-dropmenu-label / chat-select-job。
    在视口上部、偏左区域扫描「…_… …20-35K」形态的短文案（排除候选人卡片、侧栏）。
    """
    try:
        t = page.evaluate(
            """() => {
              const nodes = document.querySelectorAll('div, span, button, a, p, label, h1, h2, h3');
              const w = window.innerWidth;
              const h = window.innerHeight;
              let best = '';
              let score = -1;
              for (const el of nodes) {
                try {
                  const raw = (el.innerText || '').replace(/\\s+/g, ' ').trim();
                  if (raw.length < 15 || raw.length > 88) continue;
                  if (!/[_＿]/.test(raw)) continue;
                  if (!/\\d+\\s*[-~～]\\s*\\d+\\s*[Kk]/.test(raw)) continue;
                  if (el.closest(
                    '.geek-item, [class*="geek-item"], [class*="geek-card"], ' +
                    '[class*="candidate-card"], [class*="candidate-card-wrap"], ' +
                    '[class*="recommend-list"] [class*="card"]'
                  )) continue;
                  if (el.closest('nav[class*="side"], .side-bar, [class*="sidebar"], [class*="menu-side"]')) continue;
                  const r = el.getBoundingClientRect();
                  if (r.width <= 1 || r.height <= 1) continue;
                  if (r.bottom > h * 0.52) continue;
                  /* 宽屏下「在招职位」胶囊常在偏右，0.68 过严会扫不到，误判顶栏为空 */
                  if (r.left > w * 0.86) continue;
                  let sc = 60;
                  if (raw.length < 55) sc += 20;
                  if (/\\d+\\s*[-~～]\\s*\\d+\\s*[Kk]/.test(raw)) sc += 40;
                  if (/[_＿]/.test(raw)) sc += 30;
                  if (r.top < h * 0.20) sc += 25;
                  if (sc > score) {
                    score = sc;
                    best = raw;
                  }
                } catch (e) { continue; }
              }
              return best;
            }"""
        )
        if isinstance(t, str) and t.strip():
            logger.debug("_get_current_job_label: viewport 扫描 -> %r", t[:100])
            return _clean_job_label_text(t)
    except Exception as e:
        logger.debug("viewport toolbar scan failed: %s", e)
    return ""


def _get_current_job_label(page) -> str:
    """读取左侧职位下拉文案，避免误读右侧「沟通职位」等同 class 节点。"""

    def _read(loc) -> str:
        try:
            if loc.count() == 0:
                return ""
            return _clean_job_label_text(loc.inner_text() or loc.text_content() or "")
        except Exception:
            return ""

    try:
        inp = page.locator("input.chat-job-search").first
        if inp.count() > 0:
            for ax in (
                "xpath=ancestor::div[contains(@class,'ui-dropmenu')][1]",
                "xpath=ancestor::div[contains(@class,'dropmenu')][1]",
                "xpath=ancestor::div[contains(@class,'boss-chat-job')][1]",
                "xpath=ancestor::div[contains(@class,'job-select')][1]",
                "xpath=ancestor::div[contains(@class,'chat-job')][1]",
            ):
                try:
                    root = inp.locator(ax)
                    if root.count() == 0:
                        continue
                    for sel in ("span.chat-select-job", "div.ui-dropmenu-label", ".chat-select-job"):
                        t = _read(root.locator(sel).first)
                        if t and not _is_false_positive_job_label(t):
                            logger.debug("_get_current_job_label: scoped %s -> %r", sel, t[:100])
                            return t
                except Exception:
                    continue
    except Exception:
        pass

    try:
        locs = page.locator("span.chat-select-job, div.ui-dropmenu-label:has(span.chat-select-job)")
        n = min(locs.count(), 15)
        scored: list[tuple[int, str]] = []
        for i in range(n):
            el = locs.nth(i)
            try:
                if not el.is_visible():
                    continue
                t = _read(el)
                if not t or _is_false_positive_job_label(t):
                    continue
                # 沟通页未选具体在招职位时，文案应为「全部职位」；优先于同屏其它含「工程师」的节点
                if t == "全部职位":
                    return "全部职位"
                prio = 100 if t == "全部职位" else (50 if re.search(r"[Kk万]", t) else (40 if ("工程师" in t or "开发" in t) else 10))
                scored.append((prio, t))
            except Exception:
                continue
        scored.sort(key=lambda x: -x[0])
        if scored:
            return scored[0][1]
    except Exception:
        pass

    # 推荐牛人等页：顶栏多个 ui-dropmenu-label（职位、城市、筛选），绝不能用 .first（常先命中「杭州」）
    try:
        labels = page.locator("div.ui-dropmenu-label")
        n = min(labels.count(), 24)
        scored_lb: list[tuple[int, str]] = []
        for i in range(n):
            el = labels.nth(i)
            try:
                if not el.is_visible():
                    continue
                t = _read(el)
                if not t or _is_false_positive_job_label(t):
                    continue
                if t == "全部职位":
                    return "全部职位"
                if t == "筛选" or (t.startswith("筛选") and len(t) < 12):
                    continue
                prio = 0
                if re.search(r"\d+\s*[-~～]\s*\d+\s*[Kk]", t):
                    prio += 100
                if "_" in t:
                    prio += 50
                if "工程师" in t or "开发" in t or "经理" in t or "总监" in t:
                    prio += 25
                if prio > 0:
                    scored_lb.append((prio, t))
            except Exception:
                continue
        scored_lb.sort(key=lambda x: -x[0])
        if scored_lb:
            logger.debug("_get_current_job_label: ui-dropmenu-label 择优 -> %r", scored_lb[0][1][:100])
            return scored_lb[0][1]
    except Exception:
        pass

    vt = _get_current_job_label_viewport_toolbar_scan(page)
    if vt:
        return vt

    for sel in ["span.chat-select-job", ".chat-select-job"]:
        try:
            t = _read(page.locator(sel).first)
            if t and not _is_false_positive_job_label(t):
                return t
        except Exception:
            pass
    # 推荐牛人 / chat/recommend：下拉展开或关列表后 DOM 刷新瞬间，ui-dropmenu-label 常读空，
    # 但顶栏胶囊仍可见；与 _select_job_already_matches_pill 同源择优，避免误判「顶栏=(空)」而拒打招呼。
    try:
        pill_fb = _get_best_toolbar_job_pill_text(page)
        if pill_fb:
            logger.debug("_get_current_job_label: 胶囊择优兜底 -> %r", pill_fb[:100])
            return pill_fb
    except Exception:
        pass
    return ""


def _verify_job_selected(page, job_text: str) -> tuple[bool, str]:
    """返回 (是否匹配, 当前标签)。禁止 ``if _verify_job_selected(...)`` —— 非空元组恒为真。"""
    try:
        current = _get_current_job_label(page)
        if not current or current == "全部职位":
            return False, current if current else "(空)"
        if _is_false_positive_job_label(current):
            return False, current
        want_canon = canonicalize_boss_job_select(job_text)
        if _jd_select_line_matches(want_canon, current):
            return True, current
        # 完整行（含 _ 与薪资格式）：先尝试去符号全行等价（顶栏文案偶有多余空格/全角符）
        _, tr_w = _split_boss_job_line(want_canon)
        strict_row = bool(tr_w) and bool(
            re.search(r"\d+\s*[-~～]?\s*\d*\s*[Kk]", tr_w or "")
        )
        if strict_row:
            wn = _strip_for_compare(want_canon)
            cn = _strip_for_compare(current)
            if wn and cn and wn == cn:
                logger.info("职位已选中（顶栏文案与 jd_select 去符号后一致）: %r", current[:80])
                return True, current
            # 子串兜底：须顶栏含薪资格式，避免 current 仅为「杭州」时误匹配
            has_sal = bool(re.search(r"\d+\s*[-~～]\s*\d+\s*[Kk]", current or ""))
            if wn and cn and has_sal and len(cn) >= 12 and (wn in cn or cn in wn):
                logger.info("职位已选中（顶栏含薪资且与 jd_select 去符号包含关系一致）: %r", current[:80])
                return True, current
            if (
                _normalize_for_match(want_canon).lower()
                == _normalize_for_match(current).lower()
            ):
                logger.info("职位已选中（规范化全行一致）: %r", current[:80])
                return True, current
            logger.debug(
                "职位严格匹配未过: 顶栏=%r 期望=%r",
                current[:120],
                want_canon[:120],
            )
            return False, current
        want = _normalize_job_text(job_text)
        curr = _normalize_job_text(current)
        if "Golang" in want and "Golang" not in curr:
            return False, current
        if "Java" in want and "Java" not in curr and "Golang" in curr:
            return False, current
        if ("go" in want.lower() or "golang" in want.lower()) and "golang" in curr.lower():
            return True, current
        want_core = _strip_for_compare(want)[:12]
        curr_core = _strip_for_compare(curr)
        if want_core and (want_core in curr_core or want[:10].lower() in curr.lower()):
            return True, current
        want_stripped = _strip_for_compare(job_text)
        curr_stripped = _strip_for_compare(current)
        if want_stripped[:10] in curr_stripped or curr_stripped[:10] in want_stripped:
            return True, current
        if want_stripped[:12] and want_stripped[:12].casefold() in curr_stripped.casefold():
            return True, current
        if "Golang" in want and "Golang" in curr and ("杭州" in curr or "25" in curr or "40" in curr):
            return True, current
        return False, current
    except Exception:
        return False, ""


def select_all_positions(page) -> bool:
    try:
        page.bring_to_front()
        human_wait(page, 0.2, 0.5)
    except Exception:
        pass
    current = _get_current_job_label(page)
    if current == "全部职位":
        logger.info("已为「全部职位」，跳过")
        return True
    try:
        for trigger_sel in [
            "div.ui-dropmenu-label:has(span.chat-select-job)",
            "span.chat-select-job",
            "div.ui-dropmenu-label",
        ]:
            trig = page.locator(trigger_sel).first
            if trig.count() > 0 and trig.is_visible():
                trig.click()
                human_wait(page, 0.6, 1.2)
                break
    except Exception as e:
        logger.warning("点击职位下拉失败: %s", e)
        return False
    try:
        all_opt = page.get_by_text("全部职位", exact=True).first
        if all_opt.count() > 0 and all_opt.is_visible():
            all_opt.click()
            human_wait(page, 1.0, 1.8)
        else:
            fallback = page.locator("[class*='dropmenu-item'], [class*='option']").filter(has_text="全部职位").first
            if fallback.count() > 0 and fallback.is_visible():
                fallback.click()
                human_wait(page, 1.0, 1.8)
    except Exception as e:
        logger.warning("选择「全部职位」选项失败: %s", e)
        return False
    if _get_current_job_label(page) == "全部职位":
        logger.info("已选择「全部职位」")
        return True
    logger.warning("选择「全部职位」后校验未通过")
    return False


def _playwright_search_roots(page):
    """主页面 + 全部 frame（职位下拉/搜索框偶发在子 frame）。"""
    seen: set[int] = set()
    roots: list[Any] = []
    for obj in (page,):
        k = id(obj)
        if k not in seen:
            seen.add(k)
            roots.append(obj)
    try:
        for f in page.frames:
            k = id(f)
            if k not in seen:
                seen.add(k)
                roots.append(f)
    except Exception:
        pass
    return roots


def _dropdown_salary_rows_locator(root):
    return root.locator(
        "[class*='dropmenu'] li, "
        "[class*='dropmenu-list'] li, "
        "[class*='dropdown'] li, "
        "[class*='boss-dropmenu'] li, "
        "[class*='boss-drop-list'] li, "
        "ul[role='listbox'] li, "
        "[class*='option'][class*='job']"
    ).filter(has_text=re.compile(r"\d+\s*[-~～]?\s*\d*\s*[Kk]|\d+\s*[Kk]"))


def _find_visible_job_search_locator(page):
    for root in _playwright_search_roots(page):
        for sel in BOSS_JOB_SEARCH_INPUT_SELECTORS:
            try:
                loc = root.locator(sel)
                n = min(loc.count(), 24)
                for i in range(n):
                    el = loc.nth(i)
                    if el.count() == 0:
                        continue
                    try:
                        if el.is_visible():
                            return el
                    except Exception:
                        pass
            except Exception:
                continue
    return None


def _boss_url_looks_like_user_center(url: str) -> bool:
    u = (url or "").lower().replace("_", "-")
    return "user-center" in u or "usercenter" in u.replace("-", "")


def _recover_from_boss_user_center_page(page) -> None:
    """误点头像进入个人中心后，尝试返回推荐牛人等业务页。"""
    try:
        if not _boss_url_looks_like_user_center(page.url or ""):
            return
        logger.warning("检测到已进入个人中心/账号页，尝试关闭菜单并返回上一页或推荐牛人")
        try:
            page.keyboard.press("Escape")
            human_wait(page, 0.25, 0.45)
        except Exception:
            pass
        try:
            page.go_back()
            human_wait(page, 0.9, 1.4)
        except Exception:
            pass
        if _boss_url_looks_like_user_center(page.url or ""):
            try:
                page.goto(
                    "https://www.zhipin.com/web/chat/recommend",
                    wait_until="domcontentloaded",
                    timeout=20000,
                )
                human_wait(page, 1.5, 2.5)
            except Exception:
                pass
    except Exception as e:
        logger.debug("recover user-center 失败: %s", e)


def _toolbar_pill_geo_ok(trig, *, max_cx_ratio: float) -> bool:
    """
    顶栏在招职位胶囊可稍靠右（宽屏/新布局）；max_cx_ratio 为允许的中心 x 占视口宽度比例上限。
    """
    try:
        if trig.count() == 0:
            return False
        lim = float(max_cx_ratio)
        return bool(
            trig.first.evaluate(
                f"""el => {{
                  if (!el || !el.getBoundingClientRect) return false;
                  const r = el.getBoundingClientRect();
                  const w = window.innerWidth || 1200;
                  if (r.width <= 0 || r.height <= 0) return false;
                  const cx = (r.left + r.right) / 2;
                  if (cx > w * {lim}) return false;
                  let p = el;
                  for (let i = 0; i < 20 && p; i++) {{
                    const href = (p.getAttribute && p.getAttribute('href')) || '';
                    const h = href.toLowerCase();
                    if (h.includes('user-center') || h.includes('usercenter')) return false;
                    const cls = (p.className && String(p.className).toLowerCase()) || '';
                    const pid = (p.id && String(p.id).toLowerCase()) || '';
                    if (cls.includes('header-user') || cls.includes('nav-user')) return false;
                    if (cls.includes('user-avatar') || cls.includes('avatar-wrap')) return false;
                    if (cls.includes('boss-sidebar-user') || cls.includes('profile-dropdown')) return false;
                    if (cls.includes('sidebar-user') && cls.includes('boss')) return false;
                    if (pid.includes('user') && (pid.includes('header') || pid.includes('nav'))) return false;
                    p = p.parentElement;
                  }}
                  return true;
                }}"""
            )
        )
    except Exception:
        return False


def _locator_is_likely_job_dropdown_trigger(trig) -> bool:
    """
    避免点到右上角头像/个人中心下拉：
    排除 href 含 user-center 的祖先、常见 header-user/avatar 区，以及视口过靠右的节点。
    """
    return _toolbar_pill_geo_ok(trig, max_cx_ratio=0.78)


def _locator_resembles_boss_job_dropdown_trigger(trig) -> bool:
    """
    文案/占位区分「在招职位」下拉 vs 旁边的「城市/区县」或「筛选」。
    纯「杭州」「滨江区」等短文案且不含岗位特征时，不作为职位下拉点击。
    """
    try:
        if trig.count() == 0:
            return False
        el = trig.first
        tag = (el.evaluate("e => (e.tagName||'').toLowerCase()") or "").lower()
        if tag == "input":
            ph = (el.get_attribute("placeholder") or "").strip()
            cls = (el.get_attribute("class") or "").lower()
            if "筛选" in ph:
                return False
            if "chat-job-search" in cls:
                return True
            if "职位" in ph or "岗位" in ph:
                return True
            return False
        t = (el.inner_text() or el.text_content() or "").strip()
        t = " ".join(t.split())
        if not t:
            return True
        if t == "筛选" or (t.startswith("筛选") and len(t) < 12):
            return False
        short_place = (
            "杭州",
            "北京",
            "上海",
            "广州",
            "深圳",
            "成都",
            "武汉",
            "西安",
            "南京",
            "苏州",
            "滨江区",
            "西湖区",
            "余杭区",
            "拱墅区",
            "上城区",
            "钱塘区",
            "富阳区",
            "临安区",
            "淳安县",
            "桐庐县",
            "建德市",
        )
        if t in short_place:
            return False
        if len(t) <= 4 and t.endswith("区"):
            return False
        if "全部职位" in t:
            return True
        if "_" in t and _BOSS_TOOLBAR_SALARY_K_RE.search(t):
            return True
        if _BOSS_TOOLBAR_SALARY_K_RE.search(t):
            return True
        if any(
            k in t
            for k in (
                "工程师",
                "开发",
                "经理",
                "总监",
                "架构",
                "顾问",
                "运营",
                "专员",
                "职位",
                "岗位",
                "主管",
            )
        ):
            return True
        if len(t) <= 8 and not re.search(r"[Kk]", t) and "_" not in t:
            return False
        return True
    except Exception:
        return True


_TOOLBAR_JOB_PILL_SELECTORS = [
    "div.ui-dropmenu-label",
    "div[class*='dropmenu-label']",
    "span.chat-select-job",
    "span[class*='chat-select-job']",
]


def _score_boss_toolbar_job_pill_text(t: str) -> int:
    """顶栏「在招职位」胶囊文案打分：须带薪资格式，避免把纯城市/筛选当成职位。"""
    s = _clean_job_label_text(t)
    if not s or s == "全部职位":
        return 0
    if s == "筛选" or (s.startswith("筛选") and len(s) < 12):
        return 0
    if _is_false_positive_job_label(s):
        return 0
    score = 0
    if _BOSS_TOOLBAR_SALARY_K_RE.search(s):
        score += 100
    if "_" in s or "＿" in s:
        score += 50
    if any(x in s for x in ("工程师", "开发", "经理", "总监", "顾问", "专员", "运营", "设计师", "产品")):
        score += 25
    return score


def _evaluate_click_boss_job_pill_in_document() -> str:
    """在浏览器内执行：匹配「在招职位」胶囊并点击；允许无下划线（仅 岗位词+城市+薪资）。"""
    return r"""() => {
      const reSalary = /\d+\s*[-~～\u2013\u2014\uFF0D－]\s*\d+\s*[Kk]/i;
      const jobCue = /工程师|开发|经理|顾问|总监|设计师|运营|产品|专员|算法|测试|数据|销售|市场|人力|行政/;
      const cityCue = /杭州|北京|上海|深圳|广州|成都|武汉|南京|苏州|西安|重庆|天津|长沙|郑州|合肥/;
      const nodes = document.querySelectorAll(
        'div, span, button, a, p, label, h2, h3, h4, strong, em, i'
      );
      const cands = [];
      for (const el of nodes) {
        const t = (el.innerText || '').replace(/\s+/g, ' ').trim();
        if (t.length < 10 || t.length > 100) continue;
        if (!reSalary.test(t)) continue;
        const hasU = t.includes('_') || t.includes('＿');
        if (!hasU && !(jobCue.test(t) && cityCue.test(t))) continue;
        if (!hasU && t.length < 16) continue;
        if (/^筛选/.test(t) && t.length < 14) continue;
        if (el.closest(
          '.geek-item, [class*="geek-item"], [class*="candidate-card"], ' +
          '[class*="geek-card"], [class*="conversation-list"]'
        )) continue;
        const r = el.getBoundingClientRect();
        if (r.width < 1 || r.height < 1) continue;
        if (r.bottom > innerHeight * 0.72) continue;
        if (r.left > innerWidth * 0.985) continue;
        const cy = r.top + r.height / 2;
        const area = r.width * r.height;
        if (area > 250000) continue;
        cands.push({ el, t, cy, area });
      }
      cands.sort((a, b) => a.cy - b.cy || a.area - b.area);
      for (const c of cands.slice(0, 12)) {
        try {
          c.el.scrollIntoView({ block: 'nearest', inline: 'nearest' });
          c.el.dispatchEvent(
            new MouseEvent('click', { bubbles: true, cancelable: true, view: window })
          );
          c.el.click();
          return c.t.slice(0, 90);
        } catch (e) {}
      }
      return '';
    }"""


def _boss_url_is_recommend_page(url: str) -> bool:
    u = (url or "").lower()
    if "zhipin.com" not in u and "zhpin.com" not in u:
        return False
    return "geek/recommend" in u or "chat/recommend" in u


def _get_best_toolbar_job_pill_text(page) -> str:
    """
    与「点击在招职位胶囊」相同的打分与几何过滤，只读文案不点击。
    解决 _get_current_job_label 与胶囊展示不一致时误展开下拉的问题。
    """
    roots = _playwright_search_roots(page)
    best_score = -1
    best_text = ""
    for root in roots:
        for selector in _TOOLBAR_JOB_PILL_SELECTORS:
            loc = root.locator(selector)
            n = min(loc.count(), 36)
            for i in range(n):
                el = loc.nth(i)
                try:
                    if not el.is_visible():
                        continue
                    raw = _clean_job_label_text(el.inner_text() or el.text_content() or "")
                    sc = _score_boss_toolbar_job_pill_text(raw)
                    if sc < 65:
                        continue
                    if not _toolbar_pill_geo_ok(el, max_cx_ratio=0.96):
                        continue
                    if not _locator_resembles_boss_job_dropdown_trigger(el):
                        continue
                    if raw == "筛选" or (raw.startswith("筛选") and len(raw) < 10):
                        continue
                    if sc > best_score:
                        best_score = sc
                        best_text = raw
                except Exception:
                    continue
    return best_text


def _select_job_already_matches_pill(page, job_text: str) -> bool:
    want = canonicalize_boss_job_select(job_text.strip())
    pill = _get_best_toolbar_job_pill_text(page)
    if not pill:
        return False
    if _jd_select_line_matches(want, pill):
        logger.info("顶栏职位胶囊已与 jd_select 一致，无需展开下拉: %r", pill[:90])
        return True
    return False


def _click_scored_job_pill_on_toolbar(page) -> bool:
    """
    推荐牛人 / 新沟通顶栏：无「全部职位」时，当前在招职位直接显示为「Python 工程师 _ 杭州 15-25K」等胶囊。
    必须点击该胶囊才会展开列表与搜索框，才能切换到 JD 目标岗位。
    主页面 + 全部 frame 均尝试（Boss 偶将主内容放在 iframe）。
    """
    roots = _playwright_search_roots(page)

    def _try_scored(root: Any, geo_ratio: float, min_score: int) -> bool:
        for selector in _TOOLBAR_JOB_PILL_SELECTORS:
            loc = root.locator(selector)
            n = min(loc.count(), 36)
            best_i = -1
            best_score = -1
            best_preview = ""
            for i in range(n):
                el = loc.nth(i)
                try:
                    if not el.is_visible():
                        continue
                    raw = _clean_job_label_text(el.inner_text() or el.text_content() or "")
                    sc = _score_boss_toolbar_job_pill_text(raw)
                    if sc < min_score:
                        continue
                    if not _toolbar_pill_geo_ok(el, max_cx_ratio=geo_ratio):
                        continue
                    if not _locator_resembles_boss_job_dropdown_trigger(el):
                        continue
                    if sc > best_score:
                        best_score = sc
                        best_i = i
                        best_preview = raw[:72]
                except Exception:
                    continue
            if best_i < 0:
                continue
            try:
                pill = root.locator(selector).nth(best_i)
                logger.info(
                    "点击顶栏在招职位胶囊以展开列表（当前展示: %s，将切换为 jd_select）",
                    best_preview or "(空)",
                )
                pill.click(timeout=8000, force=True, no_wait_after=True)
                logger.info("职位胶囊点击已派发，等待下拉渲染…")
                human_wait(page, 0.75, 1.25)
                return True
            except Exception as e:
                logger.debug("点击在招职位胶囊失败 sel=%s: %s", selector, e)
                continue
        return False

    try:
        for root in roots:
            if _try_scored(root, 0.78, 100):
                return True
        for root in roots:
            if _try_scored(root, 0.92, 70):
                return True
        for root in roots:
            for selector in _TOOLBAR_JOB_PILL_SELECTORS:
                filtered = root.locator(selector).filter(has_text=_BOSS_TOOLBAR_SALARY_K_RE)
                n = min(filtered.count(), 18)
                for i in range(n):
                    el = filtered.nth(i)
                    try:
                        if not el.is_visible():
                            continue
                        raw = _clean_job_label_text(el.inner_text() or el.text_content() or "")
                        if _score_boss_toolbar_job_pill_text(raw) < 65:
                            continue
                        if raw == "筛选" or (raw.startswith("筛选") and len(raw) < 10):
                            continue
                        if not _toolbar_pill_geo_ok(el, max_cx_ratio=0.96):
                            continue
                        if not _locator_resembles_boss_job_dropdown_trigger(el):
                            continue
                        logger.info("点击顶栏薪资格式匹配项以展开职位列表: %s", raw[:70])
                        el.click(timeout=8000, force=True, no_wait_after=True)
                        logger.info("职位胶囊点击已派发，等待下拉渲染…")
                        human_wait(page, 0.75, 1.25)
                        return True
                    except Exception:
                        continue
    except Exception as e:
        logger.debug("_click_scored_job_pill_on_toolbar: %s", e)

    js = _evaluate_click_boss_job_pill_in_document()
    for ri, root in enumerate(roots):
        try:
            clicked_preview = root.evaluate(js)
            if isinstance(clicked_preview, str) and clicked_preview.strip():
                logger.info(
                    "通过 DOM 脚本点击在招职位胶囊(frame#%s): %s",
                    ri,
                    clicked_preview[:72],
                )
                human_wait(page, 0.8, 1.35)
                return True
        except Exception as e:
            logger.debug("frame %s DOM 点击职位胶囊失败: %s", ri, e)

    logger.info(
        "未能自动定位顶栏在招职位胶囊；若页面已打开职位下拉请关闭后重试，或检查顶栏是否被遮挡"
    )
    return False


def _open_job_dropdown_multi(page) -> None:
    """展开职位下拉：优先「全部职位」；否则点击顶栏当前在招职位胶囊（推荐牛人常见）。"""
    _close_user_menu_if_open(page)
    try:
        page.keyboard.press("Escape")
        human_wait(page, 0.2, 0.38)
    except Exception:
        pass
    try:
        all_job_btn = page.get_by_text("全部职位", exact=False).first
        if all_job_btn.count() > 0 and all_job_btn.is_visible():
            if _locator_is_likely_job_dropdown_trigger(all_job_btn) and _locator_resembles_boss_job_dropdown_trigger(
                all_job_btn
            ):
                all_job_btn.click(timeout=4000, no_wait_after=True)
                human_wait(page, 0.65, 1.2)
                return
    except Exception:
        pass
    if _click_scored_job_pill_on_toolbar(page):
        return
    for trigger_sel in BOSS_JOB_TRIGGER_SELECTORS:
        try:
            loc = page.locator(trigger_sel)
            n = min(loc.count(), 14)
            for i in range(n):
                trig = loc.nth(i)
                if trig.count() == 0:
                    continue
                try:
                    if not trig.is_visible():
                        continue
                    if not _locator_is_likely_job_dropdown_trigger(trig):
                        logger.debug("跳过疑似非职位下拉的触发器: %s #%s", trigger_sel, i)
                        continue
                    if not _locator_resembles_boss_job_dropdown_trigger(trig):
                        logger.debug("跳过疑似城市/筛选控件: %s #%s", trigger_sel, i)
                        continue
                    trig.click(timeout=4000, no_wait_after=True)
                    human_wait(page, 0.65, 1.1)
                    return
                except Exception:
                    continue
        except Exception:
            continue


def _scan_and_click_matching_job_row(page, job_text: str) -> bool:
    """不依赖搜索框：在所有 root 上扫描带薪资格式的下拉项并点击匹配行。"""
    want = canonicalize_boss_job_select(job_text)
    logger.info("在下拉列表中扫描目标岗位（避免全页 count，最多检查 120 行；长列表可配合键盘滚动）…")
    for root in _playwright_search_roots(page):
        try:
            rows = _dropdown_salary_rows_locator(root)
            for i in range(120):
                try:
                    el = rows.nth(i)
                    try:
                        vis = el.is_visible(timeout=600)
                    except Exception:
                        vis = False
                    if not vis:
                        continue
                    if i > 0 and i % 28 == 0:
                        try:
                            page.keyboard.press("PageDown")
                            human_wait(page, 0.2, 0.4)
                        except Exception:
                            pass
                    txt = (el.inner_text() or el.text_content() or "").strip()
                    if not txt or "全部职位" in txt:
                        continue
                    if _jd_select_line_matches(want, txt):
                        el.scroll_into_view_if_needed()
                        human_wait(page, 0.15, 0.35)
                        el.click(timeout=8000, no_wait_after=True, force=True)
                        human_wait(page, 1.0, 2.0)
                        ok_scan, _ = _verify_job_selected(page, job_text)
                        if ok_scan:
                            logger.info("已按 jd_select 匹配选中: %s", txt[:60])
                            return True
                except Exception as e:
                    logger.debug("扫描列表项 %s: %s", i, e)
        except Exception as e:
            logger.debug("列表扫描(root)失败: %s", e)
    return False


def _click_first_matching_row_in_job_dropmenu_panel(page, job_text: str) -> bool:
    """
    Boss 下拉内常有多条文案完全相同的在招职位（如同一 Java 岗多开）。
    仅在「职位下拉面板」内找 li/option，点第一条与 jd_select 语义一致的行，避免扫到牛人卡片。
    """
    want = canonicalize_boss_job_select(job_text)
    panel_selectors = (
        "div.ui-dropmenu-list",
        "div[class*='dropmenu-list']",
        "div[class*='DropMenu-list']",
        "div[class*='boss-drop-list']",
        "ul[role='listbox']",
    )
    for ps in panel_selectors:
        panel = page.locator(ps).first
        try:
            if not panel.is_visible(timeout=600):
                continue
        except Exception:
            continue
        items = panel.locator("li, [role='option'], div[class*='option'], a[href*='javascript']")
        for i in range(72):
            try:
                el = items.nth(i)
                if not el.is_visible(timeout=450):
                    continue
                txt = _clean_job_label_text(el.inner_text() or el.text_content() or "")
                if not txt or "请输入职位" in txt or len(txt) < 8:
                    continue
                if not _BOSS_TOOLBAR_SALARY_K_RE.search(txt):
                    continue
                if not _jd_select_line_matches(want, txt):
                    continue
                logger.info("在职位下拉面板内点击匹配行 #%s（重复项点第一条即可）: %s", i, txt[:72])
                el.click(timeout=7000, force=True, no_wait_after=True)
                human_wait(page, 0.85, 1.35)
                ok, cur = _verify_job_selected(page, job_text)
                if ok:
                    logger.info("面板点击后顶栏校验已通过: %r", (cur or "")[:85])
                    return True
            except Exception as e:
                logger.debug("面板行 %s: %s", i, e)
                continue
    return False


def _try_click_matching_job_row_by_title_filter(page, job_text: str) -> bool:
    """下拉已展开时：用职位标题 + 薪资格式快速定位行；优先只在下拉面板内匹配。"""
    want = canonicalize_boss_job_select(job_text)
    title_part, _r = _split_boss_job_line(want)
    if not title_part or len(title_part) < 2:
        return False
    hit = None
    try:
        panel = page.locator(
            "div.ui-dropmenu-list, div[class*='dropmenu-list'], div[class*='boss-drop-list']"
        ).first
        if panel.is_visible(timeout=500):
            hit = panel.get_by_text(title_part, exact=False).filter(has_text=_BOSS_TOOLBAR_SALARY_K_RE)
    except Exception:
        pass
    if hit is None:
        hit = page.get_by_text(title_part, exact=False).filter(has_text=_BOSS_TOOLBAR_SALARY_K_RE)
    for j in range(14):
        try:
            el = hit.nth(j)
            if not el.is_visible(timeout=800):
                continue
            txt = (el.inner_text() or "").strip()
            if not txt or "全部职位" in txt:
                continue
            in_geek = el.evaluate(
                """e => !!(e && e.closest && e.closest('.geek-item, [class*="geek-item"]'))"""
            )
            if in_geek:
                continue
            if not _jd_select_line_matches(want, txt):
                continue
            logger.info("快速匹配下拉项: %s", txt[:70])
            el.click(timeout=8000, force=True, no_wait_after=True)
            human_wait(page, 0.9, 1.5)
            ok, _ = _verify_job_selected(page, job_text)
            return bool(ok)
        except Exception:
            continue
    return False


def _search_terms_for_job_dropdown(job_text: str) -> list[str]:
    """生成职位搜索框尝试关键字：整段标题、首词、前两词（覆盖 Python 工程师 / python工程师）。"""
    canon = canonicalize_boss_job_select(job_text)
    title_part, _rest = _split_boss_job_line(canon)
    if not title_part:
        title_part = canon
    parts = title_part.split()
    out: list[str] = []
    for x in (title_part, parts[0] if parts else "", " ".join(parts[:2]) if len(parts) >= 2 else ""):
        x = (x or "").strip()
        if x and x not in out:
            out.append(x)
    if not out:
        out = [job_text.strip()[:24]]
    return out


def _try_click_matching_option(page, job_text: str, inp) -> bool:
    """在下拉列表中点击与 jd_select 语义一致的那一行（含薪资）。inp 可为 None，仅走列表扫描与文本兜底。"""
    want = canonicalize_boss_job_select(job_text)
    terms = _search_terms_for_job_dropdown(job_text)
    if inp is not None and terms:
        sv0 = _to_boss_search_term(terms[0], job_text)
        try:
            inp.click(timeout=5000, no_wait_after=True)
            human_wait(page, 0.2, 0.4)
            inp.fill("")
            human_wait(page, 0.1, 0.2)
            inp.fill(sv0)
            human_wait(page, 0.7, 1.2)
        except Exception as e:
            logger.debug("预填搜索词失败: %s", e)

    if _click_first_matching_row_in_job_dropmenu_panel(page, job_text):
        return True

    if _try_click_matching_job_row_by_title_filter(page, job_text):
        return True

    if _scan_and_click_matching_job_row(page, job_text):
        return True

    # 回退：get_by_text + 顺序点击（旧逻辑，略增强校验）
    for search_value in _search_terms_for_job_dropdown(job_text):
        sv = _to_boss_search_term(search_value, job_text)
        if inp is not None:
            try:
                inp.click(timeout=5000, no_wait_after=True)
                human_wait(page, 0.2, 0.4)
                inp.fill("")
                human_wait(page, 0.12, 0.25)
                inp.fill(sv)
                human_wait(page, 0.7, 1.3)
            except Exception as e:
                logger.debug("填充搜索词失败 %s: %s", sv, e)
                continue
        else:
            human_wait(page, 0.25, 0.45)
        try:
            if page.get_by_text("没有相关职位", exact=False).first.is_visible(timeout=400):
                continue
        except Exception:
            pass
        candidates = _locate_job_dropdown_options(page, sv)
        if candidates is None:
            candidates = page.get_by_text(sv, exact=False).filter(has_text=_salary_hint_re())
        for j in range(10):
            try:
                el = candidates.nth(j)
                if not el.is_visible():
                    continue
                in_geek = el.evaluate(
                    """e => {
                      if (!e || !e.closest) return false;
                      return !!e.closest('.geek-item, [class*="geek-item"]');
                    }"""
                )
                if in_geek:
                    continue
                el.scroll_into_view_if_needed()
                human_wait(page, 0.15, 0.35)
                opt_txt = (el.inner_text() or "").strip()
                if opt_txt and not _jd_select_line_matches(want, opt_txt):
                    continue
                el.click(timeout=8000, no_wait_after=True, force=True)
                human_wait(page, 1.0, 2.0)
                ok_j, _ = _verify_job_selected(page, job_text)
                if ok_j:
                    logger.info("已选择下拉项 j=%s search=%s", j, sv)
                    return True
            except Exception as e:
                logger.debug("点击候选 j=%s: %s", j, e)
        if _click_visible_job_option_outside_geek_list(page, sv):
            human_wait(page, 1.0, 2.0)
            ok_fb, _ = _verify_job_selected(page, job_text)
            if ok_fb:
                logger.info("已通过非会话列表兜底选中 search=%s", sv)
                return True
    return False


def select_job(page, job_text: str, *, expect_city: str | None = None) -> bool:
    if not job_text or not job_text.strip():
        return False
    job_text = canonicalize_boss_job_select(job_text.strip())
    try:
        page.bring_to_front()
        human_wait(page, 0.2, 0.5)
    except Exception:
        pass
    dismiss_boss_onboarding_overlays(page)
    if _boss_url_looks_like_user_center(page.url or ""):
        _recover_from_boss_user_center_page(page)
    # 先校验已选岗：避免 dismiss 里连续 Escape 改变顶栏状态后再读不到职位文案
    ok_skip, cur0 = _verify_job_selected(page, job_text)
    if ok_skip:
        logger.info("职位已选中，跳过")
        return True
    if _select_job_already_matches_pill(page, job_text):
        return True
    logger.debug("选岗前校验未通过，读到顶栏=%r", (cur0 or "")[:120])

    dismiss_boss_recommend_extra_filters(page)
    if expect_city:
        logger.info(
            "选岗：只操作顶栏「在招职位」下拉（或「全部职位」），不点「筛选」与城市/区县；JD 期望城市=%r",
            expect_city,
        )
    ok_skip2, cur1 = _verify_job_selected(page, job_text)
    if ok_skip2:
        logger.info("职位已选中，跳过（收起浮层后校验）")
        return True
    if _select_job_already_matches_pill(page, job_text):
        return True
    if cur1 and cur1 != "(空)":
        logger.info("收起浮层后仍读到顶栏=%r，将尝试展开职位下拉", cur1[:120])

    _open_job_dropdown_multi(page)
    if _boss_url_looks_like_user_center(page.url or ""):
        _recover_from_boss_user_center_page(page)
        _open_job_dropdown_multi(page)
    human_wait(page, 0.35, 0.65)
    logger.info("职位下拉应已展开，正在匹配 jd_select 目标行…")

    inp = None
    for _ in range(6):
        inp = _find_visible_job_search_locator(page)
        if inp is not None and inp.count() > 0:
            break
        human_wait(page, 0.2, 0.4)

    if inp is not None and inp.count() > 0:
        try:
            inp.click(timeout=4000)
            human_wait(page, 0.3, 0.55)
            inp.fill("")
            human_wait(page, 0.12, 0.22)
        except Exception as e:
            logger.debug("聚焦职位搜索框失败: %s", e)
    else:
        logger.info("未检测到职位搜索框，尝试直接扫描下拉列表项（推荐牛人等新布局）")

    if _try_click_matching_option(page, job_text, inp):
        human_wait(page, 0.4, 0.7)
        return True

    # 下拉仍开时：Boss 可能已把胶囊切到 jd_select，但列表未点中行
    ok_pre_esc, cur_pre = _verify_job_selected(page, job_text)
    if ok_pre_esc:
        logger.info(
            "下拉未关时顶栏已与 jd_select 一致，关下拉后继续: %r",
            (cur_pre or "")[:100],
        )
        try:
            page.keyboard.press("Escape")
            human_wait(page, 0.2, 0.4)
        except Exception:
            pass
        return True

    # 关下拉后顶栏文案常短暂读不到（DOM 刷新）；多等几次并用视口扫描兜底
    try:
        page.keyboard.press("Escape")
        human_wait(page, 0.25, 0.45)
    except Exception:
        pass

    want_c = canonicalize_boss_job_select(job_text)
    cur_after = ""
    for attempt in range(8):
        human_wait(page, 0.5, 0.8)
        ok_after, cur_after = _verify_job_selected(page, job_text)
        if ok_after:
            logger.info(
                "顶栏已与 jd_select 一致（关下拉后第 %s 次校验，继续打招呼）: %r",
                attempt + 1,
                (cur_after or "")[:100],
            )
            return True
        vt = _get_current_job_label_viewport_toolbar_scan(page)
        if vt and _jd_select_line_matches(want_c, vt):
            logger.info(
                "关下拉后常规读顶栏为空或不一致，视口扫描与 jd_select 一致: %r",
                vt[:90],
            )
            return True
        if cur_after and cur_after != "(空)":
            logger.debug("关下拉后第 %s 次读到顶栏=%r", attempt + 1, cur_after[:100])

    # 再关一层浮层并稍等：Boss 偶发关列表后胶囊文案晚于 innerText 更新
    try:
        for _ in range(3):
            page.keyboard.press("Escape")
            human_wait(page, 0.35, 0.55)
        human_wait(page, 0.9, 1.4)
        ok_last, cur_last = _verify_job_selected(page, job_text)
        if ok_last:
            logger.info("关下拉后追加 Escape+等待，顶栏校验通过: %r", (cur_last or "")[:100])
            return True
    except Exception:
        pass

    if _boss_url_is_recommend_page(page.url or ""):
        logger.warning(
            "推荐牛人页未能从下拉严格选中 %s（最后顶栏=%r）；禁止继续打招呼以免误伤其它在招岗",
            job_text[:80],
            (cur_after or "")[:120],
        )
        return False

    logger.warning(
        "无法在顶栏职位下拉中匹配并选中: %s；关下拉后最后顶栏=%r",
        job_text[:80],
        (cur_after or "")[:120],
    )
    return False


def is_boss_identity_verify_page(page) -> bool:
    """
    当前是否处于 Boss「网站访客身份验证」滑块页（异常访问拦截）。

    命中后收网 RPA 必须立即停止并通知人工，避免在验证页继续点击加重风控。
    """
    try:
        ctx = getattr(page, "context", None)
        to_check = list(ctx.pages) if ctx else [page]
        for p in to_check:
            u = ((p.url or "") if p is not None else "").lower()
            if "verify-slider" in u or "/user/safe/verify" in u:
                return True
        try:
            t = (page.title() or "").strip()
            if "身份验证" in t and "BOSS" in t.upper():
                return True
        except Exception:
            pass
    except Exception:
        pass
    return False


def _is_chat_inbox_page(url: str) -> bool:
    u = (url or "").lower()
    if "zhipin.com" not in u and "zhpin.com" not in u:
        return False
    if "/geek/chat" in u or "/chat/index" in u:
        return True
    if "/chat" in u and "recommend" not in u and "job/list" not in u:
        return True
    return False


def _is_user_menu_dropdown_open(page) -> bool:
    """
    仅当文案出现在视口最右上（头像下拉内）时判真。
    勿用「个人中心」「账号权益」：Boss 顶栏常驻链接会误判为菜单已展开。
    """
    try:
        for kw in ("退出登录", "切换身份", "桌面客户端"):
            hit = page.get_by_text(kw, exact=False)
            n = min(hit.count(), 12)
            for i in range(n):
                el = hit.nth(i)
                try:
                    if el.count() == 0 or not el.is_visible():
                        continue
                    if el.evaluate(
                        """e => {
                          const r = e.getBoundingClientRect();
                          const w = window.innerWidth, h = window.innerHeight;
                          if (r.width <= 0 || r.height <= 0) return false;
                          const cx = (r.left + r.right) / 2, cy = (r.top + r.bottom) / 2;
                          if (cy > h * 0.36) return false;
                          if (cx < w * 0.58) return false;
                          return true;
                        }"""
                    ):
                        return True
                except Exception:
                    continue
        hit2 = page.get_by_text("退出", exact=True)
        n2 = min(hit2.count(), 10)
        for i in range(n2):
            el = hit2.nth(i)
            try:
                if el.count() == 0 or not el.is_visible():
                    continue
                if el.evaluate(
                    """e => {
                      const r = e.getBoundingClientRect();
                      const w = window.innerWidth, h = window.innerHeight;
                      if (r.width <= 0 || r.height <= 0) return false;
                      const cx = (r.left + r.right) / 2, cy = (r.top + r.bottom) / 2;
                      if (cy > h * 0.36) return false;
                      if (cx < w * 0.62) return false;
                      return true;
                    }"""
                ):
                    return True
            except Exception:
                continue
    except Exception:
        pass
    return False


def _close_user_menu_if_open(page) -> None:
    if _is_user_menu_dropdown_open(page):
        logger.info("检测到用户菜单已展开（可能误点头像），关闭后点击沟通")
        try:
            page.keyboard.press("Escape")
            human_wait(page, 0.3, 0.6)
        except Exception:
            pass


def navigate_to_chat_page(page) -> bool:
    try:
        _close_user_menu_if_open(page)
        url = page.url or ""
        if _is_chat_inbox_page(url):
            logger.debug("已在沟通页，跳过导航")
            return True
        chat_btn = page.locator('a[ka="menu-geek-chat"]').first
        if chat_btn.count() > 0:
            chat_btn.click()
            human_wait(page, 1.5, 2.5)
            if _is_chat_inbox_page(page.url or ""):
                return True
        for target_url in [
            "https://www.zhipin.com/web/geek/chat",
            "https://www.zhipin.com/web/chat/index",
        ]:
            try:
                page.goto(target_url, wait_until="domcontentloaded", timeout=15000)
                human_wait(page, 1.5, 2.5)
                if "zhipin.com" in (page.url or "") or "zhpin.com" in (page.url or ""):
                    return True
            except Exception:
                continue
    except Exception as e:
        logger.warning("navigate_to_chat_page 失败: %s", e)
    return True


def navigate_to_candidate_chat(page, job_keyword: str, candidate_name: str, candidate_skill: str) -> bool:
    job_dropdown = None
    for sel in BOSS_JOB_DROPDOWN_SELECTORS:
        try:
            loc = page.locator(sel).first
            if loc.count() > 0:
                job_dropdown = loc
                break
        except Exception:
            pass
    if job_dropdown:
        job_dropdown.click()
        human_wait(page, 0.5, 1.2)
        job_option = page.locator(
            f"li:has-text('{job_keyword}'), [class*='option']:has-text('{job_keyword}'), [class*='item']:has-text('{job_keyword}')"
        ).first
        if job_option.count() > 0:
            job_option.click()
            human_wait(page, 0.3, 0.8)
        else:
            try:
                menu = page.locator("[class*='dropmenu'], [class*='dropdown']")
                if menu.count() > 0:
                    menu.get_by_text(job_keyword, exact=False).first.click()
                human_wait(page, 0.3, 0.8)
            except Exception:
                pass
        human_wait(page, 0.2, 0.5)
    chat_item = None
    for sel in BOSS_CHAT_ITEM_SELECTORS:
        try:
            loc = page.locator(sel).filter(
                has=page.locator(f"span.geek-name:has-text('{candidate_name}')")
            ).filter(
                has=page.locator(f"span.source-job:has-text('{candidate_skill}')")
            ).first
            if loc.count() > 0:
                chat_item = loc
                break
        except Exception:
            try:
                loc = page.locator(sel).filter(
                    has=page.get_by_text(candidate_name)
                ).filter(has=page.get_by_text(candidate_skill)).first
                if loc.count() > 0:
                    chat_item = loc
                    break
            except Exception:
                pass
    if not chat_item or chat_item.count() == 0:
        return False
    chat_item.scroll_into_view_if_needed()
    human_wait(page, 0.15, 0.5)
    chat_item.click()
    human_wait(page, 1.0, 2.0)
    return True
