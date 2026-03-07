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
_HEARTBEAT_PATH = "/api/v1/edge/heartbeat"
_DEFAULT_INTERVAL_SEC = 60
_LEADER_LOCK_KEY = "l2_cluster_leader_lock"
# 锁过期时间略大于心跳间隔，避免 Leader 失联后长时间阻塞
_LOCK_TTL_SEC = _DEFAULT_INTERVAL_SEC + 15

# 本进程唯一标识（短命 UUID，每次启动重新生成）
L2_PROCESS_ID = str(uuid.uuid4())[:8]


def _load_nexus_config() -> dict[str, Any]:
    """读取 nexus_config.json"""
    if not _CONFIG_PATH.exists():
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
        """用指定 tenant_id 请求 manifest，失败返回 []"""
        url = f"{self._base_url}{_MANIFEST_PATH}"
        headers = {
            "Authorization": f"Bearer {self._access_token}",
            "X-Tenant-Id": tenant_id,
        }
        try:
            import httpx
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(url, headers=headers)
                resp.raise_for_status()
                data = resp.json()
                if not data.get("success"):
                    return []
                return data.get("manifest") or []
        except Exception as e:
            logger.warning("[SyncDaemon] manifest 拉取失败 tenant=%s err=%s", tenant_id[:24], e)
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
        简化策略：若本地无对应 item_id 目录，或 package_url 变更（通过 .sync_meta 记录），则需下载。
        """
        to_download: list[dict[str, Any]] = []
        for item in manifest:
            item_id = item.get("id")
            package_url = item.get("package_url")
            item_type = item.get("item_type", "SKILL")
            if not item_id or not package_url:
                continue

            if item_type == "SKILL":
                local_dir = self._skills_dir / str(item_id)
                meta_file = local_dir / ".sync_meta"
                need_download = False
                if not local_dir.exists():
                    need_download = True
                elif meta_file.exists():
                    try:
                        meta = json.loads(meta_file.read_text(encoding="utf-8"))
                        if meta.get("package_url") != package_url:
                            need_download = True
                    except Exception:
                        need_download = True
                else:
                    # 有目录但无 meta，视为需更新
                    need_download = True

                if need_download:
                    to_download.append(item)

            elif item_type == "MCP":
                # MCP 通常按 item_id 存为 mcps/{item_id}.json 或 mcps/{item_id}/config.json
                local_path = self._mcps_dir / f"{item_id}.json"
                local_dir = self._mcps_dir / str(item_id)
                meta_file = (local_dir / ".sync_meta") if local_dir.exists() else (self._mcps_dir / f".{item_id}.sync_meta")
                need_download = False
                if not local_path.exists() and not local_dir.exists():
                    need_download = True
                elif meta_file.exists():
                    try:
                        meta = json.loads(meta_file.read_text(encoding="utf-8"))
                        if meta.get("package_url") != package_url:
                            need_download = True
                    except Exception:
                        need_download = True
                else:
                    need_download = True

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
                dest.mkdir(parents=True, exist_ok=True)
                # zipfile 为同步 I/O，放入 executor 避免阻塞 Event Loop
                def _extract() -> None:
                    with zipfile.ZipFile(temp_path, "r") as zf:
                        zf.extractall(dest)

                await loop.run_in_executor(None, _extract)
                has_plugin = (dest / "plugin.json").exists()
                wasm_files = list(dest.glob("*.wasm"))
                has_wasm = bool(wasm_files)
                if not has_plugin or not has_wasm:
                    logger.warning(
                        "[SyncDaemon] SKILL %s 解压后缺少 plugin.json 或 .wasm，请检查包格式",
                        item_id,
                    )
                meta = {"package_url": package_url, "item_id": item_id}
                if wasm_files:
                    primary_wasm = wasm_files[0]
                    meta["wasm_sha256"] = self._compute_wasm_sha256(str(primary_wasm))
                meta_path = dest / ".sync_meta"
                meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")

            elif item_type == "MCP":
                self._mcps_dir.mkdir(parents=True, exist_ok=True)
                # MCP 可能是 .json 配置或压缩包，按后缀处理
                if temp_path.lower().endswith(".zip"):
                    dest = self._mcps_dir / str(item_id)
                    dest.mkdir(parents=True, exist_ok=True)

                    def _extract_mcp() -> None:
                        with zipfile.ZipFile(temp_path, "r") as zf:
                            zf.extractall(dest)

                    await loop.run_in_executor(None, _extract_mcp)
                    # 扫描器只读 mcps/*.json，需将主配置复制到 mcps/{item_id}.json
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
                meta_path = self._mcps_dir / f".{item_id}.sync_meta"
                meta_path.write_text(
                    json.dumps({"package_url": package_url, "item_id": item_id}, ensure_ascii=False),
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
        """热重载：调用 InventoryScanner.reload_inventory() 让新技能瞬间生效"""
        try:
            from core.inventory_scanner import reload_inventory
            result = await reload_inventory()
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
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(url, content=compressed, headers=headers)
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
        except Exception as e:
            logger.warning(
                "[SyncDaemon] 遥测上报失败（下周期重试）: %s",
                e,
                exc_info=False,
            )

    async def run_sync_cycle(self) -> None:
        """执行一次完整同步周期：拉 manifest → diff → 下载 → 热重载 → 拉 policies"""
        manifest = await self.poll_manifest()
        # 物资下载
        to_download = self._diff_manifest_vs_local(manifest) if manifest else []
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
                ok = await self.download_and_extract(
                    item_type, package_url, str(item_id), package_sha256, bypass_proxy=bypass_proxy
                )
                if ok:
                    logger.info("[SyncDaemon] 空投成功: %s ✓", item_id)
                else:
                    logger.warning("[SyncDaemon] 空投失败: %s ✗", item_id)
            await self._trigger_reload()
        else:
            logger.debug("[SyncDaemon] 清单无变更，跳过下载")

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


def start_cloud_sync_background(interval_seconds: float = 60) -> asyncio.Task | None:
    """
    启动 CloudSyncDaemon 后台同步任务。
    返回 Task 供 lifespan 在 shutdown 时 cancel；未配置时返回 None。
    """
    daemon = CloudSyncDaemon()
    if not daemon._is_configured():
        logger.info("[SyncDaemon] 未配对 L1 或缺少 tenant_id，云边同步守护进程不启动")
        return None

    task = daemon.start_background_sync(interval_seconds=interval_seconds)
    logger.info("[SyncDaemon] 云边同步守护进程已启动，间隔 %ds，tenant=%s base=%s", interval_seconds, daemon._tenant_id, daemon._base_url)
    # 启动后立即执行一次同步，不等待首个 interval
    try:
        import asyncio
        asyncio.create_task(daemon.run_sync_cycle())
    except Exception as e:
        logger.warning("[SyncDaemon] 首次同步触发失败: %s", e)
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
        "core_version": "0.8.5",
    }

    try:
        import httpx
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            return data
    except Exception as e:
        logger.warning("[L1Heartbeat] 心跳失败: %s", e)
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
