"""
Skill Loader - 智能判断加载路径 (Topology §3.3)

Super Node (standalone + IS_BRAIN_LOCAL):
  - 直接使用 pathlib 读取 SKILLS_REPO_PATH，跳过 HTTP 下载，零拷贝秒级加载

Distributed Cluster:
  - 通过 HTTP/RPC 从 L2 拉取技能资源
"""

import logging
from pathlib import Path
from typing import Optional, Tuple, Union

from core.config import settings

logger = logging.getLogger(__name__)


def _is_local_load() -> bool:
    """
    是否使用本地路径加载（Super Node 优化）

    条件：NODE_MODE == standalone 且 IS_BRAIN_LOCAL
    """
    return (
        settings.NODE_MODE == "standalone"
        and settings.IS_BRAIN_LOCAL
    )


def _resolve_skills_repo_path() -> Path:
    """解析 SKILLS_REPO_PATH 为绝对路径"""
    p = Path(settings.SKILLS_REPO_PATH)
    if not p.is_absolute():
        # 相对路径：基于项目根或当前工作目录
        root = settings.JACHIN_PROJECT_ROOT
        if root:
            p = Path(root) / p
        else:
            p = p.resolve()
    return p


def _find_skill_dir_local(skill_id: str) -> Optional[Path]:
    """
    从本地 skills_repo 查找技能目录

    查找顺序：_bundled, drivers, apps, 根目录
    """
    repo = _resolve_skills_repo_path()
    if not repo.exists():
        logger.warning(f"Skills repo not found: {repo}")
        return None

    search_dirs = ["_bundled", "drivers", "apps", ""]
    for sub in search_dirs:
        candidate = repo / sub / skill_id if sub else repo / skill_id
        if candidate.exists() and candidate.is_dir():
            return candidate.resolve()

    return None


def _fetch_skill_remote(skill_id: str, dest: Path) -> bool:
    """
    从 L2 通过 HTTP 拉取技能资源（Distributed 模式）

    Returns:
        bool: 是否成功
    """
    import urllib.request
    import urllib.error

    base = settings.BRAIN_BASE_URL.rstrip("/")
    url = f"{base}/api/v3/skills/{skill_id}/assets"
    dest.mkdir(parents=True, exist_ok=True)

    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read()
        # 假设返回 zip，解压到 dest（实际实现需根据 L2 接口调整）
        import zipfile
        import io
        with zipfile.ZipFile(io.BytesIO(data), "r") as zf:
            zf.extractall(dest)
        logger.info(f"Fetched skill {skill_id} from L2 to {dest}")
        return True
    except Exception as e:
        logger.error(f"Failed to fetch skill {skill_id} from L2: {e}")
        return False


def load_skill_path(
    skill_id: str,
    dest_cache: Optional[Path] = None,
) -> Tuple[Optional[Path], bool]:
    """
    智能加载技能路径

    根据 NODE_MODE 和 IS_BRAIN_LOCAL 决定：
    - Standalone + Brain 本地：直接返回 skills_repo 中的路径，零拷贝
    - Distributed：从 L2 HTTP 拉取到 dest_cache，返回缓存路径

    Args:
        skill_id: 技能 ID，如 com.jachin.files
        dest_cache: Distributed 模式下的本地缓存目录，拉取后存放于此

    Returns:
        (path, from_local): path 为技能根目录，from_local 表示是否本地直读（未复制）
    """
    if _is_local_load():
        # Super Node：直接读取 L2 存储目录，跳过 HTTP
        local_path = _find_skill_dir_local(skill_id)
        if local_path:
            logger.debug(f"[Standalone] Using local path: {local_path}")
            return local_path, True
        logger.warning(f"[Standalone] Skill {skill_id} not found in {_resolve_skills_repo_path()}")

    # Distributed 或本地未找到：走 HTTP 拉取
    if dest_cache is None:
        import tempfile
        dest_cache = Path(tempfile.mkdtemp(prefix=f"skill_{skill_id}_"))

    if _fetch_skill_remote(skill_id, dest_cache):
        return dest_cache, False

    return None, False


class SkillLoader:
    """
    技能加载器 - 封装智能加载路径逻辑
    """

    def __init__(
        self,
        brain_base_url: Optional[str] = None,
        skills_repo_path: Optional[Path] = None,
    ):
        self.brain_base_url = brain_base_url or settings.BRAIN_BASE_URL
        self.skills_repo_path = Path(skills_repo_path or settings.SKILLS_REPO_PATH)

    @property
    def use_local_path(self) -> bool:
        """是否使用本地路径（Super Node 零拷贝）"""
        return _is_local_load()

    def get_skill_path(self, skill_id: str) -> Optional[Path]:
        """
        获取技能路径（仅 Standalone 模式返回，否则返回 None）

        Distributed 模式需调用 load_skill_path(skill_id, dest_cache) 拉取。
        """
        if not self.use_local_path:
            return None
        return _find_skill_dir_local(skill_id)

    def load(self, skill_id: str, dest_cache: Optional[Path] = None) -> Optional[Path]:
        """
        加载技能，返回可用的技能根目录路径
        """
        path, _ = load_skill_path(skill_id, dest_cache)
        return path
