Samuel 指挥官，这是将极致硬核技术包装成“苹果级”商业体验的最终外壳——07_LAYER3_TERMINAL.md！

如果没有这一层，Jachin Nexus 依然只是个在终端里跑脚本的极客玩具。正是 Layer 3 的存在，让没有任何技术背景的 C 端用户和连锁企业前台，都能在 3 秒内把设备无缝接入你的千亿级算力网络。

请直接复制以下内容，覆盖或新建你的 docs/07_LAYER3_TERMINAL.md：

📄 文件：docs/07_LAYER3_TERMINAL.md
Markdown
# 07 — Layer 3: Jachin Terminal (灵动桌面端)

**文档类型**: 白皮书 · Layer 3 详细说明  
**版本**: v8.0 (The Singularity OS)

---

## 一、 定位与哲学 (Positioning & Philosophy)

Layer 3 是 Jachin Nexus 的**零摩擦体验外壳 (Zero-Friction UX Shell)**。

* **核心价值：隐形化管理**。它的主要目的不是计算，而是把最硬核的配对协议、底层 Daemon 引擎和物理沙箱完美隐藏在极简的毛玻璃 UI 之下。
* **业务目标：消除装机门槛**。让没有任何代码背景的用户，只需双击运行、掏出手机扫码，就能在 3 秒内将当前设备化作星图中的一个边缘算力节点。
* **v8.0 多态设计**：Layer 3 客户端是**多态的**，涵盖 PC 桌面端、移动端、树莓派、无屏设备。桌面精灵只是其中一种「视觉展示 Skill/Client」，并非唯一形态。



---

## 二、 技术栈与形态 (Tech Stack)

* **前端**: React + TypeScript + Tailwind CSS (毛玻璃赛博朋克风)。
* **后端**: Tauri v2 (Rust)，负责极其底层的 OS 级系统调用与进程接管。
* **形态**: 轻量级、跨平台的单文件桌面端应用 (Windows / macOS / Linux)，启动后缩入系统托盘。

---

## 三、 核心杀器功能 (Core Features)

### 3.1 扫码即连 (Scan-to-Connect)
* **组件**: `src/components/PairingScreen.tsx`
* **交互体验**: 
  1. 首次双击启动，屏幕中央浮现动态二维码与 6 位短码。
  2. 用户使用已登录 Layer 1 的手机扫描二维码（或在控制台输入短码）。
  3. 桌面端 (Rust) 在后台通过 HTTP 长轮询捕获云端授权状态。
  4. 授权成功瞬间，Rust 自动将 `access_token` 写入本地 `~/.jachin/nexus_config.json`，用户全程无需接触任何配置文件。

### 3.2 引擎静默唤醒 (Silent Daemon Spawn)
* **组件**: `src-tauri/src/commands/daemon.rs`
* **交互体验**: 配对成功后，无需用户打开命令行或配置环境变量。
* **底层机制**: Rust 直接调用 `std::process::Command`（在 Windows 下携带 `CREATE_NO_WINDOW` 标志），在后台完全无感地拉起 Layer 2 的核心引擎 `core/daemon.py`。
* **视觉反馈**: 界面丝滑过渡，提示“✅ 边缘大脑已连接，静默轰鸣中”，随后主界面隐藏。

### 3.3 语音唤醒 (Voice Wake — Hey Jachin)
* **组件**: `src-tauri/src/stt/` (Porcupine/Snowboy + VAD + Whisper)
* **交互**: 听到“Hey Jachin”后，自动触发录音 → Whisper STT → Layer 2 Agent → TTS 播报。复刻钢铁侠 Jarvis 体验。
* **参考**: `docs/VOICE_AND_TTS_GUIDE.md`、`docs/ambient-audio.mdc`

### 3.4 极客视觉流 (Cyber-CLI)
* **jachin-cli pair**：终端配对授权，无需扫码。
* **jachin-cli shell**：终端流光溢彩，满足顶尖黑客控制欲，实时展示 ReAct 日志流。

### 3.5 能力协商 (Capability Negotiation) — v8.0

* **设计**：客户端连接 `ws://localhost:8080/sensory` 时，**必须先发送 Manifest（能力清单）**。
* **示例**：
  * PC 桌面端：`{"device": "pc", "caps": ["ui_render", "hitl_popup"]}`
  * 树莓派：`{"device": "rpi", "caps": ["gpio_control", "audio_play"]}`
  * 手机：`{"device": "phone", "caps": ["push_notification"]}`
* **Layer 2 行为**：根据 `caps` 动态决定推送内容。UI 动画只推给 `ui_render`，HITL 弹窗只推给 `hitl_popup`，语音合成只推给 `audio_play`。桌面精灵彻底降级为一种普通的视觉展示 Skill/Client。

### 3.6 自定义星图 (Custom Nexus) - B端专供
* **功能入口**: 界面右上角的极简“⚙️ 齿轮”图标。
* **应用场景**: 针对购买了 Jachin Nexus 私有化部署的企业客户。管理员可在此处修改 `Nexus Base URL`。填入私有云地址后，Rust 会将其永久固化到本地配置中，Layer 2 守护进程将自动向新的指挥中枢发送心跳。

---

## 四、 极简内部结构 (Minimalist Structure)

为了保证启动速度与极低的内存占用，Layer 3 的结构被压缩到了极致：

```text
clients/desktop/
├── src/
│   ├── components/
│   │   ├── PairingScreen.tsx  # 扫码配对核心 UI
│   │   └── ConsoleApp.tsx     # 配对成功后的状态大盘
│   └── main.tsx
├── src-tauri/
│   ├── src/
│   │   ├── main.rs            # Rust 入口与 Command 注册
│   │   ├── commands/
│   │   │   ├── pairing.rs     # HTTP 轮询与凭证自动化写入
│   │   │   └── daemon.rs      # OS 级 API：无黑框拉起 Python 引擎
│   └── tauri.conf.json
```

## 五、 v8.0 废弃声明 (Deprecation in v8.0)

❌ **废弃极客命令行输入**：彻底取消了要求用户在终端输入 api_key 或配置 .env 文件的反人类设定。

❌ **废弃本地 Dapr 端口映射**：本地 Pub/Sub 端口和 Dapr Sidecar 集成已被彻底移除。所有与云端的通信完全交由后台静默运行的 Layer 2 Daemon (Jachin Mesh WebSocket + HTTP 心跳) 接管，Layer 3 仅专注于 UI 呈现与进程存活监控。