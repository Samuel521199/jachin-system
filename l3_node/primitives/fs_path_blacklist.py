"""
Native fs_read 敏感路径黑名单（底线防御，读宽写严）。

写入仍由 native_write_allowlist 白名单约束；读取为「非黑名单即允许」。
匹配忽略大小写；避免将 bootstrap、myboot 等目录名误当作 /boot。
"""
from __future__ import annotations

import re
from pathlib import Path

# 供设置页 / API 展示内置读取黑名单规则（与下方 is_read_path_blacklisted 逻辑一致，简述）
READ_BLACKLIST_BUILTIN_LINES: list[str] = [
    "密钥与云凭证目录：.ssh、.aws、.kube、.gnupg",
    "环境变量文件：路径段含 .env 或 credentials",
    "Windows 系统目录（含 System32、SysWOW64、WindowsApps 及 C:\\Windows\\…）",
    "SAM/SECURITY 注册表配置单元",
    "路径段名为 etc（含 C:\\etc、/etc/…）",
    "Linux /boot（根下 boot 段）及盘符根下 Boot",
    "/var/log 及 /etc/shadow、/etc/passwd",
    "Chromium 系浏览器用户数据中的 Cookies / Login Data",
]


def _parts_lower(resolved: Path) -> list[str]:
    return [
        c.replace("\\", "/").strip("/").lower()
        for c in resolved.parts
        if c and c not in ("/", "\\")
    ]


def is_read_path_blacklisted(resolved: Path) -> bool:
    """
    若路径落在敏感/系统/密钥/浏览器凭据区域，返回 True（禁止读取）。
    """
    try:
        parts = _parts_lower(resolved)
        n = str(resolved).replace("\\", "/").lower()
    except (OSError, RuntimeError, ValueError):
        return True

    # —— 密钥 / 云凭证 / GPG / K8s ——
    if any(p in (".ssh", ".aws", ".kube", ".gnupg") for p in parts):
        return True

    # —— 环境变量与凭据目录名 ——
    if any(p == ".env" or p.endswith(".env") for p in parts):
        return True
    if any(p == "credentials" for p in parts):
        return True

    # —— Windows 系统核心 ——
    if any(p in ("system32", "syswow64", "windowsapps") for p in parts):
        return True
    if len(parts) >= 2 and parts[1] == "windows":
        return True
    if re.search(r"^[a-z]:/windows(/|$)", n):
        return True

    # SAM / SECURITY（系统配置单元）
    if "windows" in n and "system32" in n and "config" in n:
        if any(x in n for x in ("config/sam", "config/security")):
            return True

    # —— 路径段名为 etc（含 C:\etc\…、…/etc/…）——
    if any(p == "etc" for p in parts):
        return True

    # —— Linux /boot（仅根下 boot 段，不误伤 …/bootstrap/…）——
    if len(parts) >= 2 and parts[0] == "/" and parts[1] == "boot":
        return True
    # Windows 盘符根下 Boot：如 D:\boot\…
    if len(parts) >= 2 and re.match(r"^[a-z]:$", parts[0]) and parts[1] == "boot":
        return True

    # —— /var/log ——
    for i in range(len(parts) - 1):
        if parts[i] == "var" and parts[i + 1] == "log":
            return True

    # —— shadow / passwd ——
    if n.endswith("/etc/shadow") or n.endswith("/etc/passwd") or "/etc/shadow" in n:
        return True
    if len(parts) >= 2 and parts[1] == "etc" and parts[-1] == "shadow":
        return True

    # —— 浏览器用户数据（Chromium 系）——
    if "user data" in n and "default" in n:
        if "cookies" in n or "login data" in n:
            return True

    # —— 用户扩展禁止读取根（native_fs_policy.json）——
    try:
        from l3_node.primitives.native_fs_policy_store import get_read_blacklist_extra_roots

        for root in get_read_blacklist_extra_roots():
            try:
                if resolved.is_relative_to(root):
                    return True
            except ValueError:
                continue
    except Exception:
        pass

    return False
