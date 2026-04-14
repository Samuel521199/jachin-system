"""
飞书 IM 发送前：接收者 ID 校验，以及邮箱/手机 → open_id（contact/v3/users/batch_get_id）。

避免模型把人名（如 vivian）当作 chat_id 直接调用发送接口。
"""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

FEISHU_CONTACT_PUBLISH_HINT = (
    " 若权限已在后台「已开通」仍报错：请到飞书开发者后台「版本管理与发布」创建新版本并发布；"
    "企业须安装该应用版本；目标用户须在应用可用范围内。"
    "tenant_access_token 每次请求重新签发，一般无需「清缓存」；发布后等待数分钟或重启 L3 再试。"
)

# batch_get_id 文档：调用权限可为「通过手机号或邮箱获取用户 ID」= contact:user.id:readonly，
# 或以应用身份读通讯录等（见开放平台该接口「权限要求」表）。与 contact:user.email:readonly 等字段权限不是同一条。
FEISHU_BATCH_GET_ID_SCOPE_CLARIFICATION = (
    " 权限说明：接口 contact/v3/users/batch_get_id 要求的 **contact:user.id:readonly** 对应开发者后台中文 **「通过手机号或邮箱获取用户 ID」**；"
    "**「获取用户邮箱信息」「获取用户基本信息」「获取用户 user ID(employee_id)」等是其它 scope，不能替代本条。**"
    "文档亦允许改用「以应用身份读取通讯录」等同级权限之一（以开放平台该接口最新说明为准）。"
)


def looks_like_email(s: str) -> bool:
    t = (s or "").strip()
    if "@" not in t or len(t) > 320:
        return False
    return bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", t))


def normalize_mobile_for_feishu(raw: str) -> str | None:
    """转为飞书 batch_get_id 常用格式 +86…（中国大陆 11 位）。"""
    t = (raw or "").strip()
    if not t:
        return None
    d = re.sub(r"\D", "", t)
    if len(d) == 11 and d.startswith("1"):
        return "+86" + d
    if len(d) == 13 and d.startswith("86") and d[2] == "1":
        return "+" + d
    return None


_display_alias_map: dict[str, str] | None = None


def load_display_name_alias_map() -> dict[str, str]:
    """英文名/昵称 → oc_/ou_/邮箱；环境变量 LARK_DISPLAY_NAME_MAP JSON 或 ~/.jachin/lark_display_name_map.json。"""
    global _display_alias_map
    if _display_alias_map is not None:
        return _display_alias_map
    out: dict[str, str] = {}
    raw = (os.environ.get("LARK_DISPLAY_NAME_MAP") or "").strip()
    if raw.startswith("{"):
        try:
            d = json.loads(raw)
            if isinstance(d, dict):
                for k, v in d.items():
                    if isinstance(k, str) and isinstance(v, str) and k.strip() and v.strip():
                        out[k.strip().lower()] = v.strip()
        except json.JSONDecodeError:
            pass
    path = (os.environ.get("LARK_DISPLAY_NAME_MAP_FILE") or "").strip()
    if not path:
        p = Path.home() / ".jachin" / "lark_display_name_map.json"
        if p.is_file():
            path = str(p)
    if path:
        try:
            d = json.loads(Path(path).read_text(encoding="utf-8"))
            if isinstance(d, dict):
                for k, v in d.items():
                    if isinstance(k, str) and isinstance(v, str) and k.strip() and v.strip():
                        out[k.strip().lower()] = v.strip()
        except Exception:
            pass
    _display_alias_map = out
    return out


def resolve_display_name_to_id(name: str) -> str | None:
    t = (name or "").strip().lower()
    if not t:
        return None
    return load_display_name_alias_map().get(t)


def is_contact_scope_denied_payload(data: dict[str, Any]) -> bool:
    msg = str(data.get("msg") or data.get("error") or "").lower()
    if "access denied" in msg and ("scope" in msg or "contact:user.id" in msg):
        return True
    return "contact:user.id" in msg


