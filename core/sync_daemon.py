"""
Jachin Nexus V2 - L1-L2 策略同步心跳 + 云边同步守护进程

- 心跳：私有化部署的 L2 定期向 L1 云端发起心跳，拉取订阅状态与全局安全策略。
- 云边同步：CloudSyncDaemon 定期拉取 manifest，下载 SKILL/MCP 包到本地仓库，热重载生效。

L2 集群化：多节点时仅 Leader（协调 Agent）执行心跳，基于 Redis 分布式锁选举。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
import shutil
import tempfile
import uuid
import zipfile
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_CONFIG_PATH = Path.home() / ".jachin" / "nexus_config.json"

# 503 提示仅打印一次，避免刷屏
_L1_503_HINT_LOGGED = False


def _log_l1_503_hint_once(api: str, err: Exception | None) -> None:
    """L1 返回 503 时，仅一次打印排查提示。"""
    global _L1_503_HINT_LOGGED
    if _L1_503_HINT_LOGGED or not err or "503" not in str(err):
        return
    _L1_503_HINT_LOGGED = True
    logger.warning(
        "[L1] 持续返回 503（%s）。请确保：1) 已运行 .\\scripts\\start-cloud.ps1 启动 L1 (Nexus)；"
        "2) L1 已完全启动（Next.js 编译完成）；3) 若使用 PostgreSQL，确保服务已启动。",
        api,
    )
_HEARTBEAT_PATH = "/api/v1/edge/heartbeat"
_DEFAULT_INTERVAL_SEC = 60
_LEADER_LOCK_KEY = "l2_cluster_leader_lock"
# 锁过期时间略大于心跳间隔，避免 Leader 失联后长时间阻塞
_LOCK_TTL_SEC = _DEFAULT_INTERVAL_SEC + 15

# 本进程唯一标识（短命 UUID，每次启动重新生成）
L2_PROCESS_ID = str(uuid.uuid4())[:8]


def _load_nexus_config() -> dict[str, Any]:
    """读取 nexus_config.json"""
    logger.info("[SyncDaemon] 即将读取 nexus_config path=%s", _CONFIG_PATH)
    if not _CONFIG_PATH.exists():
        logger.info("[SyncDaemon] nexus_config 不存在，跳过")
        return {}
    try:
        raw = _CONFIG_PATH.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        try:
            raw = _CONFIG_PATH.read_text(encoding="utf-16")
        except Exception:
            return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


# =============================================================================
# CloudSyncDaemon - L2 云边同步守护进程（神谕同步）
# =============================================================================

_MANIFEST_PATH = "/api/v1/sync/manifest"
_TELEMETRY_REPORT_PATH = "/api/v1/telemetry/report"
_TELEMETRY_UPLOAD_INTERVAL_SEC = 300  # 5 分钟
_INVENTORY_ROOT = Path.home() / ".jachin" / "inventory"
_SKILLS_DIR = _INVENTORY_ROOT / "skills"
_MCPS_DIR = _INVENTORY_ROOT / "mcps"
_L3_MCPS_DIR = _INVENTORY_ROOT / "l3_mcps"  # 路径 3：L3_LOCAL MCP，L3 拉取后动态加载


def _parse_version(v: str) -> tuple[int, ...]:
    """解析语义化版本为元组，便于比较。1.2.3 -> (1, 2, 3)，1.2.3-beta -> (1, 2, 3)"""
    if not v or not isinstance(v, str):
        return (0, 0, 0)
    parts = v.strip().split("-")[0].split(".")
    out = []
    for p in parts[:4]:  # 最多 major.minor.patch
        try:
            out.append(int(p.strip()))
        except ValueError:
            out.append(0)
    while len(out) < 3:
        out.append(0)
    return tuple(out[:3])


def _version_compare(v_remote: str, v_local: str) -> int:
    """
    比较版本：remote > local 返回 1，相等返回 0，remote < local 返回 -1。
    """
    r = _parse_version(v_remote or "0.0.0")
    l = _parse_version(v_local or "0.0.0")
    if r > l:
        return 1
    if r < l:
        return -1
    return 0


class CloudSyncDaemon:
    """
    L2 云边同步守护进程。
    定期拉取 L1 manifest，对比本地 ~/.jachin/inventory/，下载新增/更新包并热重载。
    """

    def __init__(self) -> None:
        cfg = _load_nexus_config()
        self._base_url = (cfg.get("nexus_base_url") or "").rstrip("/")
        self._access_token = cfg.get("access_token") or ""
        # tenant_id: 神谕接口鉴权，优先级 config > env > l1_user_id > instance_id
        self._tenant_id = (
            cfg.get("tenant_id")
            or os.environ.get("JACHIN_TENANT_ID")
            or cfg.get("l1_user_id")
            or cfg.get("instance_id")
            or ""
        )
        self._skills_dir = _SKILLS_DIR
        self._mcps_dir = _MCPS_DIR
        self._l3_mcps_dir = _L3_MCPS_DIR

    def _is_configured(self) -> bool:
        return bool(self._base_url and self._access_token and self._tenant_id)

    def _rewrite_package_url_for_local(self, package_url: str) -> str:
        """
        本地开发时，manifest 中的 package_url 可能为 https://nexus.jachin/...，
        L2 无法访问。若 nexus_base_url 为 localhost，则重写为本地可访问的 URL。
        """
        if not package_url or not self._base_url:
            return package_url
        # 仅当 base_url 为 localhost 时重写，避免影响生产环境
        base_lower = self._base_url.lower()
        if "localhost" not in base_lower and "127.0.0.1" not in base_lower:
            return package_url
        try:
            from urllib.parse import urlparse
            parsed = urlparse(package_url)
            host = (parsed.netloc or "").lower()
            if "nexus.jachin" in host:
                path = parsed.path or "/"
                if parsed.query:
                    path = f"{path}?{parsed.query}"
                return f"{self._base_url.rstrip('/')}{path}"
        except Exception:
            pass
        return package_url

    async def _fetch_manifest_with_tenant(self, tenant_id: str) -> list[dict[str, Any]]:
        """用指定 tenant_id 请求 manifest，失败返回 []。503 时重试（L1 可能正在启动/编译）。"""
        url = f"{self._base_url}{_MANIFEST_PATH}"
        logger.info("[SyncDaemon] 即将拉取 manifest url=%s tenant=%s", url, tenant_id[:24] if tenant_id else "")
        headers = {
            "Authorization": f"Bearer {self._access_token}",
            "X-Tenant-Id": tenant_id,
        }
        last_err = None
        for attempt in range(3):
            try:
                import httpx
                async with httpx.AsyncClient(timeout=30.0) as client:
                    resp = await client.get(url, headers=headers)
                    if resp.status_code == 503 and attempt < 2:
                        await asyncio.sleep(2 * (attempt + 1))
                        continue
                    resp.raise_for_status()
                    data = resp.json()
                    if not data.get("success"):
                        return []
                    return data.get("manifest") or []
            except Exception as e:
                last_err = e
                if "503" in str(e) and attempt < 2:
                    await asyncio.sleep(2 * (attempt + 1))
                    continue
                break
        _log_l1_503_hint_once("manifest", last_err)
        logger.warning("[SyncDaemon] manifest 拉取失败 tenant=%s err=%s", tenant_id[:24], last_err)
        return []

    async def poll_manifest(self) -> list[dict[str, Any]]:
        """
        请求 L1 manifest 接口获取最新清单。
        主 tenant 无数据时，尝试 demo-tenant-001（兼容旧版 Store 默认 cookie）。
        Returns:
             manifest 列表，失败返回 []
        """
        if not self._is_configured():
            logger.debug("[SyncDaemon] 未配置 L1 URL / tenant，跳过 manifest 拉取")
            return []

        manifest = await self._fetch_manifest_with_tenant(self._tenant_id)
        if manifest:
            logger.info("[SyncDaemon] manifest 拉取成功 tenant=%s count=%d", self._tenant_id[:24], len(manifest))
            return manifest
        logger.info("[SyncDaemon] 主 tenant=%s 无订阅", self._tenant_id[:24])
        # 兼容：旧版 Store 默认 nexus_tenant_id=demo-tenant-001，主 tenant 空时回退
        if self._tenant_id != "demo-tenant-001":
            logger.info("[SyncDaemon] 尝试 fallback tenant=demo-tenant-001")
            manifest = await self._fetch_manifest_with_tenant("demo-tenant-001")
            if manifest:
                logger.info("[SyncDaemon] fallback 成功 count=%d", len(manifest))
        return manifest

    def _diff_manifest_vs_local(self, manifest: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """
        对比 manifest 与本地 ~/.jachin/inventory/，找出新增或需更新的项。
        版本化策略：manifest.version > local .sync_meta.version 时需更新；package_url 变更也需更新。
        用户已永久卸载的技能（回收站彻底删除）将被跳过，不再从 L1 重新拉取。
        """
        logger.info("[SyncDaemon] 即将对比 manifest 与本地 inventory manifest_count=%d skills_dir=%s l3_mcps_dir=%s",
                    len(manifest), self._skills_dir, self._l3_mcps_dir)
        permanently_uninstalled: set[str] = set()
        try:
            from core.skill_registry import get_permanently_uninstalled_skills
            permanently_uninstalled = get_permanently_uninstalled_skills()
        except Exception:
            pass

        def _need_update(item: dict, local_version: str | None, package_url_changed: bool) -> bool:
            mv = item.get("version") or "1.0.0"
            if package_url_changed:
                return True
            if not local_version:
                return True
            return _version_compare(mv, local_version) > 0

        to_download: list[dict[str, Any]] = []
        for item in manifest:
            item_id = item.get("id")
            package_url = item.get("package_url")
            item_type = item.get("item_type", "SKILL")
            if not item_id or not package_url:
                continue
            if item_id in permanently_uninstalled:
                continue

            if item_type == "SKILL":
                local_dir = self._skills_dir / str(item_id)
                meta_file = local_dir / ".sync_meta"
                local_version = None
                url_changed = True
                if meta_file.exists():
                    logger.debug("[SyncDaemon] 即将读取 SKILL 本地版本 item_id=%s path=%s", item_id, meta_file)
                    try:
                        meta = json.loads(meta_file.read_text(encoding="utf-8"))
                        local_version = meta.get("version")
                        url_changed = meta.get("package_url") != package_url
                    except Exception:
                        pass
                if not local_dir.exists():
                    need_download = True
                else:
                    need_download = _need_update(item, local_version, url_changed)

                if need_download:
                    to_download.append(item)

            elif item_type == "MCP":
                runtime_tier = (item.get("runtime_tier") or "L2_GATEWAY").upper()
                if runtime_tier == "L3_LOCAL":
                    local_dir = self._l3_mcps_dir / str(item_id)
                    meta_file = local_dir / ".sync_meta"
                else:
                    local_path = self._mcps_dir / f"{item_id}.json"
                    local_dir = self._mcps_dir / str(item_id)
                    meta_file = self._mcps_dir / f".{item_id}.sync_meta"

                local_version = None
                url_changed = True
                if meta_file.exists():
                    logger.debug("[SyncDaemon] 即将读取 MCP 本地版本 item_id=%s path=%s", item_id, meta_file)
                    try:
                        meta = json.loads(meta_file.read_text(encoding="utf-8"))
                        local_version = meta.get("version")
                        url_changed = meta.get("package_url") != package_url
                    except Exception:
                        pass

                if runtime_tier == "L3_LOCAL":
                    need_download = not local_dir.exists() or _need_update(item, local_version, url_changed)
                else:
                    need_download = (not local_path.exists() and not local_dir.exists()) or _need_update(item, local_version, url_changed)

                if need_download:
                    to_download.append(item)

        return to_download

    def _compute_wasm_sha256(self, file_path: str) -> str:
        """计算文件 SHA-256 哈希，供 .sync_meta 存储及 L3 校验。"""
        h = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest().lower()

    def _verify_sha256(self, file_path: str, expected: str) -> bool:
        """校验文件 SHA-256，失败返回 False"""
        if not expected or not isinstance(expected, str):
            return True
        expected = expected.strip().lower()
        if len(expected) != 64 or not all(c in "0123456789abcdef" for c in expected):
            return True
        try:
            actual = self._compute_wasm_sha256(file_path)
            if actual != expected:
                logger.warning(
                    "[SyncDaemon] SHA-256 校验失败 expected=%s actual=%s",
                    expected[:16] + "...",
                    actual[:16] + "...",
                )
                return False
            return True
        except Exception as e:
            logger.error("[SyncDaemon] SHA-256 校验异常: %s", e, exc_info=False)
            return False

    async def download_and_extract(
        self,
        item_type: str,
        package_url: str,
        item_id: str,
        package_sha256: Optional[str] = None,
        *,
        bypass_proxy: bool = False,
        runtime_tier: Optional[str] = None,
        version: Optional[str] = None,
    ) -> bool:
        """
        流式下载并解压到本地仓库。
        若 L1 提供 package_sha256，下载完成后必须先校验，失败则删除重下（下周期重试）。
        绝对禁止解压损坏的包。
        SKILL: .zip → ~/.jachin/inventory/skills/{item_id}/
        MCP: 配置文件/二进制 → ~/.jachin/inventory/mcps/
        """
        import aiofiles
        import httpx

        loop = asyncio.get_running_loop()
        url_path = package_url.split("?")[0].lower()
        suffix = ".zip" if url_path.endswith(".zip") else ""
        temp_fd, temp_path = tempfile.mkstemp(suffix=suffix or ".bin")
        try:
            logger.info("[SyncDaemon] 即将下载 item_id=%s item_type=%s url=%s", item_id, item_type, package_url[:80] + "..." if len(package_url) > 80 else package_url)
            # 本地 URL 时禁用代理，避免 ConnectError（代理无法处理 localhost）
            async with httpx.AsyncClient(timeout=120.0, trust_env=not bypass_proxy) as client:
                async with client.stream("GET", package_url) as resp:
                    resp.raise_for_status()
                    async with aiofiles.open(temp_path, "wb") as f:
                        async for chunk in resp.aiter_bytes(chunk_size=65536):
                            await f.write(chunk)

            if package_sha256:
                ok = await loop.run_in_executor(
                    None, lambda: self._verify_sha256(temp_path, package_sha256)
                )
                if not ok:
                    logger.warning(
                        "[SyncDaemon] 包校验失败，已删除，下周期重试 item_id=%s",
                        item_id,
                    )
                    return False

            if item_type == "SKILL":
                dest = self._skills_dir / str(item_id)
                # 原子性更新：先删旧再解压，避免残留
                def _extract_skill() -> None:
                    if dest.exists():
                        logger.info("[SyncDaemon] 即将删除旧版本 SKILL item_id=%s dest=%s", item_id, dest)
                        shutil.rmtree(dest, ignore_errors=True)
                    dest.mkdir(parents=True, exist_ok=True)
                    with zipfile.ZipFile(temp_path, "r") as zf:
                        zf.extractall(dest)

                await loop.run_in_executor(None, _extract_skill)
                has_plugin = (dest / "plugin.json").exists()
                wasm_files = list(dest.glob("*.wasm"))
                has_wasm = bool(wasm_files)
                if not has_plugin or not has_wasm:
                    logger.warning(
                        "[SyncDaemon] SKILL %s 解压后缺少 plugin.json 或 .wasm，请检查包格式",
                        item_id,
                    )
                meta = {
                    "package_url": package_url,
                    "item_id": item_id,
                    "version": version or "1.0.0",
                }
                if wasm_files:
                    primary_wasm = wasm_files[0]
                    meta["wasm_sha256"] = self._compute_wasm_sha256(str(primary_wasm))
                meta_path = dest / ".sync_meta"
                meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
                # 075: 按 config/manifest.yaml 写出配置到 ~/.jachin/config/
                try:
                    from l3_node.config_writeout import write_config_from_package
                    write_config_from_package(dest, str(item_id))
                except Exception as cfg_err:
                    logger.warning("[SyncDaemon] 配置写出失败 item_id=%s: %s", item_id, cfg_err)

            elif item_type == "MCP":
                rt = (runtime_tier or "L2_GATEWAY").upper()
                # 路径 3：L3_LOCAL 解压到 l3_mcps/，含 Python 源码，L3 拉取后动态加载
                if rt == "L3_LOCAL":
                    self._l3_mcps_dir.mkdir(parents=True, exist_ok=True)
                    dest = self._l3_mcps_dir / str(item_id)
                    if temp_path.lower().endswith(".zip"):
                        def _extract_l3_mcp() -> None:
                            if dest.exists():
                                logger.info("[SyncDaemon] 即将删除旧版本 L3_LOCAL MCP item_id=%s dest=%s", item_id, dest)
                                shutil.rmtree(dest, ignore_errors=True)
                            dest.mkdir(parents=True, exist_ok=True)
                            with zipfile.ZipFile(temp_path, "r") as zf:
                                zf.extractall(dest)
                        await loop.run_in_executor(None, _extract_l3_mcp)
                    else:
                        # 非 zip 时复制到 dest 目录
                        if dest.exists():
                            logger.info("[SyncDaemon] 即将删除旧版本 L3_LOCAL MCP(非zip) item_id=%s dest=%s", item_id, dest)
                            shutil.rmtree(dest, ignore_errors=True)
                        dest.mkdir(parents=True, exist_ok=True)
                        shutil.copy(temp_path, dest / os.path.basename(temp_path))
                    meta_path = dest / ".sync_meta"
                    # 075: 按 config/manifest.yaml 写出配置到 ~/.jachin/config/
                    try:
                        from l3_node.config_writeout import write_config_from_package
                        write_config_from_package(dest, str(item_id))
                    except Exception as cfg_err:
                        logger.warning("[SyncDaemon] L3_LOCAL MCP 配置写出失败 item_id=%s: %s", item_id, cfg_err)
                else:
                    self._mcps_dir.mkdir(parents=True, exist_ok=True)
                    if temp_path.lower().endswith(".zip"):
                        dest = self._mcps_dir / str(item_id)
                        def _extract_mcp() -> None:
                            if dest.exists():
                                logger.info("[SyncDaemon] 即将删除旧版本 L2_GATEWAY MCP item_id=%s dest=%s", item_id, dest)
                                shutil.rmtree(dest, ignore_errors=True)
                            dest.mkdir(parents=True, exist_ok=True)
                            with zipfile.ZipFile(temp_path, "r") as zf:
                                zf.extractall(dest)
                        await loop.run_in_executor(None, _extract_mcp)
                        config_candidates = list(dest.glob("*.json")) + list(dest.glob("**/*.json"))
                        primary = None
                        for c in config_candidates:
                            if c.name in ("config.json", "mcp_config.json"):
                                primary = c
                                break
                        if not primary and config_candidates:
                            primary = config_candidates[0]
                        if primary:
                            shutil.copy(primary, self._mcps_dir / f"{item_id}.json")
                    else:
                        dest_file = self._mcps_dir / f"{item_id}.json"
                        shutil.copy(temp_path, dest_file)
                    # 075: L2_GATEWAY MCP 解压到 dest 时，按 config/manifest.yaml 写出配置
                    if temp_path.lower().endswith(".zip"):
                        try:
                            from l3_node.config_writeout import write_config_from_package
                            write_config_from_package(dest, str(item_id))
                        except Exception as cfg_err:
                            logger.warning("[SyncDaemon] L2_GATEWAY MCP 配置写出失败 item_id=%s: %s", item_id, cfg_err)
                    meta_path = self._mcps_dir / f".{item_id}.sync_meta"
                meta_content = {
                    "package_url": package_url,
                    "item_id": item_id,
                    "version": version or "1.0.0",
                }
                meta_path.write_text(
                    json.dumps(meta_content, ensure_ascii=False),
                    encoding="utf-8",
                )
            else:
                logger.warning("[SyncDaemon] 未知 item_type=%s，跳过 %s", item_type, item_id)
                return False

            return True
        except Exception as e:
            logger.error("[SyncDaemon] 下载/解压失败 item_id=%s: %s", item_id, e, exc_info=True)
            return False
        finally:
            try:
                os.close(temp_fd)
                os.unlink(temp_path)
            except OSError:
                pass

    async def _trigger_reload(self) -> None:
        """热重载：通过 InventoryReloader 队列触发，确保 MCP 创建/关闭同 task"""
        try:
            from core.inventory_reloader import request_reload
            future = request_reload()
            result = await future
            logger.info("[SyncDaemon] 热重载完成 mcps=%d skills=%d", result.get("mcps_injected", 0), result.get("skills_found", 0))
        except Exception as e:
            logger.error("[SyncDaemon] 热重载失败: %s", e, exc_info=True)

    async def upload_telemetry(self) -> None:
        """
        数据回传神经：将本地 usage_telemetry 未上报数据上传至 L1。
        仅当 L1 返回 success 后才标记为已上报；失败时数据保留在本地 SQLite，下周期重试。
        """
        if not self._is_configured():
            return

        try:
            from core.telemetry_batcher import get_unreported_logs, mark_reported

            compressed, ids = get_unreported_logs()
            if not ids:
                return

            url = f"{self._base_url}{_TELEMETRY_REPORT_PATH}"
            headers = {
                "Authorization": f"Bearer {self._access_token}",
                "X-Tenant-Id": self._tenant_id,
                "Content-Type": "application/json",
                "Content-Encoding": "gzip",
            }

            import httpx
            last_err = None
            for attempt in range(3):
                try:
                    async with httpx.AsyncClient(timeout=60.0) as client:
                        resp = await client.post(url, content=compressed, headers=headers)
                        if resp.status_code == 503 and attempt < 2:
                            await asyncio.sleep(2 * (attempt + 1))
                            continue
                        resp.raise_for_status()
                        data = resp.json()

                    if data.get("success"):
                        mark_reported(ids)
                        logger.info(
                            "[SyncDaemon] 遥测上报成功 count=%d inserted=%d",
                            len(ids),
                            data.get("inserted", 0),
                        )
                    else:
                        logger.warning(
                            "[SyncDaemon] L1 遥测接口返回 success=false，本地数据保留待重试: %s",
                            data.get("error", "unknown"),
                        )
                    last_err = None
                    break
                except Exception as e:
                    last_err = e
                    if "503" in str(e) and attempt < 2:
                        await asyncio.sleep(2 * (attempt + 1))
                        continue
            if last_err:
                _log_l1_503_hint_once("telemetry", last_err)
                logger.warning(
                    "[SyncDaemon] 遥测上报失败（下周期重试）: %s",
                    last_err,
                    exc_info=False,
                )
        except Exception as e:
            _log_l1_503_hint_once("telemetry", e)
            logger.warning(
                "[SyncDaemon] 遥测上报失败（下周期重试）: %s",
                e,
                exc_info=False,
            )

    async def run_sync_cycle(self) -> None:
        """执行一次完整同步周期：拉 manifest → diff → 下载 → 热重载 → 拉 policies"""
        manifest = await self.poll_manifest()
        logger.info("[SyncDaemon] manifest 拉取完成 count=%d", len(manifest) if manifest else 0)
        # 物资下载
        to_download = self._diff_manifest_vs_local(manifest) if manifest else []
        logger.info("[SyncDaemon] diff 完成 待下载=%d 项", len(to_download))
        if to_download:
            for item in to_download:
                item_id = item.get("id", "unknown")
                raw_url = item.get("package_url", "")
                package_url = self._rewrite_package_url_for_local(raw_url)
                bypass_proxy = package_url != raw_url  # 重写为 localhost 时禁用代理
                item_type = item.get("item_type", "SKILL")
                package_sha256 = item.get("package_sha256") or None
                logger.info(
                    "[SyncDaemon] 发现新战略物资: %s, 正在空投...",
                    item_id,
                )
                runtime_tier = item.get("runtime_tier") if item_type == "MCP" else None
                ok = await self.download_and_extract(
                    item_type, package_url, str(item_id), package_sha256,
                    bypass_proxy=bypass_proxy, runtime_tier=runtime_tier,
                    version=item.get("version"),
                )
                if ok:
                    logger.info("[SyncDaemon] 空投成功: %s ✓", item_id)
                else:
                    logger.warning("[SyncDaemon] 空投失败: %s ✗", item_id)
            await self._trigger_reload()
        else:
            logger.info("[SyncDaemon] 清单无变更，跳过下载")

        # L2 数据主权：RBAC 策略仅从本地 DB 读取，不再同步云端

    async def start_background_sync(self, interval_seconds: float = 60) -> asyncio.Task:
        """
        后台死循环轮询，永不崩溃。
        捕获网络断开、L1 宕机、下载超时等异常，仅打印 error 并在下个周期重试。
        遥测上报：每隔 5 分钟执行一次 upload_telemetry（数据回传神经）。
        """
        last_upload_at = 0.0

        async def _loop() -> None:
            nonlocal last_upload_at
            while True:
                try:
                    await self.run_sync_cycle()
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.error("[SyncDaemon] 同步周期异常（下轮重试）: %s", e, exc_info=True)

                # 遥测上报：每隔 5 分钟执行一次
                now = time.monotonic()
                if now - last_upload_at >= _TELEMETRY_UPLOAD_INTERVAL_SEC:
                    try:
                        await self.upload_telemetry()
                        last_upload_at = now
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:
                        logger.warning(
                            "[SyncDaemon] 遥测上报异常（下周期重试）: %s",
                            e,
                            exc_info=False,
                        )

                try:
                    await asyncio.sleep(interval_seconds)
                except asyncio.CancelledError:
                    raise

        return asyncio.create_task(_loop())


async def start_cloud_sync_background(interval_seconds: float = 60) -> asyncio.Task | None:
    """
    启动 CloudSyncDaemon 后台同步任务。
    返回 Task 供 lifespan 在 shutdown 时 cancel；未配置时返回 None。
    """
    daemon = CloudSyncDaemon()
    if not daemon._is_configured():
        logger.info("[SyncDaemon] 未配对 L1 或缺少 tenant_id，云边同步守护进程不启动")
        return None

    task = await daemon.start_background_sync(interval_seconds=interval_seconds)
    logger.info("[SyncDaemon] 云边同步守护进程已启动，间隔 %ds，tenant=%s base=%s", interval_seconds, daemon._tenant_id, daemon._base_url)
    # _loop 首次迭代会立即执行 run_sync_cycle，无需额外 create_task（避免并发触发多次 reload）
    return task


# =============================================================================
# L1 心跳（原有逻辑）
# =============================================================================


async def l1_heartbeat_sync() -> dict[str, Any] | None:
    """
    向 L1 平台发起心跳，拉取策略。
    读取 ~/.jachin/nexus_config.json 中的 instance_id、access_token、nexus_base_url。

    Returns:
        心跳响应体，失败返回 None
    """
    cfg = _load_nexus_config()
    instance_id = cfg.get("instance_id") or ""
    access_token = cfg.get("access_token") or ""
    base_url = (cfg.get("nexus_base_url") or "").rstrip("/")

    if not instance_id or not access_token or not base_url:
        logger.debug("[L1Heartbeat] 未配对或未配置 nexus_base_url，跳过心跳")
        return None

    url = f"{base_url}{_HEARTBEAT_PATH}"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    payload = {
        "instance_id": instance_id,
        "core_version": "0.8.35",
    }

    last_err = None
    for attempt in range(3):
        try:
            import httpx
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(url, json=payload, headers=headers)
                if resp.status_code == 503 and attempt < 2:
                    await asyncio.sleep(2 * (attempt + 1))
                    continue
                resp.raise_for_status()
                data = resp.json()
                return data
        except Exception as e:
            last_err = e
            if "503" in str(e) and attempt < 2:
                await asyncio.sleep(2 * (attempt + 1))
                continue
            break
    _log_l1_503_hint_once("heartbeat", last_err)
    logger.warning("[L1Heartbeat] 心跳失败: %s", last_err)
    return None


async def _heartbeat_loop(stop_event: asyncio.Event, interval_sec: float) -> None:
    """
    心跳循环：定期调用 l1_heartbeat_sync，并将策略写入 l1_policy。
    L2 集群化：仅成功获取 Redis 锁的节点作为 Leader（协调 Agent）执行心跳。
    """
    from core.l1_policy import apply_heartbeat_response

    first_run = True
    lock_ttl = int(interval_sec) + 15
    lock_value = f"{L2_PROCESS_ID}-{uuid.uuid4().hex[:12]}"

    while not stop_event.is_set():
        try:
            # 尝试获取 Leader 锁（Redis 不可用时退化：单节点模式，直接执行）
            loop = asyncio.get_running_loop()
            acquired = await loop.run_in_executor(
                None,
                lambda: _try_acquire_leader_lock(lock_value, lock_ttl),
            )
            if acquired:
                log_fn = logger.info if first_run else logger.debug
                log_fn(
                    "[L1Heartbeat] [Leader/Coordinating Agent] 本节点 %s 已获取锁，执行 L1 心跳",
                    L2_PROCESS_ID,
                )
                data = await l1_heartbeat_sync()
                if data:
                    apply_heartbeat_response(data)
                    if first_run:
                        logger.info("[L1Heartbeat] 首次策略同步完成")
                # 锁自动过期，无需显式释放（避免持有期间进程挂掉导致死锁）
            else:
                logger.debug(
                    "[L1Heartbeat] [Follower] 本节点 %s 未获取锁，跳过本次心跳（由其他 Leader 执行）",
                    L2_PROCESS_ID,
                )
        except Exception as e:
            logger.warning("[L1Heartbeat] 循环异常: %s", e)
        first_run = False
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_sec)
        except asyncio.TimeoutError:
            pass


def _try_acquire_leader_lock(value: str, ttl_sec: int) -> bool:
    """同步获取 Leader 锁。Redis 不可用或异常时返回 True（单节点退化）。"""
    try:
        from core.redis_manager import try_acquire_lock
        return try_acquire_lock(_LEADER_LOCK_KEY, value, ttl_sec)
    except Exception:
        return True  # 单节点退化


def start_l1_heartbeat_background() -> asyncio.Task | None:
    """
    启动 L1 心跳后台任务。
    返回 Task，供 lifespan 在 shutdown 时 cancel。
    L2 集群化：多节点时通过 Redis 锁选举 Leader，仅 Leader 执行心跳。
    """
    cfg = _load_nexus_config()
    if not cfg.get("instance_id") or not cfg.get("access_token") or not cfg.get("nexus_base_url"):
        logger.info("[L1Heartbeat] 未配对 L1，心跳守护进程不启动")
        return None

    interval = cfg.get("heartbeat_interval_sec") or _DEFAULT_INTERVAL_SEC
    stop_event = asyncio.Event()
    task = asyncio.create_task(_heartbeat_loop(stop_event, interval))
    task.add_done_callback(lambda t: stop_event.set() if not t.cancelled() else None)

    try:
        from core.redis_manager import get_redis_client
        if get_redis_client():
            logger.info(
                "[L1Heartbeat] 心跳守护进程已启动，间隔 %ds，L2 进程 ID=%s（集群模式：Leader 选举）",
                interval,
                L2_PROCESS_ID,
            )
        else:
            logger.info(
                "[L1Heartbeat] 心跳守护进程已启动，间隔 %ds，L2 进程 ID=%s（单节点模式）",
                interval,
                L2_PROCESS_ID,
            )
    except Exception:
        logger.info(
            "[L1Heartbeat] 心跳守护进程已启动，间隔 %ds，L2 进程 ID=%s",
            interval,
            L2_PROCESS_ID,
        )
    return task
