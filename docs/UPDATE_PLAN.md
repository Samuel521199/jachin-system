# Jachin 项目更新计划

**版本**: V1  
**更新日期**: 2026-03  
**聚焦**: 架构、Skill、MCP

---

## 1.1 控制面与数据面分离（9项）

1. [ ] `cloud/nexus` heartbeat API — HTTP 轮询 → WebSocket 长连，废除 5–10 秒轮询
2. [ ] `cloud/nexus` 指令下发 — Layer 1 鉴权后经 WS 推送至 Layer 2，延迟降至单次 RTT
3. [ ] `core/daemon.py` — Layer 2 mDNS 广播，集成 zeroconf/avahi，注册 `_jachin-nexus._tcp.local`
4. [ ] `clients/desktop` — Layer 3 mDNS 嗅探，启动时优先扫描局域网内 L2 实例
5. [ ] `clients/desktop` — 内网 IP 直连，发现后通过 `ws://192.168.x.x:8080` 建立 WebSocket
6. [ ] `cloud/nexus` — Layer 1 降级为信令服务器，仅交换 SDP/ICE
7. [ ] `core/` + `clients/` — WebRTC Data Channel 或 Libp2p 选型，NAT 打洞建立 P2P 隧道
8. [ ] 数据面直连 — 流式 Chunk、图像、语音绕过 Layer 1
9. [ ] TURN Fallback — 对称 NAT 下中继，打洞失败时退化至 TURN 服务器

---

## 1.2 生物钟与主动感知（3项）

1. [ ] `core/cron_thinker.py` — 30 分钟异步线程，脱离云端本地主动环顾
2. [ ] `core/cron_thinker.py` — 扫描系统日志、未读邮件，发现异常时推送报警
3. [ ] `core/cron_thinker.py` — 与 Jachin Mesh 并行，互为补充

---

## 1.3 Jachin Mesh 与通信（3项）

1. [ ] `cloud/nexus` + `core/daemon.py` — 废弃 10 秒 HTTP 轮询，全面切换为 WebSocket 双向长连
2. [ ] `cloud/nexus` — 毫秒级指令下发，Layer 1 → Layer 2 事件驱动
3. [ ] `core/event_bus.py` OmniSensoryBus — SQLite 队列挂载，进程重启不丢事件

---

## 1.4 Edge Mesh 与算力协同（3项）

1. [ ] `core/swarm_registry.py` — 同网设备算力协同，多 L3 设备形成算力集群
2. [ ] `core/swarm_registry.py` — 任务认领机制，L2 广播「谁空闲？」，空闲设备认领
3. [ ] `core/swarm_hook.py` — 重载任务外包，video_encode、ffmpeg 等 heavy_tools 外包至虫群

---

## 1.5 审计与合规（2项）

1. [ ] `core/` 舰队级审计 — 操作审计日志，谁在何时对哪些节点执行何操作
2. [ ] `core/policy_enforcer.py` — 设备级审计完善，与渠道级 allowlist 配合

---

## 2.1 Skill 注册表与配置（4项）

1. [ ] `core/api/routes/v2_skills.py` — GET/PUT config 需 PolicyEnforcer 校验技能访问权限
2. [ ] `core/skill_registry.py` — 安装时 configs/volumes 初始化，与 inventory 安装流程打通
3. [ ] `core/skill_registry.py` — volume_bindings 引用计数 GC，卸载时 purge_data 清理物理目录
4. [ ] `clients/desktop/SkillSettingsDrawer.tsx` — 与 L2 同步，桌面端配置持久化到 skill_registry

---

## 2.2 Skill 清单与下载（3项）

1. [ ] `core/api/routes/v2_inventory.py` — 无身份时鉴权策略，后续版本开启鉴权过滤
2. [ ] `cloud/nexus` store API — PRIVATE→PUBLIC 转换接口，显式「转换可见性」或重新 full publish
3. [ ] `clients/desktop/UninstallSkillModal.tsx` — 与 L2 打通，调用 skill_registry.uninstall_skill_with_gc

---

## 2.3 三轨道技能体系（4项）

1. [ ] `core/runtime/skill_loader.py` — Semantic Vector Router，v6.0 语义向量路由接管技能匹配
2. [ ] `skills_repo/` — SKILL.md 热加载完善，保存文件瞬间生效
3. [ ] `core/wasm_runner.py` — The Abyss 沙箱完善，燃料熔断、JSON Schema 验证
4. [ ] `core/` core:forge_compiler — Forge 边缘编译器，本地编译 Python→Wasm

---

## 2.4 技能生态与商城（2项）

1. [ ] `cloud/nexus` — JPP 调用次数统计，Layer 1 向开发者地址分润
2. [ ] `cloud/nexus` store — 定价上架、30% 抽成（商业逻辑，当前排除）

---

## 3.1 L3→L2 MCP 调用链（3项）

