"""
PMO 战报第三部分：版本发布需求映射 — 基于 Vivian 发版公告邮件窗口的已完成 Epic。

时间窗：上一封「生产环境发版维护公告」邮件 internal_date → 当前时刻。
数据源：cron_thinker 同款飞书邮箱 API + pmo_raw_records（vewpI8lyYw）。
"""
from __future__ import annotations

import os
import re
from datetime import date, datetime, timezone
from typing import Any
from urllib.parse import quote

from core.cron_thinker import (
    RELEASE_TITLE_NEEDLE,
    _lark_feishu_errcode,
    _lark_http_json_get,
    _mail_lark_http_timeout_sec,
    _mail_mailbox,
    parse_release_maintenance_date,
    release_title_present,
)
from l3_node.pmo_epic_aggregate import epic_completion_pct, group_children_by_epic
from l3_node.tools.pmo_db_tools import _connect, pmo_mirror_db_ready
from l3_node.tools.pmo_sprint_query import (
    _DEFAULT_SOURCE_VIEW,
    _fetch_view_rows,
    _merge_child_task_lists,
    _pack_epic_row,
    _sorted_big_epics,
    _collect_dept_tasks,
    _collect_epic_chain_tasks,
    _CHILD_DEPT_PARENTS,
    _epic_child_from_task,
)

TZ_BEIJING = timezone.utc  # display uses local date strings from ISO fields


def _dash(v: Any) -> str:
    if v is None or v == "" or v == "null":
        return "—"
    s = str(v).strip()
    return s if s else "—"


def _date_mmdd(iso: str | None) -> str:
    if not iso:
        return "—"
    s = str(iso).strip()[:10]
    if len(s) >= 10 and s[4] == "-":
        return f"{s[5:7]}/{s[8:10]}"
    return s


def _ms_to_dt(ms: str | int | None) -> datetime | None:
    if ms is None or ms == "":
        return None
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc)
    except (TypeError, ValueError):
        return None


def _parse_iso_date(s: str | None) -> date | None:
    if not s:
        return None
    raw = str(s).strip()[:10]
    if len(raw) < 10:
        return None
    try:
        return date.fromisoformat(raw.replace("/", "-")[:10])
    except ValueError:
        return None


def _mail_fetch_token_and_base() -> tuple[str, str]:
    from l3_node.channels.lark.client import get_lark_api_base, get_tenant_access_token

    return get_tenant_access_token(), get_lark_api_base().rstrip("/")


def _is_genuine_release_announcement(subject: str, body: str) -> bool:
    """排除回复/验收/确认类误命中，仅保留真正的发版维护公告。"""
    subj = (subject or "").strip()
    if not subj:
        return False
    if subj.startswith(("回复", "Re:", "RE:", "Fwd:", "转发")):
        return False
    if "验收" in subj and "维护公告" not in subj:
        return False
    if "确认" in subj and "维护公告" not in subj:
        return False
    title_ok = (
        RELEASE_TITLE_NEEDLE in subj
        or "生产环境维护公告" in subj
        or ("维护公告" in subj and "生产环境" in subj)
    )
    if not title_ok:
        return False
    return parse_release_maintenance_date(f"{subj}\n{body or ''}") is not None


def _mail_list_message_ids(
    *,
    token: str,
    api_base: str,
    mailbox: str,
    page_size: int,
    folder_id: str = "INBOX",
    max_pages: int = 5,
) -> list[str]:
    enc_box = quote(mailbox, safe="")
    fid = quote((folder_id or "INBOX").strip() or "INBOX", safe="")
    out: list[str] = []
    page_token: str | None = None
    for _ in range(max(1, max_pages)):
        q = [f"page_size={max(1, min(20, page_size))}", f"folder_id={fid}"]
        if page_token:
            q.append(f"page_token={quote(page_token, safe='')}")
        url = f"{api_base}/mail/v1/user_mailboxes/{enc_box}/messages?{'&'.join(q)}"
        data = _lark_http_json_get(url, token, timeout=_mail_lark_http_timeout_sec())
        if _lark_feishu_errcode(data) != 0:
            raise RuntimeError(f"列出邮件失败: {data.get('msg')!s}")
        inner = data.get("data") or {}
        items = inner.get("items")
        if isinstance(items, list):
            out.extend(str(i) for i in items if i)
        if not inner.get("has_more"):
            break
        page_token = str(inner.get("page_token") or "").strip() or None
        if not page_token:
            break
    return out


