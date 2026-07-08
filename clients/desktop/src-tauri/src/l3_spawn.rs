//! L3 引擎生命周期：Tauri Sidecar 或 Python 回退启动 l3_node，应用退出时 kill

use directories::BaseDirs;
use std::fs;
use std::io::Write;
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex, Once, OnceLock};
use std::time::{SystemTime, UNIX_EPOCH};

use tauri_plugin_shell::process::CommandEvent;
use tauri_plugin_shell::ShellExt;

/// 主程序 exe 所在目录（dist_jachin_desktop 或 target/release）
pub fn exe_dir() -> Option<PathBuf> {
    std::env::current_exe()
        .ok()
        .and_then(|p| p.parent().map(|p| p.to_path_buf()))
}

/// `spawn_l3_node` 在 `JACHIN_SKIP_L3_SPAWN=1` 时返回的哨兵错误串（预期行为，非启动失败）
pub const SKIP_L3_AUTO_SPAWN_ERR: &str = "__JACHIN_SKIP_L3_AUTO_SPAWN__";

#[inline]
pub fn is_skip_l3_auto_spawn(err: &str) -> bool {
    err == SKIP_L3_AUTO_SPAWN_ERR
}

/// 将 L3 启动失败信息写入 l3_debug.log（Release 无控制台时用户可查看）
pub fn write_l3_debug(msg: &str) {
    let line = format!("[{}] {}", timestamp(), msg);
    if let Some(dir) = exe_dir() {
        let _ = fs::create_dir_all(dir.join("logs"));
        for log_path in [
            dir.join("l3_debug.log"),
            dir.join("logs").join("l3_debug.log"),
        ] {
            if let Ok(mut f) = fs::OpenOptions::new()
                .create(true)
                .append(true)
                .open(&log_path)
            {
                let _ = writeln!(f, "{}", line);
                let _ = f.flush();
            }
        }
    }
    write_jachin_shared_l3_debug("l3_spawn", msg);
}

fn timestamp() -> String {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| format!("{}.{:03}", d.as_secs(), d.subsec_millis()))
        .unwrap_or_else(|_| "0".to_string())
}

/// 与 L3 Python `early_log` / `~/.jachin/l3_debug.log` 对齐的共享诊断文件（桌面端追加）。
/// 优先 `JACHIN_LOG_DIR/l3_debug.log`，否则 `~/.jachin/l3_debug.log`。
pub fn jachin_shared_l3_debug_path() -> PathBuf {
    if let Ok(dir) = std::env::var("JACHIN_LOG_DIR") {
        return PathBuf::from(dir).join("l3_debug.log");
    }
    BaseDirs::new()
        .map(|b| b.home_dir().join(".jachin").join("l3_debug.log"))
        .unwrap_or_else(|| std::env::temp_dir().join("l3_debug.log"))
}

// Omni 热键落盘见 `omni_hotkey_mirror_trace`（避免与 `l3_spawn` 重复两套路径逻辑）。

/// 桌面诊断目录：`%USERPROFILE%\.jachin\jachin_debug`（与热更新调试同目录）。
pub fn jachin_debug_dir() -> PathBuf {
    std::env::var("JACHIN_HOT_UPDATE_DEBUG_DIR")
        .ok()
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
        .map(PathBuf::from)
        .unwrap_or_else(|| {
            let base = std::env::var("USERPROFILE")
                .or_else(|_| std::env::var("HOME"))
                .unwrap_or_else(|_| "C:\\Users\\Public".to_string());
            PathBuf::from(base).join(".jachin").join("jachin_debug")
        })
}

/// 陪伴语音专用日志：`jachin_debug/voice_companion.log`
pub fn write_voice_companion_debug(webview: &str, stage: &str, message: &str, detail: &str) {
    let dir = jachin_debug_dir();
    if let Err(e) = fs::create_dir_all(&dir) {
        eprintln!("[voice_companion_debug] mkdir {}: {}", dir.display(), e);
        return;
    }
    let path = dir.join("voice_companion.log");
    let now_ms = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis())
        .unwrap_or(0);
    let pid = std::process::id();
    let msg = message.replace('\r', " ").replace('\n', " | ");
    let det = detail.replace('\r', " ").replace('\n', " | ");
    let line = format!(
        "{}ms [pid={}] [webview={}] [stage={}] {} | {}\n",
        now_ms, pid, webview, stage, msg, det
    );
    match fs::OpenOptions::new().create(true).append(true).open(&path) {
        Ok(mut f) => {
            if let Err(e) = f.write_all(line.as_bytes()) {
                eprintln!("[voice_companion_debug] write {}: {}", path.display(), e);
            }
            let _ = f.flush();
        }
        Err(e) => eprintln!("[voice_companion_debug] open {}: {}", path.display(), e),
    }
}

