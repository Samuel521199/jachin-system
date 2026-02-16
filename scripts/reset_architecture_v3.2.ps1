# 1. 清理旧规则
Remove-Item -Path ".\.cursor\rules\*.mdc" -Force -ErrorAction SilentlyContinue
if (!(Test-Path ".\docs")) { New-Item -ItemType Directory -Path ".\docs" }

# 2. 写入 v3.2 白皮书
$whitepaper = @"
# Jachin-System v3.2 Whitepaper & Architecture

## 1. Hierarchy
- **Tier 1 (Cloud):** Marketplace & Auth.
- **Tier 2 (Hive):** Distributed Core. Supports **Single Mode** (Laptop) & **Cluster Mode** (Server Farm).
- **Tier 3 (Terminal):** Lightweight Clients (Desktop/Mobile/IoT).

## 2. Key Tech
- **Ray:** For distributed AI task scheduling across the Hive.
- **Dapr:** For service mesh and JCP (Device Protocol).
- **Tauri:** For the Desktop Sprite client.

## 3. Data Strategy
- **Federated Memory:** Global Knowledge (Shared) + Private Memory (Isolated).
- **RBAC:** Admin controls which Agent/Skill users can access.
"@
Set-Content -Path ".\docs\architecture.md" -Value $whitepaper -Encoding UTF8

# 3. 写入 Cursor 规则 000: 目录与分层
$rule_000 = @"
---
description: v3.2 三层架构目录规范
globs: *
---
# Tier Structure Rules

1.  **Tier 1 (cloud/):** SaaS code only.
2.  **Tier 2 (core/):** The Brain.
    - `core/brain/ray_cluster/`: Manages Ray Head/Worker nodes.
    - `core/runtime/`: Loads skills from `skills_repo/`.
    - `core/web_ui/`: Local admin dashboard.
3.  **Tier 3 (clients/):** Dumb terminals. Send commands to Tier 2.
"@
Set-Content -Path ".\.cursor\rules\000-structure.mdc" -Value $rule_000 -Encoding UTF8

# 4. 写入 Cursor 规则 050: 分布式计算
$rule_050 = @"
---
description: Tier 2 Ray 集群与 Dapr 通信规范
globs: core/brain/**
---
# Distributed Compute Rules

## Ray Integration
- **Master Node:** Runs `ray start --head`. `core/main.py` connects via `ray.init(address='auto')`.
- **Worker Node:** Runs `ray start --address={MASTER_IP}`.
- **Inference:** All LLM calls must be wrapped in `@ray.remote`.

## Dapr Networking
- Services communicate via Dapr Sidecar.
- Device Discovery (JCP) uses Dapr Pub/Sub `system/announce` topic.
"@
Set-Content -Path ".\.cursor\rules\050-distributed.mdc" -Value $rule_050 -Encoding UTF8

Write-Host "✅ Jachin-System v3.2 (终极形态) 架构定义已刷新！" -ForegroundColor Green