1. [ ] `l3_node/skills/mcp_registry.py` — invoke_via_l2 携带 X-Sub-Account-Id，修复 401
2. [ ] `l3_node/skills/mcp_registry.py` — 从 l2_gateway_config 或 session 获取 sub_account_id
3. [ ] `core/api/routes/v2_mcp.py` — GET /api/v2/mcp/tools 鉴权，需 X-Sub-Account-Id

---

## 3.2 MCP 安全与策略（3项）

1. [ ] `core/policy_enforcer.py` — 全量放行 → 按 role_permissions 过滤
2. [ ] `~/.jachin/mcp_servers.json` — mcp_tool_allowlist 配置，限制 Agent 可调用的 MCP 工具
3. [ ] `core/mcp_client.py` — mcp_enabled 开关，C 端默认关闭，企业部署可开启

---

## 3.3 MCP 生态与配置（3项）

1. [ ] `core/inventory_scanner.py` — scan_local_mcps 与 inventory 打通，侧载 MCP 配置热重载
2. [ ] `~/.jachin/inventory/mcps/` — 与 mcp_servers.json 统一，配置来源一致性
3. [ ] `cloud/nexus` catalog — L1 catalog 支持 MCP 商品，与 Skill 双轨展示

---

## 4.1 L2 控制面（4项）

1. [ ] `core/policy_enforcer.py` — RBAC 权限校验，替换 TODO(MVP) 全量放行
2. [ ] `core/policy_enforcer.py` — 断网降级逻辑完善，从 role_permissions 读取
3. [ ] `core/api/routes/v2_local_admin.py` — 角色权限管理 UI，与 L2 Admin 打通
4. [ ] `core/api/routes/v2_auth.py` — API Key 密文下发流程审计，确保零信任流转

---

## 4.2 L3 执行面（4项）

1. [ ] `clients/desktop/commands/skill_sync.rs` — 与 v2_skills/config 同步，技能配置变更时 L3 感知
2. [ ] `core/brain/planner/task_planner.py` — 设备控制任务规划
3. [ ] `clients/lib/jachin_link_client/client.py` — gRPC 客户端连接 Jachin Link Gateway
4. [ ] `core/` + `l3_node/` — 全链路 runId 追踪，贯穿 SensoryInputEvent → PipelineContext → SensoryOutputEvent

---

## 5.1 其他待办（8项）

1. [ ] `clients/desktop/src-tauri/src/tts/cloud_adapter.rs` — TTS WebSocket 流式合成，长文本流式（可选）
2. [ ] `core/voice/` — 百度/腾讯 STT/TTS 多引擎支持
3. [ ] `core/system/permission.py` — 权限检查逻辑，实现权限验证
4. [ ] `core/system/plugin_manager.py` — 签名验证、License 验证，生产环境校验
5. [ ] `core/system/updater.py` — 更新检查/下载/应用，桌面端自动更新
6. [ ] `core/` Aegis — OpenTelemetry 遥测 + Prompt 注入拦截墙
7. [ ] `cloud/nexus` webhooks — Discord/Slack/WhatsApp 渠道扩展
8. [ ] `core/transport/gateway.py` — 转发到 Jachin Brain、验证配对 token

---

## 6.1 技能配置与规范（5项）

1. [x] `config/skills_config.yaml` — repo_path、runtime、wasmtime、marketplace
2. [x] `docs/JMP_SPEC.md` — JMP 协议
3. [x] `docs/PLUGIN_SECURITY_SANDBOX.md` — 插件安全沙箱
4. [x] `docs/REVENUE_AND_ROYALTY_SPEC.md` — 分润规范
5. [x] `common/schemas/` — manifest、skill、jmp、auth、license、sdui

---

## 7.1 优先级汇总

| 优先级 | 聚焦 | 代表任务 |
|--------|------|----------|
| **P0** | 阻塞演示与核心流程 | L3 MCP X-Sub-Account-Id、Jachin Mesh 长连、PolicyEnforcer RBAC、v2_inventory 鉴权 |
| **P1** | 体验与合规 | mDNS 局域网、cron_thinker、舰队审计、Semantic Router、MCP 白名单 |
| **P2** | 升维与生态 | WebRTC P2P、Edge Mesh Swarm、Wasm 沙箱完善、JPP 版税 |
| **P3** | 兜底与扩展 | TURN Fallback、IM 渠道扩展 |

---

## 8.1 参考文档

- [ARCHITECTURE.md](./ARCHITECTURE.md) — 架构规范
- [CLOUD_EDGE_AI_OS_IMPLEMENTATION_ANALYSIS.md](./CLOUD_EDGE_AI_OS_IMPLEMENTATION_ANALYSIS.md) — 实现度分析
- [whitepaper/10_CONTROL_DATA_PLANE.md](./whitepaper/10_CONTROL_DATA_PLANE.md) — 控制面与数据面分离
- [MCP_SPEC.md](./MCP_SPEC.md) — MCP 接入规范
- [whitepaper/08_JPP_SDK_AND_SKILLS.md](./whitepaper/08_JPP_SDK_AND_SKILLS.md) — 三轨道技能体系