/// 大窗语音按钮链路：`jachin_debug/voice_chat.log`（PTT / VAD → STT → L3 → TTS）
pub fn write_voice_chat_trace(trace_id: &str, stage: &str, message: &str, detail: &str) {
    let dir = jachin_debug_dir();
    if let Err(e) = fs::create_dir_all(&dir) {
        eprintln!("[voice_chat_trace] mkdir {}: {}", dir.display(), e);
        return;
    }
    let path = dir.join("voice_chat.log");
    let now_ms = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis())
        .unwrap_or(0);
    let pid = std::process::id();
    let tid = trace_id.chars().take(64).collect::<String>();
    let stg = stage.chars().take(128).collect::<String>();
    let msg = message.replace('\r', " ").replace('\n', " | ");
    let det = detail.replace('\r', " ").replace('\n', " | ");
    let line = format!(
        "{}ms [pid={}] [trace={}] [stage={}] {} | {}\n",
        now_ms, pid, tid, stg, msg, det
    );
    match fs::OpenOptions::new().create(true).append(true).open(&path) {
        Ok(mut f) => {
            if let Err(e) = f.write_all(line.as_bytes()) {
                eprintln!("[voice_chat_trace] write {}: {}", path.display(), e);
            }
            let _ = f.flush();
        }
        Err(e) => eprintln!("[voice_chat_trace] open {}: {}", path.display(), e),
    }
}

/// 追加一行 UTF-8（自动建目录）；单行内换行会压成 ` | `，便于与 L3 日志混排检索。
pub fn write_jachin_shared_l3_debug(category: &str, message: &str) {
    let path = jachin_shared_l3_debug_path();
    if let Some(parent) = path.parent() {
        if let Err(e) = fs::create_dir_all(parent) {
            eprintln!(
                "[Desktop] mkdir for l3_debug.log failed: {} ({})",
                e,
                parent.display()
            );
            return;
        }
    }
    let now_ms = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis())
        .unwrap_or(0);
    let pid = std::process::id();
    let sanitized = message.replace('\r', " ").replace('\n', " | ");
    let line = format!(
        "{}ms [Desktop][{}] [pid={}] {}\n",
        now_ms, category, pid, sanitized
    );
    match fs::OpenOptions::new().create(true).append(true).open(&path) {
        Ok(mut f) => {
            if let Err(e) = f.write_all(line.as_bytes()) {
                eprintln!("[Desktop] write {}: {}", path.display(), e);
            }
            let _ = f.flush();
        }
        Err(e) => eprintln!("[Desktop] open l3_debug.log {}: {}", path.display(), e),
    }
}

/// 供 Ctrl+C 时 kill 使用
static L3_FOR_CTRLC: OnceLock<Arc<L3Handle>> = OnceLock::new();
static JVS_FOR_CTRLC: OnceLock<Arc<crate::jvs::process_manager::JvsHandle>> = OnceLock::new();
static CTRLC_HANDLER: Once = Once::new();
static CTRLC_SHUTTING_DOWN: AtomicBool = AtomicBool::new(false);

#[cfg(windows)]
#[link(name = "kernel32")]
extern "system" {
    fn ExitProcess(u_exit_code: u32) -> !;
}

fn force_process_exit(code: i32) -> ! {
    #[cfg(windows)]
    unsafe {
        ExitProcess(code as u32);
    }
    #[cfg(not(windows))]
    {
        std::process::exit(code);
    }
}

/// 持有 L3 子进程句柄，Drop 时 kill 避免僵尸进程
pub struct L3Handle(pub Mutex<Option<tauri_plugin_shell::process::CommandChild>>);

impl L3Handle {
    pub fn new(child: tauri_plugin_shell::process::CommandChild) -> Self {
        L3Handle(Mutex::new(Some(child)))
    }

