//! L3 引擎生命周期：Tauri Sidecar 或 Python 回退启动 l3_node，应用退出时 kill

use std::fs;
use std::path::PathBuf;
use std::sync::Mutex;

use tauri_plugin_shell::ShellExt;

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

/// 使用 Python 回退启动 l3_node（Sidecar 不可用时）
pub fn spawn_l3_via_python(
    app: &impl tauri::Manager<tauri::Wry>,
    args: &[&str],
    env_l2_url: Option<&str>,
) -> Result<tauri_plugin_shell::process::CommandChild, String> {
    let root = project_root().ok_or("无法定位项目根目录 (l3_node)")?;
    let mut cmd = app.shell().command("python").args(["-m", "l3_node"]).args(args);
    if let Some(url) = env_l2_url {
        cmd = cmd.env("L2_BASE_URL", url);
    }
    cmd = cmd.current_dir(&root);
    let (mut _rx, child) = cmd.spawn().map_err(|e| format!("Python L3 启动失败: {}", e))?;
    Ok(child)
}

/// 静默启动 l3_node：优先 Sidecar，失败则回退到 python -m l3_node
/// 若有 l2_gateway_config 则用 --gateway 模式，否则 --ws-only
pub fn spawn_l3_node(app: &impl tauri::Manager<tauri::Wry>) -> Result<tauri_plugin_shell::process::CommandChild, String> {
    let (args, env_url, mode) = if let Some(url) = should_use_gateway_mode() {
        (["--gateway"].as_slice(), Some(url), "gateway")
    } else {
        (["--ws-only"].as_slice(), None, "ws-only")
    };

    let child = match app.shell().sidecar("bin/l3_node") {
        Ok(sidecar) => {
            let sidecar = sidecar.args(args);
            let sidecar = if let Some(ref url) = env_url {
                sidecar.env("L2_BASE_URL", url.as_str())
            } else {
                sidecar
            };
            match sidecar.spawn() {
                Ok((mut _rx, c)) => c,
                Err(e) => {
                    let err_msg = e.to_string();
                    if err_msg.contains("找不到") || err_msg.contains("path") || err_msg.contains("os error 3") {
                        println!("[L3] Sidecar 不可用，回退到 python -m l3_node");
                        spawn_l3_via_python(app, args, env_url.as_deref())?
                    } else {
                        return Err(format!("L3 启动失败: {}", err_msg));
                    }
                }
            }
        }
        Err(_) => {
            println!("[L3] Sidecar 未找到，使用 python -m l3_node");
            spawn_l3_via_python(app, args, env_url.as_deref())?
        }
    };

    println!("[L3] 引擎已启动 ws://127.0.0.1:18881 (mode={})", mode);
    Ok(child)
}
