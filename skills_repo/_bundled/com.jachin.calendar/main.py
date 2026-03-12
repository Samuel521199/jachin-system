"""
com.jachin.calendar - 日历管家
本地文件级日历管理
"""

import json
import os
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Any, List

from core.config import settings
from core.skills.base_skill import BaseSkill

_DATA_DIR = settings.JACHIN_DATA_DIR or os.path.expanduser("~/.jachin")
_STORE_PATH = Path(_DATA_DIR) / "calendar_skill.json"


def _load_items() -> List[Dict]:
    _STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not _STORE_PATH.exists():
        return []
    try:
        with open(_STORE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _save_items(items: List[Dict]) -> None:
    _STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_STORE_PATH, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)


class CalendarSkill(BaseSkill):
    """日历管家技能"""

    def __init__(self, manifest: Dict[str, Any]):
        super().__init__(manifest)

    async def add_event(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """添加事件/提醒"""
        try:
            title = params.get("title", "")
            start_at = params.get("start_at", "")
            item_type = params.get("item_type", "reminder")
            if not title or not start_at:
                return {"success": False, "error": "title and start_at required"}
            items = _load_items()
            item = {
                "id": str(uuid.uuid4()),
                "title": title,
                "start_at": start_at,
                "item_type": item_type,
                "is_done": False,
                "created_at": datetime.now().isoformat(),
            }
            items.append(item)
            _save_items(items)
            return {"success": True, "id": item["id"], "message": f"已添加「{title}」"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def check_schedule(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """查看日程"""
        try:
            days = params.get("days", 7)
            items = _load_items()
            now = datetime.now()
            end = now + timedelta(days=days)
            result = []
            for it in items:
                if it.get("is_done"):
                    continue
                try:
                    dt = datetime.fromisoformat(it["start_at"].replace("Z", "+00:00"))
                    if dt.tzinfo:
                        dt = dt.replace(tzinfo=None)
                    if now <= dt <= end:
                        result.append({
                            "id": it["id"],
                            "title": it["title"],
                            "start_at": it["start_at"],
                            "item_type": it.get("item_type", "reminder"),
                        })
                except Exception:
                    pass
            result.sort(key=lambda x: x["start_at"])
            return {"success": True, "items": result[:50]}
        except Exception as e:
            return {"success": False, "error": str(e)}
