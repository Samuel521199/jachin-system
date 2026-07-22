"""Shared message contact book for voice/text recipient disambiguation."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


DEFAULT_MESSAGE_CONTACTS: list[dict[str, Any]] = [
    {
        "name": "Neil",
        "kind": "person",
        "aliases": ["Neil", "new", "n"],
        "shortcut_number": "1",
        "shortcut_letter": "A",
        "enabled": True,
    },
    {
        "name": "Vivian",
        "kind": "person",
        "aliases": ["Vivian", "v"],
        "shortcut_number": "2",
        "shortcut_letter": "B",
        "enabled": True,
    },
    {
        "name": "测试备注冒烟草稿",
        "kind": "group",
        "aliases": ["测试备注冒烟草稿", "测试备注", "测试群", "群聊", "群"],
        "shortcut_number": "3",
        "shortcut_letter": "C",
        "enabled": True,
    },
]


def message_contacts_path() -> Path:
    override = os.environ.get("JACHIN_MESSAGE_CONTACTS_PATH", "").strip()
    if override:
        return Path(override)
    home = os.environ.get("USERPROFILE") or os.environ.get("HOME") or str(Path.home())
    return Path(home) / ".jachin" / "config" / "message_contacts.json"


def load_message_contacts() -> list[dict[str, Any]]:
    path = message_contacts_path()
    if not path.exists():
        return [dict(item) for item in DEFAULT_MESSAGE_CONTACTS]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return [dict(item) for item in DEFAULT_MESSAGE_CONTACTS]
    raw_contacts = data.get("contacts") if isinstance(data, dict) else data
    if not isinstance(raw_contacts, list):
        return [dict(item) for item in DEFAULT_MESSAGE_CONTACTS]
    contacts: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_contacts):
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or "").strip()
        if not name:
            continue
        aliases = raw.get("aliases")
        if isinstance(aliases, str):
            alias_list = [item.strip() for item in aliases.split(",") if item.strip()]
        elif isinstance(aliases, list):
            alias_list = [str(item).strip() for item in aliases if str(item).strip()]
        else:
            alias_list = []
        shortcut_number = str(raw.get("shortcut_number") or raw.get("number") or index + 1).strip()
        shortcut_letter = str(raw.get("shortcut_letter") or raw.get("letter") or chr(ord("A") + index)).strip()
        contacts.append(
            {
                "name": name,
                "kind": str(raw.get("kind") or "person").strip() or "person",
                "aliases": alias_list,
                "shortcut_number": shortcut_number,
                "shortcut_letter": shortcut_letter.upper()[:2],
                "enabled": bool(raw.get("enabled", True)),
            }
        )
    enabled = [item for item in contacts if item.get("enabled")]
    return enabled or [dict(item) for item in DEFAULT_MESSAGE_CONTACTS]


def message_contact_options() -> list[tuple[str, str, str]]:
    options: list[tuple[str, str, str]] = []
    for index, contact in enumerate(load_message_contacts()):
        number = str(contact.get("shortcut_number") or index + 1).strip() or str(index + 1)
        letter = str(contact.get("shortcut_letter") or chr(ord("A") + index)).strip().upper() or chr(ord("A") + index)
        name = str(contact.get("name") or "").strip()
        if name:
            options.append((number, letter, name))
    return options


def message_contact_alias_map() -> dict[str, str]:
    out: dict[str, str] = {}
    for number, letter, name in message_contact_options():
        out[_norm(number)] = name
        out[_norm(letter)] = name
        out[_norm(name)] = name
    for contact in load_message_contacts():
        name = str(contact.get("name") or "").strip()
        aliases = contact.get("aliases") if isinstance(contact.get("aliases"), list) else []
        for alias in aliases:
            key = _norm(str(alias))
            if key and name:
                out[key] = name
    return out


def _norm(text: str) -> str:
    return str(text or "").strip().lower()