def _mail_get_message_full(
    *,
    token: str,
    api_base: str,
    mailbox: str,
    message_id: str,
) -> dict[str, Any]:
    from core.cron_thinker import _b64url_to_str

    enc_box = quote(mailbox, safe="")
    enc_mid = quote(message_id, safe="")
    url = (
        f"{api_base}/mail/v1/user_mailboxes/{enc_box}/messages/{enc_mid}"
        "?format=plain_text_full"
    )
    data = _lark_http_json_get(url, token, timeout=_mail_lark_http_timeout_sec())
    if _lark_feishu_errcode(data) != 0:
        raise RuntimeError(f"获取邮件详情失败: {data.get('msg')!s}")
    msg = (data.get("data") or {}).get("message")
    if not isinstance(msg, dict):
        return {}
    subj = str(msg.get("subject") or "")
    body = _b64url_to_str(msg.get("body_plain_text"))
    if not body.strip():
        body = _b64url_to_str(msg.get("body_preview"))
    return {
        "message_id": message_id,
        "subject": subj,
        "body": body,
        "internal_date": msg.get("internal_date"),
        "internal_dt": _ms_to_dt(msg.get("internal_date")),
    }


def _dedupe_release_mails_by_maintenance(
    mails: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """同一维护日多封邮件时保留最早一封（正式公告）。"""
    by_maint: dict[str, dict[str, Any]] = {}
    for m in mails:
        md = str(m.get("maintenance_date") or "").strip()
        if not md:
            continue
        prev = by_maint.get(md)
        cur_dt = m.get("internal_dt") or datetime.max.replace(tzinfo=timezone.utc)
        if prev is None:
            by_maint[md] = m
            continue
        prev_dt = prev.get("internal_dt") or datetime.max.replace(tzinfo=timezone.utc)
        if cur_dt < prev_dt:
            by_maint[md] = m
    out = list(by_maint.values())
    out.sort(
        key=lambda x: (
            str(x.get("maintenance_date") or ""),
            x.get("internal_dt") or datetime.min.replace(tzinfo=timezone.utc),
        ),
        reverse=True,
    )
    return out


def fetch_release_announcement_mails(
    *,
    mailbox: str | None = None,
    page_size: int = 20,
    max_pages: int = 5,
    app_id: str | None = None,
    app_secret: str | None = None,
) -> list[dict[str, Any]]:
    """
    拉取邮箱内匹配发版公告标题的邮件（含 internal_date），按维护日降序（去重后）。
    """
    if app_id and app_secret:
        os.environ["LARK_APP_ID"] = app_id.strip()
        os.environ["LARK_APP_SECRET"] = app_secret.strip()
    mailbox = (mailbox or _mail_mailbox()).strip()
    token, api_base = _mail_fetch_token_and_base()
    ids = _mail_list_message_ids(
        token=token,
        api_base=api_base,
        mailbox=mailbox,
        page_size=max(1, min(20, page_size)),
        max_pages=max_pages,
    )
    hits: list[dict[str, Any]] = []
    for mid in ids:
        detail = _mail_get_message_full(
            token=token,
            api_base=api_base,
            mailbox=mailbox,
            message_id=mid,
        )
        if not detail:
            continue
        subj = str(detail.get("subject") or "")
        body = str(detail.get("body") or "")
        if not _is_genuine_release_announcement(subj, body):
            continue
        raw = f"{subj}\n{body}"
        maint = parse_release_maintenance_date(raw)
        detail["maintenance_date"] = maint.isoformat() if maint else None
        detail["title_needle"] = RELEASE_TITLE_NEEDLE
        hits.append(detail)
    return _dedupe_release_mails_by_maintenance(hits)


def resolve_release_window(
    mails: list[dict[str, Any]],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """
    确定 Epic 完成统计窗口。

    - ``since``：上一封（第二新）发版公告邮件时间；仅一封时用该封时间。
    - ``until``：当前时刻。
    """
    now = now or datetime.now(tz=timezone.utc)
    sorted_mails = list(mails)
    if not sorted_mails:
        return {
            "ok": False,
            "reason": "no_release_mail_found",
            "since": None,
            "until": now,
            "since_mail": None,
            "latest_mail": None,
        }
    latest = sorted_mails[0]
    # 当前发版周期 = 最新维护日公告；统计窗起点 = 上一维护日公告发出时刻
    since_mail = sorted_mails[1] if len(sorted_mails) > 1 else sorted_mails[0]
    since_dt = since_mail.get("internal_dt")
    if since_dt is None:
        maint = since_mail.get("maintenance_date")
        if maint:
            try:
                since_dt = datetime.combine(
                    date.fromisoformat(str(maint)[:10]),
                    datetime.min.time(),
                    tzinfo=timezone.utc,
                )
            except ValueError:
                since_dt = None
    return {
        "ok": True,
        "since": since_dt,
        "until": now,
        "since_mail": {
            "message_id": since_mail.get("message_id"),
            "subject": since_mail.get("subject"),
            "internal_date": since_mail.get("internal_date"),
            "maintenance_date": since_mail.get("maintenance_date"),
        },
        "latest_mail": {
            "message_id": latest.get("message_id"),
            "subject": latest.get("subject"),
            "internal_date": latest.get("internal_date"),
            "maintenance_date": latest.get("maintenance_date"),
        },
        "mail_count": len(sorted_mails),
    }


def _epic_completion_date(epic: dict[str, Any], children: list[dict[str, Any]]) -> date | None:
    dates: list[date] = []
    for row in [epic, *children]:
        for k in ("actual_delivery_date", "acceptance_date", "review_date", "expected_delivery_date"):
            d = _parse_iso_date(row.get(k))
            if d:
                dates.append(d)
    return max(dates) if dates else None


def _load_all_epics_with_children(
    *,
    source_view: str = _DEFAULT_SOURCE_VIEW,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    conn = _connect()
    try:
        rows = _fetch_view_rows(conn, source_view=source_view)
    finally:
        conn.close()
    epic_sorted, epic_indices = _sorted_big_epics(rows)
    epic_chain = _collect_epic_chain_tasks(rows, epic_indices)
    all_children: list[dict[str, Any]] = []
    for dept_parent in _CHILD_DEPT_PARENTS:
        collected = _collect_dept_tasks(rows, epic_indices, dept_parent)
        chain_slice = [t for t in epic_chain if t.get("department") == dept_parent]
        all_children.extend(_merge_child_task_lists(collected, chain_slice))
    epics = [_pack_epic_row(f) for _, f in epic_sorted]
    return epics, [_epic_child_from_task(t) for t in all_children]


def find_completed_epics_in_window(
    *,
    since: datetime | None,
    until: datetime | None = None,
    source_view: str = _DEFAULT_SOURCE_VIEW,
) -> list[dict[str, Any]]:
    """返回窗口内完成度 100% 且完成日期落在 [since, until] 的 Epic（去重按 epic_name）。"""
    until = until or datetime.now(tz=timezone.utc)
    since_date = since.date() if since else None
    until_date = until.date() if until else date.today()

    epics, all_children = _load_all_epics_with_children(source_view=source_view)
    children_by_epic = group_children_by_epic(all_children)

    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for epic in epics:
        name = str(epic.get("epic_name") or "").strip()
        if not name or name in seen:
            continue
        kids = list(children_by_epic.get(name, []))
        pct = epic_completion_pct(epic, kids)
        if pct < 100:
            continue
        comp_date = _epic_completion_date(epic, kids)
        if since_date and comp_date and comp_date < since_date:
            continue
        if comp_date and comp_date > until_date:
            continue
        seen.add(name)
        out.append(
            {
                "epic_name": name,
                "priority": epic.get("priority"),
                "sprint": epic.get("sprint"),
                "completion_pct": pct,
                "completion_date": comp_date.isoformat() if comp_date else None,
                "person": epic.get("person"),
                "task_no": epic.get("task_no"),
                "status": epic.get("status"),
            }
        )
    out.sort(
        key=lambda e: (
            e.get("completion_date") or "",
            str(e.get("priority") or "Z"),
            e.get("epic_name") or "",
        ),
        reverse=True,
    )
    return out


def build_release_mapping_markdown(
    *,
    completed_epics: list[dict[str, Any]],
    window: dict[str, Any],
    mailbox: str | None = None,
) -> str:
    """组装 📦 版本发布需求映射 GFM 区块。"""
    since_mail = window.get("since_mail") or {}
    since_dt = window.get("since")
    until_dt = window.get("until")
    since_s = (
        since_dt.strftime("%Y-%m-%d %H:%M UTC")
        if isinstance(since_dt, datetime)
        else "—"
    )
    until_s = (
        until_dt.strftime("%Y-%m-%d %H:%M UTC")
        if isinstance(until_dt, datetime)
        else "—"
    )
    subj = _dash(since_mail.get("subject"))
    if len(subj) > 48:
        subj = subj[:45] + "…"
    note = (
        f"统计窗：自上一封发版公告（{subj} · {since_s}）至 {until_s}；"
        f"邮箱 {mailbox or _mail_mailbox()}；"
        f"共 **{len(completed_epics)}** 个顶层 Epic 完成（完成度 100%）"
    )

    rows: list[str] = []
    for i, epic in enumerate(completed_epics[:30], 1):
        prio = str(epic.get("priority") or "—").strip()
        if prio and prio.upper().startswith("P") and not prio.startswith("【"):
            prio = f"【{prio.upper()}】"
        name = str(epic.get("epic_name") or "—")
        sprint = _dash(epic.get("sprint"))
        comp = _date_mmdd(epic.get("completion_date"))
        person = _dash(epic.get("person"))
        rows.append(f"| {i} | {prio} {name} | {sprint} | {comp} | {person} |")

    if not rows:
        rows.append("| — | ⚠️ 窗口内无完成度 100% 的 Epic | — | — | — |")

    return "\n".join(
        [
            "### **📦 版本发布需求映射**",
            f"**口径**：{note}",
            "",
            "| # | 大需求 (Epic) | Sprint | 完成日期 | 负责人 |",
            "| --- | --- | --- | --- | --- |",
            *rows,
        ]
    )


def run_release_epic_mapping(
    *,
    app_id: str | None = None,
    app_secret: str | None = None,
    mailbox: str | None = None,
    page_size: int = 20,
) -> dict[str, Any]:
    """一站式：拉邮件 → 定窗口 → 查已完成 Epic → 返回 Markdown 片段与明细。"""
    if not pmo_mirror_db_ready():
        return {
            "status": "failed",
            "error": "pmo_raw_records 为空，请先 INIT（core:pmo_mirror_import）",
        }
    try:
        mails = fetch_release_announcement_mails(
            mailbox=mailbox,
            page_size=page_size,
            app_id=app_id,
            app_secret=app_secret,
        )
    except Exception as e:
        return {
            "status": "failed",
            "error": f"拉取发版邮件失败: {e}",
            "error_class": "transient",
        }

    window = resolve_release_window(mails)
    completed = find_completed_epics_in_window(since=window.get("since"))
    md = build_release_mapping_markdown(
        completed_epics=completed,
        window=window,
        mailbox=mailbox,
    )
    return {
        "status": "ok",
        "mailbox": mailbox or _mail_mailbox(),
        "window": window,
        "completed_epics": completed,
        "completed_count": len(completed),
        "release_mails_found": len(mails),
        "markdown_section": md,
    }