    #[allow(dead_code)]
    pub fn kill(&self) {
        if let Ok(mut guard) = self.0.lock() {
            if let Some(c) = guard.take() {
                let _ = c.kill();
            }
        }
    }

    /// 替换子进程（用于 gateway_connect 重启 L3）
    pub fn replace(&self, child: tauri_plugin_shell::process::CommandChild) {
        if let Ok(mut guard) = self.0.lock() {
            if let Some(old) = guard.take() {
                let _ = old.kill();
            }
            *guard = Some(child);
        }
    }
}

/// 注册 Ctrl+C 时 kill L3，确保端口释放
pub fn register_ctrlc_kill(handle: &Arc<L3Handle>) {
    let _ = L3_FOR_CTRLC.set(handle.clone());
    install_ctrlc_shutdown_handler();
}

fn install_ctrlc_shutdown_handler() {
    CTRLC_HANDLER.call_once(|| {
        let _ = ctrlc::set_handler(move || {
            if CTRLC_SHUTTING_DOWN.swap(true, Ordering::SeqCst) {
                force_process_exit(0);
            }
            if let Some(l3) = L3_FOR_CTRLC.get() {
                l3.kill();
                eprintln!("[L3] Ctrl+C 已结束 L3 子进程，端口已释放");
            }
            if let Some(jvs) = JVS_FOR_CTRLC.get() {
                jvs.stop();
                eprintln!("[JVS] Ctrl+C 已结束语音服务子进程");
            }
            crate::commands::english_vocab::shutdown_english_vocab_service();
            force_process_exit(0);
        });
    });
}

pub fn register_ctrlc_jvs(handle: &Arc<crate::jvs::process_manager::JvsHandle>) {
    let _ = JVS_FOR_CTRLC.set(handle.clone());
    install_ctrlc_shutdown_handler();
}

impl Drop for L3Handle {
    fn drop(&mut self) {
        if let Ok(mut guard) = self.0.lock() {
            if let Some(c) = guard.take() {
                let _ = c.kill();
            }
        }
    }
}

fn gateway_config_path() -> Option<PathBuf> {
    let home = std::env::var("USERPROFILE")
        .ok()
        .or_else(|| std::env::var("HOME").ok())?;
    Some(
        PathBuf::from(home)
            .join(".jachin")
            .join("l2_gateway_config.json"),
    )
}

/// 便携包安装根（主程序 exe 目录）：设置 JACHIN_APP_ROOT / JACHIN_LOG_DIR，与 run_l3.bat 一致。
fn is_portable_install_dir(dir: &PathBuf) -> bool {
    dir.join(".env").is_file()
        && dir
            .join("bin")
            .join(l3_sidecar_portable_exe_name())
            .is_file()
}

fn apply_portable_install_env(
    mut cmd: tauri_plugin_shell::process::Command,
    install_dir: &PathBuf,
) -> tauri_plugin_shell::process::Command {
    let app_root = if is_portable_install_dir(install_dir) {
        install_dir.clone()
    } else {
        project_root().unwrap_or_else(|| install_dir.clone())
    };
    let logs_dir = install_dir.join("logs");
    let _ = fs::create_dir_all(&logs_dir);
    cmd = cmd
        .env("JACHIN_APP_ROOT", app_root.to_string_lossy().as_ref())
        .env("JACHIN_LOG_DIR", logs_dir.to_string_lossy().as_ref());
    cmd
}

fn sidecar_line_mirror_worthy(line: &str) -> bool {
    let t = line.trim();
    if t.is_empty() {
        return false;
    }
    let l = t.to_lowercase();
    l.contains("[l3")
        || l.contains("[im channels")
        || l.contains("error")
        || l.contains("warning")
        || l.contains("traceback")
        || l.contains("connected to wss")
        || l.contains("启动")
        || l.contains("health")
        || l.contains("[l3 runtime]")
}

