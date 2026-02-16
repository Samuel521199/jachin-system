# Jachin-System v4.0 目录结构

```
jachin-system/
├── common/                          # 协议与 schema 桥接层
│   ├── protocols/
│   │   ├── jachin_link.proto
│   │   └── swarm_discovery.proto
│   ├── schemas/
│   │   ├── auth.py                  # TrustZone, UserRole, UserContext
│   │   ├── manifest.py
│   │   ├── sentinel.py
│   │   └── ...
│   └── crypto/
│
├── core/                            # Tier 2 核心
│   ├── api/                         # FastAPI 路由
│   │   ├── chat.py, chat_v2.py, voice.py
│   │   ├── cluster.py, console.py, skills.py
│   │   ├── orchestrator.py, monitoring.py
│   │   └── routes/
│   │
│   ├── brain/                       # 智能层
│   │   ├── agent_orchestrator.py
│   │   ├── llm/                     # LLM 适配器
│   │   │   ├── base.py, factory.py
│   │   │   ├── qwen_adapter*.py, local_adapter.py
│   │   │   └── personality.py, regions.py
│   │   ├── llm_engine/               # 模型路由 (v4.0)
│   │   │   ├── __init__.py           # route_and_get_llm, get_model_router
│   │   │   └── router.py             # ModelRouter, ModelType
│   │   ├── planner/
│   │   │   ├── intent_parser.py, intent_planner.py
│   │   │   ├── task_planner.py, resource_allocator.py
│   │   │   └── __init__.py
│   │   ├── ray_actors/               # 兼容层 (仅 __init__.py)
│   │   │   └── __init__.py           # 从 core.skills 再导出
│   │   └── ray_cluster/
│   │       ├── cluster_manager.py, task_scheduler.py
│   │       ├── tasks.py, task_types.py
│   │       ├── resource_monitor.py, decorators.py
│   │       └── __init__.py
│   │
│   ├── skills/                      # 技能 Actor (v4.0 迁移)
│   │   ├── __init__.py
│   │   ├── base_skill.py             # BaseSkillActor, AccessDenied
│   │   └── sentinel.py               # SentinelActor
│   │
│   ├── swarm/                       # 蜂群调度 (v4.0)
│   │   ├── node_registry.py          # NodeRegistry, Node, NodeInfo
│   │   ├── scheduler.py
│   │   └── health_monitor.py
│   │
│   ├── security/                    # 安全层 (v4.0)
│   │   ├── trust_zone.py             # SecurityContext, TrustZoneManager
│   │   └── acl_manager.py
│   │
│   ├── system/
│   │   ├── plugin_manager.py         # PluginManager, get_plugin_manager
│   │   ├── plugin_executor.py
│   │   ├── permission.py, permission_enforcer.py
│   │   ├── kernel.py, telemetry.py
│   │   └── runtime_permission_interceptor.py
│   │
│   ├── runtime/
│   │   ├── skill_loader.py            # SkillLoader
│   │   ├── skill_runner.py           # SkillRunner (依赖 ray)
│   │   ├── manifest.py               # ManifestParser, SkillManifest
│   │   ├── interfaces.py, schemas.py
│   │   └── sandbox/
│   │       ├── base.py, docker_sandbox.py
│   │       └── __init__.py
│   │
│   ├── memory/
│   │   ├── vector_store.py
│   │   └── schema/
│   │       ├── database.py, models.py
│   │       └── migrations/
│   │
│   ├── transport/
│   │   ├── gateway.py
│   │   ├── mtls_manager.py, connection_manager.py
│   │   └── __init__.py
│   │
│   ├── registry/
│   │   ├── registry.py               # DeviceRegistry
│   │   ├── protocol.py, dapr.py
│   │   └── __init__.py
│   │
│   ├── config/                      # settings
│   ├── dapr/                        # StateStore, PubSub
│   ├── monitoring/                  # PerformanceMonitor
│   └── voice/                       # SpeechToText, TextToSpeech
│
├── skills_repo/
│   ├── _bundled/                    # 系统预装
│   │   ├── com.jachin.files/
│   │   ├── com.jachin.os-mate/
│   │   ├── com.jachin.calendar/
│   │   └── com.jachin.voip/
│   ├── drivers/                     # 硬件驱动 (v4.0)
│   │   └── com.jachin.sys-monitor/
│   └── apps/                        # 纯软件应用 (v4.0)
│       └── com.jachin.web-surfer/
│
├── clients/
│   ├── desktop/
│   └── lib/
│       ├── jachin_link_client/
│       └── edge_brain/               # Edge Reflex (v4.0)
│
├── scripts/
│   └── test_skill_loading.py
│
└── docs/
    ├── whitepaper_v4.0_swarm.md
    └── DIRECTORY_STRUCTURE_V4.md
```

---

## Python Import 路径验证

### 关键导入链 (需项目根在 sys.path)

| 导入 | 路径 | 依赖 |
|------|------|------|
| `from core.skills.base_skill import BaseSkillActor` | core/skills/base_skill.py | ray |
| `from core.skills.sentinel import SentinelActor` | core/skills/sentinel.py | ray |
| `from core.brain.ray_actors import BaseSkillActor` | core/brain/ray_actors/__init__.py → core.skills | ray |
| `from core.brain.llm_engine import ModelRouter, route_and_get_llm` | core/brain/llm_engine/__init__.py | - |
| `from core.system.plugin_manager import get_plugin_manager` | core/system/plugin_manager.py | ray |
| `from core.swarm.node_registry import NodeRegistry, Node` | core/swarm/node_registry.py | - |
| `from core.security.trust_zone import SecurityContext` | core/security/trust_zone.py | - |
| `from core.runtime.skill_loader import SkillLoader` | core/runtime/skill_loader.py | - |
| `from core.runtime import SkillLoader` | core/runtime/__init__.py | ray (via SkillRunner) |
| `from common.schemas.auth import TrustZone` | common/schemas/auth.py | - |

### 注意事项

- **core.runtime**：`SkillRunner` 依赖 `ray`，已改为懒加载。`from core.runtime.skill_loader import SkillLoader` 或 `from core.runtime.manifest import ManifestParser` 在无 ray 环境下可正常导入；仅当显式导入 `SkillRunner` 时才加载 ray。

- **技能 manifest 路径**：PluginManager 按顺序查找 `_bundled`、`drivers`、`apps`、根目录。
