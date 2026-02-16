"""
com.jachin.files - 文件指挥官
继承 BaseSkill，实现 list_files、search_files
"""

import re
from pathlib import Path
from glob import glob
from typing import Dict, Any, List, Optional

from core.skills.base_skill import BaseSkill


class FilesSkill(BaseSkill):
    """文件指挥官技能"""

    def __init__(self, manifest: Dict[str, Any]):
        super().__init__(manifest)

    async def list_files(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """列出指定目录的文件"""
        try:
            path = params.get("path", ".")
            recursive = params.get("recursive", False)
            pattern = params.get("pattern", "*")
            base_path = Path(path).resolve()
            if not base_path.exists():
                return {"success": False, "error": f"Path does not exist: {path}"}
            if not base_path.is_dir():
                return {"success": False, "error": f"Not a directory: {path}"}
            search = str(base_path / "**" / pattern) if recursive else str(base_path / pattern)
            matched = glob(search, recursive=recursive)
            files = []
            for fp in matched:
                p = Path(fp)
                if p.is_file():
                    stat = p.stat()
                    files.append({"path": str(p), "name": p.name, "size": stat.st_size, "modified": stat.st_mtime})
            return {"success": True, "path": str(base_path), "count": len(files), "files": files}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def search_files(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """搜索文件"""
        try:
            pattern = params.get("pattern", "*")
            path = params.get("path", ".")
            content_pattern = params.get("content_pattern")
            recursive = params.get("recursive", True)
            base_path = Path(path).resolve()
            if not base_path.exists():
                return {"success": False, "error": f"Path does not exist: {path}"}
            search = str(base_path / "**" / pattern) if recursive else str(base_path / pattern)
            matched = glob(search, recursive=recursive)
            results = []
            for fp in matched:
                p = Path(fp)
                if not p.is_file():
                    continue
                if content_pattern:
                    try:
                        with open(p, "r", encoding="utf-8", errors="ignore") as f:
                            if re.search(content_pattern, f.read()):
                                results.append({"path": str(p), "name": p.name, "matched_content": True})
                    except Exception:
                        pass
                else:
                    stat = p.stat()
                    results.append({"path": str(p), "name": p.name, "size": stat.st_size, "modified": stat.st_mtime})
            return {"success": True, "pattern": pattern, "count": len(results), "results": results}
        except Exception as e:
            return {"success": False, "error": str(e)}
