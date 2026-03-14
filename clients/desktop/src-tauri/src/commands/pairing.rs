//! V2 L3-L2 零信任配对 - 网关接驳
//! 读写 ~/.jachin/l2_gateway_config.json，管理 L2 网关地址与配对状态

use serde::{Deserialize, Serialize};
use std::fs;
use std::io::Write;
use std::path::PathBuf;
use tauri::Manager;
use tauri_plugin_shell::ShellExt;

const DEFAULT_L2_URL: &str = "http://localhost:18888";

fn jachin_dir() -> Result<PathBuf, String> {
    let home = if cfg!(target_os = "windows") {
        std::env::var("USERPROFILE").map_err(|_| "USERPROFILE not set")?
    } else {
        std::env::var("HOME").map_err(|_| "HOME not set")?
    };
    Ok(PathBuf::from(home).join(".jachin"))
}

fn gateway_config_path() -> Result<PathBuf, String> {
    Ok(jachin_dir()?.join("l2_gateway_config.json"))
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default)]
pub struct GatewayConfig {
    pub l2_base_url: Option<String>,
    pub node_id: Option<String>,
    pub paired: Option<bool>,
    pub display_name: Option<String>,
    #[serde(alias = "use_local")]
    pub use_local: Option<bool>,
}

impl Default for GatewayConfig {
    fn default() -> Self {
        Self {
            l2_base_url: None,
            node_id: None,
            paired: None,
            display_name: None,
            use_local: None,
        }
    }
}

fn read_gateway_config(path: &std::path::Path) -> GatewayConfig {
    if !path.exists() {
        return GatewayConfig::default();
    }
    let content = fs::read_to_string(path).unwrap_or_default();
    serde_json::from_str(&content).unwrap_or_default()
}

/// 检查是否已完成 L2 网关配对或使用本地模式
#[tauri::command]
pub fn is_gateway_paired() -> Result<bool, String> {
    let path = gateway_config_path()?;
    let cfg = read_gateway_config(&path);
    if cfg.use_local.unwrap_or(false) {
        return Ok(true);
    }
    let paired = cfg.paired.unwrap_or(false);
    let has_node = cfg
        .node_id
        .as_ref()
        .map(|s| !s.is_empty())
        .unwrap_or(false);
    Ok(paired && has_node)
}

/// 读取 L2 网关配置（含 sub_account_id，供 L2 API 鉴权）
#[tauri::command]
pub fn read_l2_gateway_config() -> Result<serde_json::Value, String> {
    let path = gateway_config_path()?;
    if !path.exists() {
        return Ok(serde_json::json!({}));
    }
    let content = fs::read_to_string(&path).map_err(|e| e.to_string())?;
    Ok(serde_json::from_str(&content).unwrap_or(serde_json::json!({})))
}

