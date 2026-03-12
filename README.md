```
 ██╗ █████╗  ██████╗██╗  ██╗██╗███╗   ██╗    ███╗   ██╗███████╗██╗  ██╗██╗   ██╗███████╗
 ██║██╔══██╗██╔════╝██║  ██║██║████╗  ██║    ████╗  ██║██╔════╝╚██╗██╔╝██║   ██║██╔════╝
 ██║███████║██║     ███████║██║██╔██╗ ██║    ██╔██╗ ██║█████╗   ╚███╔╝ ██║   ██║███████╗
 ██║██╔══██║██║     ██╔══██║██║██║╚██╗██║    ██║╚██╗██║██╔══╝   ██╔██╗ ██║   ██║╚════██║
 ██║██║  ██║╚██████╗██║  ██║██║██║ ╚████║    ██║ ╚████║███████╗██╔╝ ██╗╚██████╔╝███████║
 ╚═╝╚═╝  ╚═╝ ╚═════╝╚═╝  ╚═╝╚═╝╚═╝  ╚═══╝    ╚═╝  ╚═══╝╚══════╝╚═╝  ╚═╝ ╚═════╝ ╚══════╝
```

<p align="center">
  <img src="https://img.shields.io/badge/license-MIT-00ff88?style=flat-square" alt="License MIT" />
  <img src="https://img.shields.io/badge/TypeScript-007ACC?style=flat-square&logo=typescript&logoColor=white" alt="TypeScript" />
  <img src="https://img.shields.io/badge/Rust-000000?style=flat-square&logo=rust&logoColor=white" alt="Rust" />
  <img src="https://img.shields.io/badge/Tauri-FFC131?style=flat-square&logo=tauri&logoColor=black" alt="Tauri" />
  <img src="https://img.shields.io/badge/Drizzle%20ORM-2F4F4F?style=flat-square" alt="Drizzle ORM" />
  <img src="https://img.shields.io/badge/Multi--Agent%20Swarm-8B5CF6?style=flat-square" alt="Multi-Agent Swarm" />
</p>

<p align="center">
  <strong>The First Distributed AI OS.</strong><br/>
  <em>真正的全息感知、多态神经与边缘算力虫群底座。</em>
</p>

---

## Why Jachin Nexus?

> **Enough with chatbots.** Enough with expensive, black-box BaaS that locks you in and bleeds your budget.

| Pain | Jachin Nexus |
|------|--------------|
| Monolithic Chat UI | **Omni-Sensory Bus** — UI decoupled, capability negotiation, streaming typewriter |
| Centralized compute | **Edge Mesh Swarm** — P2P task bounty, any device becomes a compute node |
| Vendor lock-in | **De-BaaSification** — Drizzle ORM, PostgreSQL, Redis. Zero-friction Kubernetes deployment |
| Single-agent bottleneck | **Cognitive Handoff** — Multi-persona, memory-preserving soul transfer in milliseconds |

**De-BaaSification (绝对主权)** — We reclaimed Layer 1. PostgreSQL + Drizzle ORM. No BaaS lock-in. Your data, your rules. One `helm install` and you own the stack.

---

## The Singularity Architecture

| Kill Switch | Description |
|-------------|-------------|
| 🛡️ **Aegis Shield (神盾高可用)** | Token Compaction when context overflows. LLM failover with millisecond retry. Your agent never dies mid-thought. |
| 🐝 **Edge Mesh Swarm (算力虫群)** | Break physical boundaries. P2P LAN task bounty. Heavy tools offloaded to idle nodes. All devices, one brain. |
| 📡 **P2P Routing (能直连绝不绕路)** | mDNS LAN discovery, WebRTC hole-punching. Control plane vs data plane separation. |
| 🎭 **Cognitive Handoff (认知接力)** | Millisecond multi-agent soul transfer. Architect → Researcher → Default. Memory preserved, persona switched. |
| 🌙 **Dream Weaver (梦境重塑)** | Idle-time memory consolidation. Conflict resolution. Short-term → core memory. Your agent dreams. |
| ⚡ **Omni-Sensory Bus (全息感官)** | UI fully decoupled. Streaming typewriter, HITL popups, Swarm radar. Capability negotiation per device. |

---

## SaaS-Ready (企业级多租户)

Layer 1 defaults to **Platform First** — B2B2C multi-tenancy out of the box.

| Entity | Isolation |
|--------|-----------|
| **Organizations** | Fleet ownership, billing plans |
| **Organization Users** | owner / admin / member roles |
| **Edge Agents** | `organization_id` — personal or enterprise fleet |
| **Blueprints** | Shared within org, licensed per fleet |
| **Transactions** | Enterprise procurement, license keys |

One platform. Millions of tenants. Zero cross-leak.

---

## Quick Start (造物主点火指南)

详见 [docs/QUICKSTART.md](docs/QUICKSTART.md)

```bash
# 1. Layer 1 — Database (cloud/nexus)
cd cloud/nexus && npm run db:push

# 2. Layer 2 — 控制面
python -m core.main

# 3. Layer 3 — Desktop Console
cd clients/desktop && npm run tauri dev
```

**Or one-shot (Windows):** `.\start.bat`

---

## Project Structure

```
jachin-system/
├── cloud/nexus/          # Layer 1 — Next.js + Drizzle ORM + Auth.js
├── core/                 # Layer 2 — Agent Loop, Swarm, Dream Weaver
├── clients/desktop/      # Layer 3 — Tauri + React (全息指挥台)
├── jachin-plugin-sdk/    # JPP Rust (Wasm)
├── jachin-plugin-sdk-python/  # JPP Python
└── docs/                 # Whitepapers, GTM, Architecture
```

---

## Documentation

| Link | Description |
|------|-------------|
| [📖 Whitepapers & Architecture](./docs/README.md) | Full spec, protocols, commercialization |
| [⚡ Neuron Forge (Plugin SDK)](./jachin-plugin-sdk-python/README.md) | Build Python plugins, monetize in 5 min |
| [🦀 Rust Plugin Scaffold](./jachin-plugin-sdk/README.md) | KB-sized Wasm, zero-trust sandbox |

---

## Contributing & Security

- [CONTRIBUTING.md](./CONTRIBUTING.md) — Code style, PR flow
- [SECURITY.md](./SECURITY.md) — Vulnerability reporting

---

<p align="center">
  <strong>v8.0 The Singularity OS</strong> · Last updated 2026-02
</p>
