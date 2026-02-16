# Layer 3 LifecycleManager 伪代码逻辑

**依据**: ARCHITECTURE_DESIGN_SPEC §3.1 Hybrid Lifecycle、§3.2 Intelligent Caching  
**位置**: `core/agent/runtime/`（或 `clients/lib/agent_runtime/`，因 L3 在 clients 侧）

---

## 1. 模块职责

| 组件 | 职责 |
|------|------|
| `LifecycleManager` | 根据 `deployment_strategy` 决定加载/执行/清理策略 |
| `CacheManager` | 本地 Hash 校验、LRU 清理、与 L2 拉取协调 |
| `ResidentDaemon` | resident 模式的后台守护、Keep-Alive、休眠唤醒 |

---

## 2. LifecycleManager 伪代码

```python
# core/agent/runtime/lifecycle_manager.py (伪代码)

from enum import Enum
from pathlib import Path
from typing import Optional, Dict, Any
from common.schemas.skill import DeploymentStrategy

class LifecycleManager:
    """
    Layer 3 技能生命周期管理器
    
    根据 deployment_strategy 调度：
    - ephemeral: RAM 加载 -> 执行 -> 立即销毁
    - cached: 检查本地 Hash -> 缺失则从 L2 拉取 -> 磁盘缓存 -> 执行 -> 进程随用随开
    - resident: 安装后常驻 -> Keep-Alive -> 休眠唤醒
    """
    
    def __init__(self, l2_base_url: str, local_cache_root: Path):
        self.l2_base_url = l2_base_url          # L2 serve_skill_assets 端点
        self.local_cache_root = local_cache_root  # 本地缓存根目录，如 ~/.jachin/cache/skills
        self._resident_processes: Dict[str, Process] = {}  # skill_id -> 常驻进程
    
    async def prepare_skill(self, skill_id: str, manifest: SkillManifest) -> PreparedSkill:
        """
        根据 deployment_strategy 准备技能执行环境
        Returns: PreparedSkill (含 logic_path, assets_path, strategy)
        """
        strategy = manifest.deployment_strategy
        
        if strategy == DeploymentStrategy.EPHEMERAL:
            # 即时模式：从 L2 拉取到临时目录，不持久化
            return await self._prepare_ephemeral(skill_id, manifest)
        
        elif strategy == DeploymentStrategy.CACHED:
            # 缓存模式：检查本地 Hash，决定拉取或使用缓存
            return await self._prepare_cached(skill_id, manifest)
        
        elif strategy == DeploymentStrategy.RESIDENT:
            # 常驻模式：检查是否已启动，未启动则启动守护进程
            return await self._prepare_resident(skill_id, manifest)
    
    async def _prepare_ephemeral(self, skill_id: str, manifest: SkillManifest) -> PreparedSkill:
        """即时模式：拉取到 tmp，执行后由 caller 负责清理"""
        # 1. 从 L2 拉取 logic + assets 到 tempdir
        temp_dir = Path(tempfile.mkdtemp(prefix=f"skill_{skill_id}_"))
        await self._fetch_from_l2(skill_id, manifest, temp_dir)
        # 2. 返回路径，caller 执行完毕后需调用 cleanup(temp_dir)
        return PreparedSkill(logic_path=temp_dir, assets_path=temp_dir, strategy="ephemeral", temp_dir=temp_dir)
    
    async def _prepare_cached(self, skill_id: str, manifest: SkillManifest) -> PreparedSkill:
        """缓存模式：Hash 校验 -> 缺失/过期则拉取 -> 返回路径"""
        cache_dir = self.local_cache_root / skill_id / manifest.version
        assets_manifest = await self._get_assets_manifest(skill_id)  # 从 L2 获取 logic_hash, assets_hash
        
        # 检查本地 Hash
        local_logic_hash = self._compute_dir_hash(cache_dir / "logic") if (cache_dir / "logic").exists() else None
        local_assets_hash = self._compute_dir_hash(cache_dir / "assets") if (cache_dir / "assets").exists() else None
        
        need_logic = local_logic_hash != assets_manifest.logic_hash
        need_assets = assets_manifest.assets_hash and local_assets_hash != assets_manifest.assets_hash
        
        if need_logic or need_assets:
            # 从 L2 拉取（增量：仅拉取变更部分）
            await self._fetch_from_l2(skill_id, manifest, cache_dir, logic_only=not need_assets)
        
        # LRU 清理：若缓存超限，淘汰最久未用
        await self._lru_evict_if_needed()
        
        return PreparedSkill(logic_path=cache_dir / "logic", assets_path=cache_dir / "assets", strategy="cached")
    
    async def _prepare_resident(self, skill_id: str, manifest: SkillManifest) -> PreparedSkill:
        """常驻模式：确保缓存存在，启动/复用守护进程"""
        # 1. 先走 cached 逻辑确保本地有完整包
        cached = await self._prepare_cached(skill_id, manifest)
        
        # 2. 检查 resident 进程是否已运行
        if skill_id not in self._resident_processes or not self._resident_processes[skill_id].is_alive():
            proc = await self._start_resident_daemon(skill_id, cached)
            self._resident_processes[skill_id] = proc
        
        return PreparedSkill(
            logic_path=cached.logic_path,
            assets_path=cached.assets_path,
            strategy="resident",
            daemon_process=self._resident_processes[skill_id],
        )
    
    async def execute_and_cleanup(self, prepared: PreparedSkill, capability: str, params: Dict) -> Any:
        """
        执行能力，并根据 strategy 做清理
        """
        result = await self._invoke_skill(prepared, capability, params)
        
        if prepared.strategy == DeploymentStrategy.EPHEMERAL:
            # 执行后立即清理
            shutil.rmtree(prepared.temp_dir, ignore_errors=True)
        
        # cached: 不清理，保留在磁盘
        # resident: 不清理，进程常驻
        return result
    
    async def _fetch_from_l2(self, skill_id: str, manifest: SkillManifest, dest: Path, logic_only: bool = False):
        """从 L2 serve_skill_assets 拉取资源"""
        # GET {l2_base_url}/api/v3/skills/{skill_id}/assets?logic_hash=xxx&assets_hash=xxx
        # 支持 Range 请求、增量下载
        ...
    
    async def _lru_evict_if_needed(self):
        """LRU 清理：缓存超限时淘汰最久未用"""
        # 遍历 local_cache_root 下各 skill/version，按 last_used_at 排序，删除超出配额部分
        ...
```

