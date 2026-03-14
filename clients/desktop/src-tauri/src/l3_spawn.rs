//! L3 引擎生命周期：Tauri Sidecar 或 Python 回退启动 l3_node，应用退出时 kill

use std::fs;
use std::io::Write;
use std::path::PathBuf;
use std::sync::{Arc, Mutex, OnceLock};

use tauri_plugin_shell::process::CommandEvent;
use tauri_plugin_shell::ShellExt;

/// 主程序 exe 所在目录（dist_jachin_desktop 或 target/release）
pub fn exe_dir() -> Option<PathBuf> {
    std::env::current_exe().ok().and_then(|p| p.parent().map(|p| p.to_path_buf()))
}

/// 将 L3 启动失败信息写入 l3_debug.log（Release 无控制台时用户可查看）
pub fn write_l3_debug(msg: &str) {
    if let Some(dir) = exe_dir() {
        let log_path = dir.join("l3_debug.log");
        if let Ok(mut f) = fs::OpenOptions::new().create(true).append(true).open(&log_path) {
            let _ = writeln!(f, "[{}] {}", timestamp(), msg);
            let _ = f.flush();
        }
    }
}

fn timestamp() -> String {
    use std::time::{SystemTime, UNIX_EPOCH};
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| format!("{}.{:03}", d.as_secs(), d.subsec_millis()))
        .unwrap_or_else(|_| "0".to_string())
}