fn spawn_sidecar_output_forwarder(
    mut rx: tauri::async_runtime::Receiver<tauri_plugin_shell::process::CommandEvent>,
) {
    tauri::async_runtime::spawn(async move {
        while let Some(event) = rx.recv().await {
            match event {
                CommandEvent::Stdout(bytes) => {
                    let _ = std::io::stdout().write_all(&bytes);
                    let _ = std::io::stdout().flush();
                    if let Ok(s) = std::str::from_utf8(&bytes) {
                        for line in s.lines() {
                            if sidecar_line_mirror_worthy(line) {
                                write_jachin_shared_l3_debug("l3_sidecar", line);
                            }
                        }
                    }
                }
                CommandEvent::Stderr(bytes) => {
                    let _ = std::io::stderr().write_all(&bytes);
                    let _ = std::io::stderr().flush();
                    if let Ok(s) = std::str::from_utf8(&bytes) {
                        for line in s.lines() {
                            if !line.trim().is_empty() {
                                write_jachin_shared_l3_debug("l3_sidecar_stderr", line);
                            }
                        }
                    }
                }
                CommandEvent::Terminated(payload) => {
                    write_jachin_shared_l3_debug(
                        "l3_spawn",
                        &format!(
                            "侧车进程退出 code={:?} signal={:?}",
                            payload.code, payload.signal
                        ),
                    );
                }
                _ => {}
            }
        }
    });
}

fn spawn_l3_health_probe() {
    tauri::async_runtime::spawn(async move {
        let client = match reqwest::Client::builder()
            .timeout(std::time::Duration::from_secs(2))
            .build()
        {
            Ok(c) => c,
            Err(e) => {
                write_jachin_shared_l3_debug(
                    "l3_spawn",
                    &format!("健康检查 HTTP 客户端创建失败: {}", e),
                );
                return;
            }
        };
        let ports = [18991u16, 18990, 18992, 18993, 18994];
        for attempt in 1..=30u32 {
            tokio::time::sleep(tokio::time::Duration::from_secs(2)).await;
            for port in ports {
                let url = format!("http://127.0.0.1:{}/api/health", port);
                if let Ok(resp) = client.get(&url).send().await {
                    if resp.status().is_success() {
                        let body = resp.text().await.unwrap_or_default();
                        let preview: String = body.chars().take(240).collect();
                        write_jachin_shared_l3_debug(
                            "l3_spawn",
                            &format!(
                                "侧车健康检查 OK (~{}s) port={} body={}",
                                attempt * 2,
                                port,
                                preview
                            ),
                        );
                        return;
                    }
                }
            }
            if attempt == 15 {
                write_jachin_shared_l3_debug(
                    "l3_spawn",
                    "侧车仍在启动中（约 30s）… 详见 logs/l3_debug.log 或 ~/.jachin/l3_debug.log",
                );
            }
        }
        write_jachin_shared_l3_debug(
            "l3_spawn",
            "侧车 60s 内未通过 /api/health；请检查 bin/l3_node、.env 与 logs/l3_debug.log",
        );
    });
}

fn existing_l3_health_ok() -> Option<(u16, String)> {
    let client = reqwest::blocking::Client::builder()
        .timeout(std::time::Duration::from_secs(2))
        .build()
        .ok()?;
    for port in [18991u16, 18990, 18992, 18993, 18994] {
        let url = format!("http://127.0.0.1:{}/api/health", port);
        if let Ok(resp) = client.get(&url).send() {
            if resp.status().is_success() {
                let body = resp.text().unwrap_or_default();
                let preview: String = body.chars().take(240).collect();
                return Some((port, preview));
            }
        }
    }
    None
}

fn should_use_gateway_mode() -> Option<String> {
    let path = gateway_config_path()?;
    let content = fs::read_to_string(&path).ok()?;
    let cfg: serde_json::Value = serde_json::from_str(&content).ok()?;
    let url = cfg.get("l2_base_url")?.as_str()?;
    if url.is_empty() {
        return None;
    }
    Some(url.to_string())
}

/// 推断项目根目录（含 l3_node 包）
pub fn project_root() -> Option<PathBuf> {
    // 1. 优先尝试当前工作目录（tauri dev 时多为 clients/desktop 或项目根）
    if let Ok(cwd) = std::env::current_dir() {
        let mut p = cwd.as_path();
        for _ in 0..8 {
            if p.join("l3_node").join("__main__.py").exists() {
                return Some(p.to_path_buf());
            }
            p = p.parent()?;
        }
    }
    // 2. 从可执行文件路径向上查找：target/debug -> ... -> project_root
    let exe = std::env::current_exe().ok()?;
    let mut p = exe.parent()?;
    for _ in 0..10 {
        if p.join("l3_node").join("__main__.py").exists() {
            return Some(p.to_path_buf());
        }
        p = p.parent()?;
    }
    None
}