def is_likely_display_name_not_id(s: str, receive_id_type: str) -> bool:
    """
    明显不是飞书 ID、邮箱、手机号的短字符串（如 vivian、张三），用于拦截误填。
    当 receive_id_type 为 user_id/union_id 时不做此启发式（格式多样）。
    """
    if receive_id_type in ("user_id", "union_id"):
        return False
    t = (s or "").strip()
    if not t or len(t) > 128:
        return False
    if t.startswith(("oc_", "ou_", "on_", "om_")):
        return False
    if looks_like_email(t):
        return False
    if normalize_mobile_for_feishu(t):
        return False
    # 已像飞书常见 token（含下划线、较长十六进制片段）
    if "_" in t and len(t) >= 12:
        return False
    # 纯昵称：字母/中文/点，无 @，长度较短
    if re.match(r"^[\w\u4e00-\u9fff.·\s-]{1,40}$", t) and "@" not in t:
        return True
    return False


def feishu_batch_get_id(
    *,
    token: str,
    api_base: str,
    emails: list[str] | None = None,
    mobiles: list[str] | None = None,
) -> dict[str, Any]:
    """POST /contact/v3/users/batch_get_id"""
    try:
        import requests
    except ImportError:
        return {"code": -1, "msg": "缺少 requests"}

    base = (api_base or "").rstrip("/")
    url = f"{base}/contact/v3/users/batch_get_id"
    body: dict[str, Any] = {"include_resigned": False}
    if emails:
        body["emails"] = emails[:50]
    if mobiles:
        body["mobiles"] = mobiles[:50]
    if not body.get("emails") and not body.get("mobiles"):
        return {"code": -1, "msg": "emails/mobiles 为空"}

    resp = requests.post(
        url,
        params={"user_id_type": "open_id"},
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        },
        json=body,
        timeout=15,
    )
    try:
        return resp.json()
    except Exception as e:
        return {"code": -1, "msg": str(e)}


def pick_open_id_from_batch_get(data: dict[str, Any]) -> tuple[str | None, str | None]:
    """返回 (open_id, error_message)。"""
    if data.get("code") != 0:
        err = str(data.get("msg") or data)
        if is_contact_scope_denied_payload(data):
            err = err + FEISHU_BATCH_GET_ID_SCOPE_CLARIFICATION + FEISHU_CONTACT_PUBLISH_HINT
        return None, err
    ulist = (data.get("data") or {}).get("user_list") or []
    if not ulist:
        return (
            None,
            "batch_get_id 未返回用户（该邮箱/手机在租户通讯录中不存在、无权限，或邮箱为飞书不支持的类型；"
            "官方文档注明 emails 对部分企业邮箱有限制，可改用手机号或让对方提供 ou_）。",
        )
    u0 = ulist[0]
    oid = (u0.get("open_id") or "").strip()
    if not oid:
        return None, "batch_get_id 返回中缺少 open_id"
    return oid, None


def resolve_email_or_mobile_to_open_id(
    *,
    token: str,
    api_base: str,
    hint: str,
) -> tuple[str | None, str | None]:
    """hint 为邮箱或手机号字符串。"""
    if looks_like_email(hint):
        data = feishu_batch_get_id(token=token, api_base=api_base, emails=[hint.strip()])
        return pick_open_id_from_batch_get(data)
    mob = normalize_mobile_for_feishu(hint)
    if mob:
        data = feishu_batch_get_id(token=token, api_base=api_base, mobiles=[mob])
        return pick_open_id_from_batch_get(data)
    return None, None


def feishu_search_v1_user_get(
    token: str,
    api_base: str,
    query: str,
    *,
    page_size: int = 50,
) -> dict[str, Any]:
    """GET /search/v1/user — 官方文档多要求 user_access_token；部分租户下 tenant 亦可试。"""
    try:
        import requests
    except ImportError:
        return {"code": -1, "msg": "缺少 requests"}

    base = (api_base or "").rstrip("/")
    params: dict[str, Any] = {"query": (query or "").strip(), "page_size": max(1, min(200, page_size))}
    resp = requests.get(
        f"{base}/search/v1/user",
        params=params,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        },
        timeout=20,
    )
    try:
        return resp.json()
    except Exception as e:
        return {"code": -1, "msg": str(e)}


def feishu_contact_v3_users_list_page(
    token: str,
    api_base: str,
    *,
    page_token: str | None = None,
    page_size: int = 100,
) -> dict[str, Any]:
    """GET /contact/v3/users — 应用身份分页拉取通讯录内用户（受数据权限范围限制）。"""
    try:
        import requests
    except ImportError:
        return {"code": -1, "msg": "缺少 requests"}

    base = (api_base or "").rstrip("/")
    q: dict[str, Any] = {"user_id_type": "open_id", "page_size": max(1, min(100, page_size))}
    if page_token:
        q["page_token"] = page_token
    resp = requests.get(
        f"{base}/contact/v3/users",
        params=q,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        },
        timeout=25,
    )
    try:
        return resp.json()
    except Exception as e:
        return {"code": -1, "msg": str(e)}