/// 供 Ctrl+C 时 kill 使用
static L3_FOR_CTRLC: OnceLock<Arc<L3Handle>> = OnceLock::new();

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
    let _ = ctrlc::set_handler(move || {
        if let Some(l3) = L3_FOR_CTRLC.get() {
            l3.kill();
            eprintln!("[L3] Ctrl+C 已结束 L3 子进程，端口已释放");
        }
        std::process::exit(0);
    });
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
    let home = std::env::var("USERPROFILE").ok().or_else(|| std::env::var("HOME").ok())?;
    Some(PathBuf::from(home).join(".jachin").join("l2_gateway_config.json"))
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
fn project_root() -> Option<PathBuf> {
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

/// 从项目根 .env 读取关键变量（DASHSCOPE_API_KEY 等），供 L3 子进程使用
pub fn load_l3_env_vars(root: &PathBuf) -> Vec<(String, String)> {
    let mut vars = Vec::new();
    let env_path = root.join(".env");
    if let Ok(content) = fs::read_to_string(&env_path) {
        for line in content.lines() {
            let line = line.trim();
            if line.is_empty() || line.starts_with('#') {
                continue;
            }
            if let Some((k, v)) = line.split_once('=') {
                let k = k.trim().to_string();
                let v = v.trim().trim_matches('"').trim_matches('\'').to_string();
                if !v.is_empty()
                    && matches!(k.as_str(), "DASHSCOPE_API_KEY" | "OPENAI_API_KEY" | "LITELLM_FALLBACK_MODELS" | "LLM_MODEL")
                {
                    vars.push((k, v));
                }
            }
        }
    }
    vars
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
    let exe_name = if cfg!(target_os = "windows") {
        "l3_node-x86_64-pc-windows-msvc.exe"
    } else {
        "l3_node-x86_64-unknown-linux-gnu"
    };
    let exe_path = bin_dir.join(exe_name);
    if !exe_path.exists() {
        return Err(format!("L3 exe 不存在: {}，请运行 .\\scripts\\build_full.ps1", exe_path.display()));
    }
    let mut cmd = app.shell().command(exe_path.to_string_lossy().as_ref()).args(args).env("PYTHONUTF8", "1");
    if let Some(url) = env_url {
        cmd = cmd.env("L2_BASE_URL", url);
    }
    for (k, v) in env_vars {
        cmd = cmd.env(k, v);
    }
    if let Some(name) = env_device_name {
        let trimmed = name.trim();
        if !trimmed.is_empty() {
            cmd = cmd.env("JACHIN_DEVICE_NAME", trimmed.chars().take(64).collect::<String>());
        }
    }
    cmd = cmd.current_dir(&dir);
    let (mut rx, child) = cmd.spawn().map_err(|e| format!("直接启动 L3 exe 失败: {}", e))?;
    tauri::async_runtime::spawn(async move {
        while let Some(event) = rx.recv().await {
            match event {
                CommandEvent::Stdout(bytes) => {
                    let _ = std::io::stdout().write_all(&bytes);
                    let _ = std::io::stdout().flush();
                }
                CommandEvent::Stderr(bytes) => {
                    let _ = std::io::stderr().write_all(&bytes);
                    let _ = std::io::stderr().flush();
                }
                _ => {}
            }
        }
    });
    Ok(child)
}

/// 使用 Python 回退启动 l3_node（Sidecar 不可用时）
pub fn spawn_l3_via_python(
    app: &impl tauri::Manager<tauri::Wry>,
    args: &[&str],
    env_l2_url: Option<&str>,
    env_device_name: Option<&str>,
) -> Result<tauri_plugin_shell::process::CommandChild, String> {
    let root = project_root().ok_or("无法定位项目根目录 (l3_node)")?;
    let mut cmd = app.shell().command("python").args(["-m", "l3_node"]).args(args);
    cmd = cmd.env("PYTHONUNBUFFERED", "1").env("PYTHONUTF8", "1");
    for (k, v) in load_l3_env_vars(&root) {
        cmd = cmd.env(&k, &v);
    }
    if let Some(url) = env_l2_url {
        cmd = cmd.env("L2_BASE_URL", url);
    }
    if let Some(name) = env_device_name {
        let trimmed = name.trim();
        if !trimmed.is_empty() {
            cmd = cmd.env("JACHIN_DEVICE_NAME", trimmed.chars().take(64).collect::<String>());
        }
    }
    cmd = cmd.current_dir(&root);
    let (mut rx, child) = cmd.spawn().map_err(|e| format!("Python L3 启动失败: {}", e))?;
    tauri::async_runtime::spawn(async move {
        while let Some(event) = rx.recv().await {
            match event {
                CommandEvent::Stdout(bytes) => {
                    let _ = std::io::stdout().write_all(&bytes);
                    let _ = std::io::stdout().flush();
                }
                CommandEvent::Stderr(bytes) => {
                    let _ = std::io::stderr().write_all(&bytes);
                    let _ = std::io::stderr().flush();
                }
                _ => {}
            }
        }
    });
    Ok(child)
}

/// 静默启动 l3_node：优先 Sidecar，失败则回退到 python -m l3_node
/// 若有 l2_gateway_config 则用 --gateway 模式，否则 --ws-only
/// 若环境变量 JACHIN_SKIP_L3_SPAWN=1，则不启动（用户可手动运行 scripts/run_l3.ps1 查看完整日志）
pub fn spawn_l3_node(app: &impl tauri::Manager<tauri::Wry>) -> Result<tauri_plugin_shell::process::CommandChild, String> {
    if std::env::var("JACHIN_SKIP_L3_SPAWN").as_deref() == Ok("1") {
        let msg = "JACHIN_SKIP_L3_SPAWN=1，跳过 L3 自动启动。请手动运行: .\\scripts\\run_l3.ps1";
        write_l3_debug(msg);
        return Err(msg.to_string());
    }
    let (args, env_url, mode) = if let Some(url) = should_use_gateway_mode() {
        (["--gateway"].as_slice(), Some(url), "gateway")
    } else {
        (["--ws-only"].as_slice(), None, "ws-only")
    };

    let root = project_root().unwrap_or_else(PathBuf::new);
    let env_root = if root.as_os_str().is_empty() {
        exe_dir().unwrap_or_else(PathBuf::new)
    } else {
        root.clone()
    };
    let env_vars = load_l3_env_vars(&env_root);

    let child = match app.shell().sidecar("bin/l3_node") {
        Ok(sidecar) => {
            let mut sidecar = sidecar.args(args).env("PYTHONUTF8", "1");
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
                Ok((mut rx, c)) => {
                    let child = c;
                    tauri::async_runtime::spawn(async move {
                        while let Some(event) = rx.recv().await {
                            match event {
                                CommandEvent::Stdout(bytes) => {
                                    let _ = std::io::stdout().write_all(&bytes);
                                    let _ = std::io::stdout().flush();
                                }
                                CommandEvent::Stderr(bytes) => {
                                    let _ = std::io::stderr().write_all(&bytes);
                                    let _ = std::io::stderr().flush();
                                }
                                _ => {}
                            }
                        }
                    });
                    child
                }
                Err(e) => {
                    let err_msg = e.to_string();
                    let expected = exe_dir()
                        .map(|d| format!("{}", d.join("bin").join("l3_node-x86_64-pc-windows-msvc.exe").display()))
                        .unwrap_or_else(|| "bin/l3_node-*.exe".to_string());
                    write_l3_debug(&format!("Sidecar spawn 失败: {} (期望路径: {})", err_msg, expected));
                    if err_msg.contains("找不到") || err_msg.contains("path") || err_msg.contains("os error 3") || err_msg.contains("os error 2") || err_msg.contains("not found") {
                        write_l3_debug("尝试直接启动 exe_dir/bin/l3_node-*.exe");
                        match spawn_l3_via_direct_exe(app, args, env_url.as_deref(), &env_vars, None) {
                            Ok(c) => c,
                            Err(direct_err) => {
                                write_l3_debug(&format!("直接 exe 启动失败: {}", direct_err));
                                if project_root().is_some() {
                                    write_l3_debug("回退到 python -m l3_node");
                                    spawn_l3_via_python(app, args, env_url.as_deref(), None)?
                                } else {
                                    return Err(format!("L3 启动失败: {}；直接 exe: {}", err_msg, direct_err));
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
            write_l3_debug(&format!("Sidecar 未找到: {}，尝试直接启动 exe_dir/bin/l3_node-*.exe", err_msg));
            match spawn_l3_via_direct_exe(app, args, env_url.as_deref(), &env_vars, None) {
                Ok(c) => c,
                Err(direct_err) => {
                    write_l3_debug(&format!("直接 exe 启动失败: {}", direct_err));
                    if project_root().is_some() {
                        write_l3_debug("回退到 python -m l3_node");
                        spawn_l3_via_python(app, args, env_url.as_deref(), None)?
                    } else {
                        let msg = format!(
                            "L3 启动失败: {}。便携包需确保 bin/l3_node-x86_64-pc-windows-msvc.exe 存在，请运行: .\\scripts\\build_full.ps1",
                            direct_err
                        );
                        write_l3_debug(&msg);
                        return Err(msg);
                    }
                }
            }
        }
    };

    write_l3_debug(&format!("L3 引擎已启动 ws://127.0.0.1:18981 (mode={})", mode));
    Ok(child)
}