const L3_ENV_KEYS: &[&str] = &[
    "DASHSCOPE_API_KEY",
    "DASHSCOPE_API_KEY_SEA",
    "DASHSCOPE_API_KEY_CN",
    "DASHSCOPE_API_BASE",
    "DASHSCOPE_API_BASE_CN",
    "DASHSCOPE_API_BASE_SEA",
    "JACHIN_ACTIVE_REGION",
    "OPENAI_API_KEY",
    // MCP tavily-mcp：占位符 ${TAVILY_API_KEY} 从 L3 进程 os.environ 展开；须与 ~/.jachin/.env 一并注入，否则子进程报 -32600
    "TAVILY_API_KEY",
    "LITELLM_FALLBACK_MODELS",
    "LLM_MODEL",
    "JACHIN_L3_DEBUG",
    "L3_VERBOSE_LOG",
    "LOG_LEVEL",
    // PMO 战报推送目标（须注入 L3 侧车；否则仅 Python 读盘 .env 可能晚于 MCP 占位符展开）
    "LARK_APP_ID",
    "LARK_APP_SECRET",
    "PMO_PRIMARY_CHAT_ID",
    "PMO_MONITOR_CHAT_ID",
    "PMO_PUSH_MONITOR",
    "PMO_CHANGE_ALERT_CHAT_ID",
    "PMO_CHANGE_ALERT_MONITOR_CHAT_ID",
    "PMO_BITABLE_WATCH_ENABLED",
    "PMO_CHANGE_ALERT_ENABLED",
    // 安全锁：控制台「安全锁审批」与 CLI approve 均依赖 L3 进程内该变量；须从项目 .env 注入子进程（原白名单未包含会导致 503 admin_token_not_configured）
    "JACHIN_SAFETY_LOCK_ADMIN_TOKEN",
    "JACHIN_SAFETY_LOCK_LEARN",
    // Kalaroko 巡检飞书自建应用 Open API（卡片 + 话题回复；与 desktop .env 同步注入 L3 侧车）
    "FEISHU_APP_ID",
    "FEISHU_APP_SECRET",
    "FEISHU_CHAT_ID",
];

fn parse_env_file(path: &PathBuf) -> Vec<(String, String)> {
    let mut vars = Vec::new();
    let Ok(content) = fs::read_to_string(path) else {
        return vars;
    };
    for line in content.lines() {
        let line = line.trim();
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        if let Some((k, v)) = line.split_once('=') {
            let k = k.trim().to_string();
            let v = v.trim().trim_matches('"').trim_matches('\'').to_string();
            if !v.is_empty() && L3_ENV_KEYS.contains(&k.as_str()) {
                vars.push((k, v));
            }
        }
    }
    vars
}

/// 打包/便携安装：`.env` 未配置时默认关闭 PMO 变更预警（不覆盖用户显式 `=1`）。
fn ensure_pmo_alert_disabled_by_default(mut vars: Vec<(String, String)>) -> Vec<(String, String)> {
    for key in ["PMO_BITABLE_WATCH_ENABLED", "PMO_CHANGE_ALERT_ENABLED"] {
        if !vars.iter().any(|(k, _)| k == key) {
            vars.push((key.to_string(), "0".to_string()));
        }
    }
    vars
}

/// 从项目根 `.env` 与 `~/.jachin/.env` 读取白名单变量（`L3_ENV_KEYS`），供 L3 子进程使用。
/// 统帅目录在后合并且**不覆盖**项目已有同名键（与 Python `load_dotenv(..., override=false)` 一致）。
/// 此前仅在项目缺少 DASHSCOPE/OPENAI 时才读统帅目录，导致「项目已有 Key、TAVILY 只在 ~/.jachin/.env」时子进程永远拿不到 TAVILY。
pub fn load_l3_env_vars(root: &PathBuf) -> Vec<(String, String)> {
    let mut vars = parse_env_file(&root.join(".env"));
    if let Ok(home) = std::env::var("USERPROFILE").or_else(|_| std::env::var("HOME")) {
        let home_env = PathBuf::from(home).join(".jachin").join(".env");
        for (k, v) in parse_env_file(&home_env) {
            if !vars.iter().any(|(ek, _)| ek == &k) {
                vars.push((k, v));
            }
        }
    }
    if portable_l3_sidecar_exe_path()
        .map(|p| p.is_file())
        .unwrap_or(false)
    {
        ensure_pmo_alert_disabled_by_default(vars)
    } else {
        vars
    }
}