def _user_row_matches_query(row: dict[str, Any], ql: str) -> bool:
    name = str(row.get("name") or "").strip().lower()
    en = str(row.get("en_name") or "").strip().lower()
    em = str(row.get("email") or "").strip().lower()
    if not ql:
        return False
    if ql == name or ql == en:
        return True
    if ql in name or ql in en:
        return True
    if em and ql in em.split("@", 1)[0]:
        return True
    return False


def search_user_candidates_by_name(
    query: str,
    *,
    tenant_token: str,
    api_base: str,
) -> tuple[list[dict[str, Any]], str | None]:
    """
    按「展示名/英文名」解析用户：先 search/v1/user（优先 LARK_FEISHU_USER_ACCESS_TOKEN），
    再回退 GET /contact/v3/users 分页本地匹配。

    返回 (candidates, fatal_error)；candidates 每项含 open_id, name, en_name, email。
    """
    ql = (query or "").strip().lower()
    if not ql:
        return [], "搜索关键词为空"

    ut = (os.environ.get("LARK_FEISHU_USER_ACCESS_TOKEN") or "").strip()
    tokens_to_try: list[tuple[str, str]] = []
    if ut:
        tokens_to_try.append((ut, "LARK_FEISHU_USER_ACCESS_TOKEN"))
    tokens_to_try.append((tenant_token, "tenant_access_token"))

    seen: set[str] = set()
    out: list[dict[str, Any]] = []

    for tok, _label in tokens_to_try:
        data = feishu_search_v1_user_get(tok, api_base, query)
        if data.get("code") != 0:
            continue
        users = (data.get("data") or {}).get("users") or []
        for u in users:
            if not isinstance(u, dict):
                continue
            oid = str(u.get("open_id") or "").strip()
            if not oid or oid in seen:
                continue
            seen.add(oid)
            out.append(
                {
                    "open_id": oid,
                    "name": str(u.get("name") or ""),
                    "en_name": str(u.get("en_name") or ""),
                    "email": str(u.get("email") or ""),
                }
            )
        if out:
            return out, None

    try:
        max_pages = int(os.environ.get("JACHIN_LARK_USER_LIST_SEARCH_MAX_PAGES", "12"))
    except ValueError:
        max_pages = 12
    max_pages = max(1, min(50, max_pages))
    page_token: str | None = None
    last_err: str | None = None
    for _ in range(max_pages):
        data = feishu_contact_v3_users_list_page(
            tenant_token, api_base, page_token=page_token, page_size=100
        )
        if data.get("code") != 0:
            last_err = str(data.get("msg") or data)
            break
        ddata = data.get("data") or {}
        for it in ddata.get("items") or []:
            if not isinstance(it, dict):
                continue
            oid = str(it.get("open_id") or "").strip()
            if not oid or oid in seen:
                continue
            if _user_row_matches_query(it, ql):
                seen.add(oid)
                out.append(
                    {
                        "open_id": oid,
                        "name": str(it.get("name") or ""),
                        "en_name": str(it.get("en_name") or ""),
                        "email": str(it.get("email") or ""),
                    }
                )
        if not ddata.get("has_more"):
            break
        page_token = ddata.get("page_token")
        if not page_token:
            break

    if not out and last_err:
        return [], last_err + (
            " 若需使用官方「搜索用户」接口，请在环境变量配置 LARK_FEISHU_USER_ACCESS_TOKEN（user_access_token），"
            "并开通 contact:user:search。"
        )
    return out, None