/// 读取保存的 L2 网关地址
#[tauri::command]
pub fn read_l2_gateway_url() -> Result<String, String> {
    let path = gateway_config_path()?;
    let cfg = read_gateway_config(&path);
    Ok(cfg
        .l2_base_url
        .filter(|s| !s.is_empty())
        .unwrap_or_else(|| DEFAULT_L2_URL.to_string()))
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct WriteGatewayConfigInput {
    pub url: String,
    #[serde(default, alias = "displayName")]
    pub display_name: Option<String>,
}

/// 保存 L2 网关地址及可选设备名（设备名会同步到 L2 审批界面）
#[tauri::command]
pub fn write_l2_gateway_config(input: WriteGatewayConfigInput) -> Result<(), String> {
    let path = gateway_config_path()?;
    let parent = path.parent().ok_or("Invalid path")?;
    fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    let url = input.url.trim().trim_end_matches('/').to_string();
    let mut cfg: serde_json::Value = if path.exists() {
        let content = fs::read_to_string(&path).map_err(|e| e.to_string())?;
        serde_json::from_str(&content).unwrap_or(serde_json::json!({}))
    } else {
        serde_json::json!({})
    };
    cfg["l2_base_url"] = serde_json::json!(url);
    if let Some(name) = input.display_name {
        let trimmed = name.trim();
        if !trimmed.is_empty() {
            cfg["display_name"] = serde_json::json!(trimmed.chars().take(64).collect::<String>());
        }
    }
    fs::write(
        &path,
        serde_json::to_string_pretty(&cfg).map_err(|e| e.to_string())?,
    )
    .map_err(|e| e.to_string())?;
    Ok(())
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GatewayConnectInput {
    #[serde(alias = "l2Url")]
    pub l2_url: String,
    #[serde(default, alias = "displayName")]
    pub display_name: Option<String>,
}

/// 发起网关接驳：重启 L3 为 --gateway 模式（支持 Sidecar 或 Python 回退）
/// display_name 会写入配置并作为 JACHIN_DEVICE_NAME 传给 L3，同步到 L2 审批界面
#[tauri::command]
pub async fn gateway_connect(app: tauri::AppHandle, input: GatewayConnectInput) -> Result<(), String> {
    let url = input.l2_url.trim().trim_end_matches('/');
    let display_name = input.display_name.clone();
    write_l2_gateway_config(WriteGatewayConfigInput {
        url: url.to_string(),
        display_name: display_name.clone(),
    })?;

    let env_root = crate::l3_spawn::exe_dir().unwrap_or_else(PathBuf::new);
    let env_vars = crate::l3_spawn::load_l3_env_vars(&env_root);

    let sidecar = match app.shell().sidecar("bin/l3_node") {
        Ok(s) => s,
        Err(e) => {
            println!("[L3] Sidecar 未找到，尝试直接启动 exe: {}", e);
            let child = crate::l3_spawn::spawn_l3_via_direct_exe(
                &app,
                &["--gateway"],
                Some(url),
                &env_vars,
                display_name.as_deref(),
            )?;
            if let Some(handle) = app.try_state::<std::sync::Arc<crate::l3_spawn::L3Handle>>() {
                handle.replace(child);
            } else {
                let l3 = std::sync::Arc::new(crate::l3_spawn::L3Handle::new(child));
                crate::l3_spawn::register_ctrlc_kill(&l3);
                app.manage(l3);
            }
            println!("[L3] 已以 --gateway 模式启动，等待 L2 审批");
            return Ok(());
        }
    };
    let mut sidecar = sidecar.args(["--gateway"]).env("L2_BASE_URL", url);
    if let Some(ref name) = display_name {
        let trimmed = name.trim();
        if !trimmed.is_empty() {
            sidecar = sidecar.env("JACHIN_DEVICE_NAME", trimmed.chars().take(64).collect::<String>());
        }
    }
    if let Some(ref dir) = crate::l3_spawn::exe_dir() {
        sidecar = sidecar.current_dir(dir);
    }
    for (k, v) in &env_vars {
        sidecar = sidecar.env(k, v);
    }
    let child = match sidecar.spawn() {
        Ok((mut rx, c)) => {
            let child = c;
            tauri::async_runtime::spawn(async move {
                while let Some(event) = rx.recv().await {
                    match event {
                        tauri_plugin_shell::process::CommandEvent::Stdout(ref b) => {
                            let _ = std::io::stdout().write_all(b);
                        }
                        tauri_plugin_shell::process::CommandEvent::Stderr(ref b) => {
                            let _ = std::io::stderr().write_all(b);
                        }
                        _ => {}
                    }
                }
            });
            child
        }
        Err(e) => {
            let err_msg = e.to_string();
            if err_msg.contains("找不到") || err_msg.contains("path") || err_msg.contains("os error 2") || err_msg.contains("os error 3") {
                println!("[L3] Sidecar 不可用，尝试直接启动 exe");
                match crate::l3_spawn::spawn_l3_via_direct_exe(
                    &app,
                    &["--gateway"],
                    Some(url),
                    &env_vars,
                    display_name.as_deref(),
                ) {
                    Ok(c) => c,
                    Err(_direct_err) => {
                        println!("[L3] 直接 exe 失败，回退到 python -m l3_node");
                        crate::l3_spawn::spawn_l3_via_python(
                            &app,
                            &["--gateway"],
                            Some(url),
                            display_name.as_deref(),
                        )?
                    }
                }
            } else {
                return Err(format!("L3 启动失败: {}。请运行: python scripts/build_l3_sidecar.py", err_msg));
            }
        }
    };

    if let Some(handle) = app.try_state::<std::sync::Arc<crate::l3_spawn::L3Handle>>() {
        handle.replace(child);
    } else {
        let l3 = std::sync::Arc::new(crate::l3_spawn::L3Handle::new(child));
        crate::l3_spawn::register_ctrlc_kill(&l3);
        app.manage(l3);
    }
    println!("[L3] 已以 --gateway 模式启动，等待 L2 审批");
    Ok(())
}

/// 标记使用本地模式（跳过 L2 审批界面），下次启动直接进入主界面。
/// 保留 l2_base_url，以便 L3 仍以 --gateway 模式启动并发送心跳，JachinLink 可显示在线。
#[tauri::command]
pub fn set_use_local_mode() -> Result<(), String> {
    let path = gateway_config_path()?;
    let parent = path.parent().ok_or("Invalid path")?;
    fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    let mut cfg: serde_json::Value = if path.exists() {
        let content = fs::read_to_string(&path).map_err(|e| e.to_string())?;
        serde_json::from_str(&content).unwrap_or(serde_json::json!({}))
    } else {
        serde_json::json!({})
    };
    cfg["use_local"] = serde_json::json!(true);
    fs::write(&path, serde_json::to_string_pretty(&cfg).unwrap()).map_err(|e| e.to_string())?;
    Ok(())
}

/// 检查 L3 引擎是否就绪（WebSocket 端口 18981 已监听）
#[tauri::command]
pub fn is_l3_engine_ready() -> Result<bool, String> {
    use std::net::TcpStream;
    TcpStream::connect("127.0.0.1:18981")
        .map(|_| true)
        .or_else(|_| Ok(false))
}