/// Tauri `externalBin` 路径（与 `tauri.conf.json` 的 `bin/l3_node` 一致）
pub fn l3_sidecar_external_bin_path() -> &'static str {
    "bin/l3_node"
}

fn l3_sidecar_target_triple() -> &'static str {
    if cfg!(all(target_os = "windows", target_arch = "x86_64")) {
        "x86_64-pc-windows-msvc"
    } else if cfg!(all(target_os = "windows", target_arch = "aarch64")) {
        "aarch64-pc-windows-msvc"
    } else if cfg!(all(target_os = "linux", target_arch = "x86_64")) {
        "x86_64-unknown-linux-gnu"
    } else if cfg!(all(target_os = "linux", target_arch = "aarch64")) {
        "aarch64-unknown-linux-gnu"
    } else if cfg!(all(target_os = "macos", target_arch = "aarch64")) {
        "aarch64-apple-darwin"
    } else if cfg!(all(target_os = "macos", target_arch = "x86_64")) {
        "x86_64-apple-darwin"
    } else {
        "x86_64-pc-windows-msvc"
    }
}

/// 便携包 `exe_dir/bin/` 下侧车文件名（与 `build_l3_sidecar.py` 产出一致）
pub fn l3_sidecar_portable_exe_name() -> String {
    let t = l3_sidecar_target_triple();
    if cfg!(target_os = "windows") {
        format!("l3_node-{}.exe", t)
    } else {
        format!("l3_node-{}", t)
    }
}

/// 主程序旁便携侧车绝对路径（随安装/解压位置变化，**不**含编译机固定盘符）
pub fn portable_l3_sidecar_exe_path() -> Option<PathBuf> {
    let dir = exe_dir()?;
    Some(dir.join("bin").join(l3_sidecar_portable_exe_name()))
}

/// 直接通过 exe 路径启动（绕过 Tauri Sidecar 路径解析，解决 bundle.active:false 时 Tauri 找不到 bin 的问题）
pub fn spawn_l3_via_direct_exe(
    app: &impl tauri::Manager<tauri::Wry>,
    args: &[&str],
    env_url: Option<&str>,
    env_vars: &[(String, String)],
    env_device_name: Option<&str>,
) -> Result<tauri_plugin_shell::process::CommandChild, String> {
    let dir = exe_dir().ok_or("无法获取 exe 目录")?;
    let bin_dir = dir.join("bin");
    let exe_name = l3_sidecar_portable_exe_name();
    let exe_path = bin_dir.join(&exe_name);
    if !exe_path.exists() {
        return Err(format!(
            "L3 exe 不存在: {}，请运行 .\\scripts\\build_full.ps1",
            exe_path.display()
        ));
    }
    let mut cmd = app
        .shell()
        .command(exe_path.to_string_lossy().as_ref())
        .args(args)
        .env("PYTHONUTF8", "1");
    cmd = apply_portable_install_env(cmd, &dir);
    if let Some(url) = env_url {
        cmd = cmd.env("L2_BASE_URL", url);
    }
    for (k, v) in env_vars {
        cmd = cmd.env(k, v);
    }
    if let Some(name) = env_device_name {
        let trimmed = name.trim();
        if !trimmed.is_empty() {
            cmd = cmd.env(
                "JACHIN_DEVICE_NAME",
                trimmed.chars().take(64).collect::<String>(),
            );
        }
    }
    cmd = cmd.current_dir(&dir);
    let (rx, child) = cmd
        .spawn()
        .map_err(|e| format!("直接启动 L3 exe 失败: {}", e))?;
    spawn_sidecar_output_forwarder(rx);
    Ok(child)
}