def normalize_lark_im_receive(
    receive_id: str,
    receive_id_type: str,
    *,
    token: str,
    api_base: str,
) -> tuple[str, str, str | None]:
    """
    将「模型可能填错的接收者」规范为飞书 im/v1/messages 可接受的 receive_id + receive_id_type。

    返回 (receive_id, receive_id_type, error_or_none)。
    """
    rid = (receive_id or "").strip()
    rt = (receive_id_type or "chat_id").strip().lower()
    if rt not in ("chat_id", "open_id", "user_id", "union_id", "email"):
        rt = "chat_id"

    if not rid:
        return "", rt, "接收者 ID 为空"

    _mapped = resolve_display_name_to_id(rid)
    if _mapped:
        rid = _mapped
        if rid.startswith("ou_"):
            return rid, "open_id", None
        if rid.startswith("oc_"):
            return rid, "chat_id", None
        # 映射为邮箱等则继续走下方解析

    # 显式类型：尽量信任（user_id/union_id 形态多变，不做昵称拦截）
    if rt == "email":
        if not looks_like_email(rid):
            return rid, rt, "receive_id_type=email 时 receive_id 须为合法邮箱"
        return rid, "email", None

    if rt == "user_id":
        return rid, "user_id", None

    if rt == "union_id":
        return rid, "union_id", None

    if rt == "chat_id":
        if rid.startswith("oc_"):
            return rid, "chat_id", None
        if looks_like_email(rid):
            oid, err = resolve_email_or_mobile_to_open_id(token=token, api_base=api_base, hint=rid)
            if err or not oid:
                return "", "open_id", err or "无法将邮箱解析为 open_id"
            return oid, "open_id", None
        mob = normalize_mobile_for_feishu(rid)
        if mob:
            oid, err = resolve_email_or_mobile_to_open_id(token=token, api_base=api_base, hint=rid)
            if err or not oid:
                return "", "open_id", err or "无法将手机号解析为 open_id"
            return oid, "open_id", None
        if is_likely_display_name_not_id(rid, "chat_id"):
            cands, serr = search_user_candidates_by_name(rid, tenant_token=token, api_base=api_base)
            if serr and not cands:
                return "", "chat_id", f"按姓名自动搜索用户失败：{serr}"
            if len(cands) == 1:
                return cands[0]["open_id"], "open_id", None
            if len(cands) > 1:
                lines = [
                    f"- {u.get('name','') or '-'} / {u.get('en_name','') or '-'} | 邮箱:{u.get('email','') or '-'} | open_id:{u.get('open_id','')}"
                    for u in cands[:15]
                ]
                return (
                    "",
                    "chat_id",
                    "找到多个匹配用户，请确认是哪一个：\n"
                    + "\n".join(lines)
                    + "\n也可在 LARK_DISPLAY_NAME_MAP 中为该人配置唯一 open_id，或让用户提供邮箱。",
                )
            return (
                "",
                "chat_id",
                f"未能在飞书通讯录中搜到名为「{rid}」的用户。请核对姓名、提供邮箱/手机/open_id，"
                "或配置 LARK_FEISHU_USER_ACCESS_TOKEN（user_access_token）以启用 search/v1/user。",
            )
        return rid, "chat_id", None

    if rt == "open_id":
        if rid.startswith("ou_"):
            return rid, "open_id", None
        if looks_like_email(rid) or normalize_mobile_for_feishu(rid):
            oid, err = resolve_email_or_mobile_to_open_id(token=token, api_base=api_base, hint=rid)
            if err or not oid:
                return "", "open_id", err or "无法解析为 open_id"
            return oid, "open_id", None
        if is_likely_display_name_not_id(rid, "open_id"):
            cands, serr = search_user_candidates_by_name(rid, tenant_token=token, api_base=api_base)
            if serr and not cands:
                return "", "open_id", f"按姓名自动搜索用户失败：{serr}"
            if len(cands) == 1:
                return cands[0]["open_id"], "open_id", None
            if len(cands) > 1:
                lines = [
                    f"- {u.get('name','') or '-'} / {u.get('en_name','') or '-'} | 邮箱:{u.get('email','') or '-'} | open_id:{u.get('open_id','')}"
                    for u in cands[:15]
                ]
                return (
                    "",
                    "open_id",
                    "找到多个匹配用户，请确认是哪一个：\n"
                    + "\n".join(lines)
                    + "\n也可配置 LARK_DISPLAY_NAME_MAP 唯一映射。",
                )
            return (
                "",
                "open_id",
                f"未能在飞书通讯录中搜到名为「{rid}」的用户。请核对姓名、提供邮箱/手机/open_id，"
                "或配置 LARK_FEISHU_USER_ACCESS_TOKEN。",
            )
        return rid, "open_id", None

    return rid, rt, None
