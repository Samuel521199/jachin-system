"""
com.jachin.web-surfer - 联网冲浪
使用 duckduckgo-search 和 requests 实现搜索与网页阅读
"""

import logging
from typing import Dict, Any

from core.skills.base_skill import BaseSkill

logger = logging.getLogger(__name__)


class WebSurferSkill(BaseSkill):
    """联网冲浪技能"""

    def __init__(self, manifest: Dict[str, Any]):
        super().__init__(manifest)

    async def web_search(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """搜索互联网"""
        try:
            from duckduckgo_search import DDGS
            query = params.get("query", "")
            if not query:
                return {"success": False, "error": "query is required"}
            results = []
            with DDGS() as ddgs:
                for r in ddgs.text(query, max_results=5):
                    results.append({
                        "title": r.get("title", ""),
                        "href": r.get("href", ""),
                        "body": r.get("body", ""),
                    })
            return {"success": True, "query": query, "results": results}
        except ImportError:
            return {"success": False, "error": "duckduckgo-search not installed. pip install duckduckgo-search"}
        except Exception as e:
            logger.error(f"web_search failed: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def read_url(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """读取网页内容"""
        try:
            import requests
            url = params.get("url", "")
            if not url:
                return {"success": False, "error": "url is required"}
            r = requests.get(url, timeout=10, headers={"User-Agent": "Jachin-WebSurfer/1.0"})
            r.raise_for_status()
            text = r.text[:8000]  # 限制长度
            return {"success": True, "url": url, "content": text}
        except ImportError:
            return {"success": False, "error": "requests not installed"}
        except Exception as e:
            logger.error(f"read_url failed: {e}", exc_info=True)
            return {"success": False, "error": str(e)}