/// 使用 Python 回退启动 l3_node（Sidecar 不可用时）
///
/// `env_overlay`：在 `load_l3_env_vars` 之后按 key 覆盖（与 Sidecar/直接 exe 路径一致）。
/// 典型场景：`gateway_connect` 已合并 `JACHIN_ACTIVE_REGION=SEA`，但项目根 `.env` 仍为 CN 时，
/// 若此处不覆盖，L3 进程会误用旧区域，进而出现「选 SEA 时配对/行为异常」。
pub fn spawn_l3_via_python(
    app: &impl tauri::Manager<tauri::Wry>,
    args: &[&str],
    env_l2_url: Option<&str>,
    env_device_name: Option<&str>,
    env_overlay: &[(String, String)],
) -> Result<tauri_plugin_shell::process::CommandChild, String> {
    let root = project_root().ok_or("无法定位项目根目录 (l3_node)")?;
    let mut cmd = app
        .shell()
        .command("python")
        .args(["-m", "l3_node"])
        .args(args);
    cmd = cmd
        .env("PYTHONUNBUFFERED", "1")
        .env("PYTHONUTF8", "1")
        .env("JACHIN_APP_ROOT", root.to_string_lossy().as_ref());
    let mut merged = load_l3_env_vars(&root);
    for (k, v) in env_overlay {
        merged.retain(|(ek, _)| ek != k);
        merged.push((k.clone(), v.clone()));
    }
    for (k, v) in merged {
        cmd = cmd.env(&k, &v);
    }
    if let Some(url) = env_l2_url {
        cmd = cmd.env("L2_BASE_URL", url);
    }
    if let Some(name) = env_device_name {
        let trimmed = name.trim();
        if !trimmed.is_empty() {
            cmd = cmd.env(
                "JACHIN_DEVICE_NAME",
                trimmed.chars().take(64).collect::<String>(),
            );
        }
    }
    cmd = cmd.current_dir(&root);
    let (rx, child) = cmd
        .spawn()
        .map_err(|e| format!("Python L3 启动失败: {}", e))?;
    spawn_sidecar_output_forwarder(rx);
    Ok(child)
}

