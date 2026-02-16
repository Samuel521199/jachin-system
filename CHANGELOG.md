# Changelog

All notable changes to this project will be documented in this file.

## [v0.2.0] - 2026-02-12

### Added

- **控制台 HUD API**：思维流日志、建议、记忆搜索、模型列表与切换
- **配置 API**：`/api/v3/config` 供 Horizon 显示环境与模型
- **技能权限字段**：manifest 中 `permissions` 支持 LiveTile 悬停展示
- **Dapr 部署适配**：`start.ps1` 支持 placement/scheduler 地址配置，适配本地/云/多级部署

### Changed

- ConsoleLayout：Void 节点数由记忆数驱动，Horizon 从后端 config 获取 environment/model
- DAPR_GUIDE：新增 Placement 与 Scheduler 地址配置文档

### Fixed

- Dapr scheduler 连接超时：显式指定 `localhost:6060` 避免 mDNS 返回容器内网 IP

---

## [v3.2] - 2026-02-03

详见 [docs/whitepaper_v3.2_final.md](docs/whitepaper_v3.2_final.md)