---

## 3. 缓存逻辑细化（_prepare_cached）

```
┌─────────────────────────────────────────────────────────────────┐
│  _prepare_cached(skill_id, manifest)                             │
├─────────────────────────────────────────────────────────────────┤
│  1. cache_dir = ~/.jachin/cache/skills/{skill_id}/{version}       │
│  2. assets_manifest = GET L2 /skills/{id}/assets/manifest        │
│     → logic_hash, assets_hash                                     │
│  3. local_logic_hash = hash_dir(cache_dir/logic)  # 若存在        │
│     local_assets_hash = hash_dir(cache_dir/assets)               │
│  4. if local_logic_hash != logic_hash:                           │
│        fetch logic from L2 → cache_dir/logic                      │
│  5. if assets_hash and local_assets_hash != assets_hash:          │
│        fetch assets from L2 → cache_dir/assets                    │
│  6. lru_evict_if_needed()  # 总缓存超限则淘汰                     │
│  7. return PreparedSkill(logic_path, assets_path)                │
└─────────────────────────────────────────────────────────────────┘
```

---

## 4. ephemeral 模式清理时机

```
用户请求 → prepare_skill(ephemeral) → 拉取到 tempdir
         → execute(capability, params)
         → execute_and_cleanup() 内: 执行完毕后 shutil.rmtree(tempdir)
```

---

## 5. resident 模式守护逻辑

```python
async def _start_resident_daemon(self, skill_id: str, prepared: PreparedSkill) -> Process:
    """启动 resident 守护进程"""
    # 子进程/子解释器 运行 skill 的 main loop
    # 支持: 心跳、休眠唤醒、优雅退出
    proc = Process(target=_resident_worker_main, args=(skill_id, prepared.logic_path))
    proc.start()
    return proc
```

---

## 6. 与 L2 接口约定（供 SkillDownloader / serve_skill_assets 对齐）

| 端点 | 方法 | 说明 |
|------|------|------|
| `GET /api/v3/skills/{id}/assets/manifest` | - | 返回 `{logic_hash, assets_hash, logic_paths, assets_paths}` |
| `GET /api/v3/skills/{id}/assets?part=logic` | - | 拉取 logic 包（支持 Range） |
| `GET /api/v3/skills/{id}/assets?part=assets` | - | 拉取 assets 包（支持 Range） |

---

## 7. 待确认事项

1. **L3 位置**：`core/agent/runtime/` 还是 `clients/lib/agent_runtime/`？core 为 L2 主体，L3 逻辑通常在 clients。
2. **Hash 算法**：SHA256 是否足够？是否需支持分块 Hash（大文件）？
3. **LRU 配额**：默认缓存上限（如 2GB）及配置方式？
4. **resident 进程模型**：子进程 vs 子解释器 vs 独立服务？