/// 静默启动 l3_node：优先 Sidecar，失败则回退到 python -m l3_node
/// 若有 l2_gateway_config 则用 --gateway 模式，否则 --ws-only
/// 若环境变量 JACHIN_SKIP_L3_SPAWN=1，则不启动第二条 L3（与 `start-layer3.ps1` 等同控制台已跑的 python -m l3_node 共存）
pub fn spawn_l3_node(
    app: &impl tauri::Manager<tauri::Wry>,
) -> Result<tauri_plugin_shell::process::CommandChild, String> {
    if std::env::var("JACHIN_SKIP_L3_SPAWN").as_deref() == Ok("1") {
        write_l3_debug("[L3] Sidecar 跳过：JACHIN_SKIP_L3_SPAWN=1（外部/脚本已托管 L3，非错误）");
        return Err(SKIP_L3_AUTO_SPAWN_ERR.to_string());
    }
    if let Some((port, body)) = existing_l3_health_ok() {
        write_jachin_shared_l3_debug(
            "l3_spawn",
            &format!(
                "检测到已有健康 L3，复用现有实例，不重复拉起 Sidecar port={} body={}",
                port, body
            ),
        );
        return Err(SKIP_L3_AUTO_SPAWN_ERR.to_string());
    }
    let (args, env_url, mode) = if let Some(url) = should_use_gateway_mode() {
        (["--gateway"].as_slice(), Some(url), "gateway")
    } else {
        (["--ws-only"].as_slice(), None, "ws-only")
    };

    let exe_display = exe_dir()
        .map(|d| d.display().to_string())
        .unwrap_or_else(|| "?".to_string());
    write_jachin_shared_l3_debug(
        "l3_spawn",
        &format!(
            "Desktop 开始拉起 L3 侧车 mode={} install_dir={} shared_log={}",
            mode,
            exe_display,
            jachin_shared_l3_debug_path().display()
        ),
    );

    let exe_root = exe_dir().unwrap_or_else(PathBuf::new);
    let root = project_root().unwrap_or_else(PathBuf::new);
    let env_root = if is_portable_install_dir(&exe_root) {
        exe_root.clone()
    } else if root.as_os_str().is_empty() {
        exe_root.clone()
    } else {
        root.clone()
    };
    let env_vars = load_l3_env_vars(&env_root);

    // 便携包：侧车与主程序同目录树 `主程序.exe` + `bin/l3_node-<triple>.exe`。
    // 路径始终由 current_exe 推导（解压到任意盘符均可），但 tauri `sidecar()` 在部分环境下会错误解析导致 os error 2；
    // 若文件已存在则直接按绝对路径启动，避免先失败再回退的噪音与偶发问题。
    if let Some(ref p) = portable_l3_sidecar_exe_path() {
        if p.is_file() {
            write_l3_debug(&format!(
                "[L3] 使用便携侧车（相对主程序目录）: {}",
                p.display()
            ));
            let child = spawn_l3_via_direct_exe(app, args, env_url.as_deref(), &env_vars, None)?;
            write_l3_debug(&format!(
                "L3 引擎已启动 ws://127.0.0.1:18981 (mode={})",
                mode
            ));
            spawn_l3_health_probe();
            return Ok(child);
        }
    }

    let child = match app.shell().sidecar(l3_sidecar_external_bin_path()) {
        Ok(sidecar) => {
            let mut sidecar = sidecar.args(args).env("PYTHONUTF8", "1");
            if let Some(ref dir) = exe_dir() {
                sidecar = apply_portable_install_env(sidecar, dir);
            }
            if let Some(ref url) = env_url {
                sidecar = sidecar.env("L2_BASE_URL", url.as_str());
            }
            for (k, v) in &env_vars {
                sidecar = sidecar.env(k, v);
            }
            if let Some(ref dir) = exe_dir() {
                sidecar = sidecar.current_dir(dir);
            }
            match sidecar.spawn() {
                Ok((rx, c)) => {
                    let child = c;
                    spawn_sidecar_output_forwarder(rx);
                    child
                }
                Err(e) => {
                    let err_msg = e.to_string();
                    let expected = exe_dir()
                        .map(|d| {
                            format!(
                                "{}",
                                d.join("bin").join(l3_sidecar_portable_exe_name()).display()
                            )
                        })
                        .unwrap_or_else(|| format!("bin/{}", l3_sidecar_portable_exe_name()));
                    write_l3_debug(&format!(
                        "Sidecar spawn 失败: {} (期望路径: {})",
                        err_msg, expected
                    ));
                    if err_msg.contains("找不到")
                        || err_msg.contains("path")
                        || err_msg.contains("os error 3")
                        || err_msg.contains("os error 2")
                        || err_msg.contains("not found")
                    {
                        write_l3_debug("尝试直接启动 exe_dir/bin/l3_node-<triple>.exe");
                        match spawn_l3_via_direct_exe(
                            app,
                            args,
                            env_url.as_deref(),
                            &env_vars,
                            None,
                        ) {
                            Ok(c) => c,
                            Err(direct_err) => {
                                write_l3_debug(&format!("直接 exe 启动失败: {}", direct_err));
                                if project_root().is_some() {
                                    write_l3_debug("回退到 python -m l3_node");
                                    spawn_l3_via_python(
                                        app,
                                        args,
                                        env_url.as_deref(),
                                        None,
                                        &env_vars,
                                    )?
                                } else {
                                    return Err(format!(
                                        "L3 启动失败: {}；直接 exe: {}",
                                        err_msg, direct_err
                                    ));
                                }
                            }
                        }
                    } else {
                        let msg = format!("L3 启动失败: {}", err_msg);
                        write_l3_debug(&msg);
                        return Err(msg);
                    }
                }
            }
        }
        Err(e) => {
            let err_msg = e.to_string();
            write_l3_debug(&format!(
                "Sidecar 未找到: {}，尝试直接启动 exe_dir/bin 下 l3_node 侧车",
                err_msg
            ));
            match spawn_l3_via_direct_exe(app, args, env_url.as_deref(), &env_vars, None) {
                Ok(c) => c,
                Err(direct_err) => {
                    write_l3_debug(&format!("直接 exe 启动失败: {}", direct_err));
                    if project_root().is_some() {
                        write_l3_debug("回退到 python -m l3_node");
                        spawn_l3_via_python(app, args, env_url.as_deref(), None, &env_vars)?
                    } else {
                        let msg = format!(
                            "L3 启动失败: {}。便携包需确保 bin/{} 存在，请运行: .\\scripts\\build_full.ps1",
                            direct_err,
                            l3_sidecar_portable_exe_name()
                        );
                        write_l3_debug(&msg);
                        return Err(msg);
                    }
                }
            }
        }
    };

    write_l3_debug(&format!(
        "L3 引擎已启动 ws://127.0.0.1:18981 (mode={})",
        mode
    ));
    spawn_l3_health_probe();
    Ok(child)
}
