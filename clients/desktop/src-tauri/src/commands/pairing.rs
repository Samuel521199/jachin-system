//! V2 L3-L2 零信任配对 - 网关接驳
//! 读写 ~/.jachin/l2_gateway_config.json，管理 L2 网关地址与配对状态

use serde::{Deserialize, Serialize};
use std::fs;
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
pub struct GatewayConfig {
    pub l2_base_url: Option<String>,
    pub node_id: Option<String>,
    pub paired: Option<bool>,
}

/// 检查是否已完成 L2 网关配对或使用本地模式
#[tauri::command]
pub fn is_gateway_paired() -> Result<bool, String> {
    let path = gateway_config_path()?;
    if !path.exists() {
        return Ok(false);
    }
    let content = fs::read_to_string(&path).map_err(|e| e.to_string())?;
    let cfg: serde_json::Value = serde_json::from_str(&content).unwrap_or(serde_json::json!({}));
    if cfg.get("use_local").and_then(|v| v.as_bool()).unwrap_or(false) {
        return Ok(true);
    }
    let paired = cfg.get("paired").and_then(|v| v.as_bool()).unwrap_or(false);
    let has_node = cfg.get("node_id").and_then(|v| v.as_str()).map(|s| !s.is_empty()).unwrap_or(false);
    Ok(paired && has_node)
}

/// 读取保存的 L2 网关地址
#[tauri::command]
pub fn read_l2_gateway_url() -> Result<String, String> {
    let path = gateway_config_path()?;
    if !path.exists() {
        return Ok(DEFAULT_L2_URL.to_string());
    }
    let content = fs::read_to_string(&path).map_err(|e| e.to_string())?;
    let cfg: serde_json::Value = serde_json::from_str(&content).unwrap_or(serde_json::json!({}));
    Ok(cfg
        .get("l2_base_url")
        .and_then(|v| v.as_str())
        .unwrap_or(DEFAULT_L2_URL)
        .to_string())
}

/// 保存 L2 网关地址
#[tauri::command]
pub fn write_l2_gateway_url(url: String) -> Result<(), String> {
    let path = gateway_config_path()?;
    let parent = path.parent().ok_or("Invalid path")?;
    fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    let url = url.trim().trim_end_matches('/').to_string();
    let mut cfg: GatewayConfig = if path.exists() {
        let content = fs::read_to_string(&path).map_err(|e| e.to_string())?;
        serde_json::from_str(&content).unwrap_or(GatewayConfig {
            l2_base_url: None,
            node_id: None,
            paired: None,
        })
    } else {
        GatewayConfig {
            l2_base_url: None,
            node_id: None,
            paired: None,
        }
    };
    cfg.l2_base_url = Some(url.clone());
    fs::write(
        &path,
        serde_json::to_string_pretty(&cfg).map_err(|e| e.to_string())?,
    )
    .map_err(|e| e.to_string())?;
    Ok(())
}

/// 发起网关接驳：重启 L3 为 --gateway 模式（支持 Sidecar 或 Python 回退）
#[tauri::command]
pub async fn gateway_connect(app: tauri::AppHandle, l2_url: String) -> Result<(), String> {
    let url = l2_url.trim().trim_end_matches('/');
    write_l2_gateway_url(url.to_string())?;

    let child = match app.shell().sidecar("bin/l3_node") {
        Ok(sidecar) => {
            let sidecar = sidecar.args(["--gateway"]).env("L2_BASE_URL", url);
            match sidecar.spawn() {
                Ok((_rx, c)) => c,
                Err(e) => {
                    let err_msg = e.to_string();
                    if err_msg.contains("找不到") || err_msg.contains("path") || err_msg.contains("os error 3") {
                        println!("[L3] Sidecar 不可用，回退到 python -m l3_node");
                        crate::l3_spawn::spawn_l3_via_python(
                            &app,
                            &["--gateway"],
                            Some(url),
                        )?
                    } else {
                        return Err(format!("L3 启动失败: {}。请运行: python scripts/build_l3_sidecar.py", err_msg));
                    }
                }
            }
        }
        Err(_) => {
            println!("[L3] Sidecar 未找到，使用 python -m l3_node");
            crate::l3_spawn::spawn_l3_via_python(&app, &["--gateway"], Some(url))?
        }
    };

    if let Some(handle) = app.try_state::<crate::l3_spawn::L3Handle>() {
        handle.replace(child);
    } else {
        app.manage(crate::l3_spawn::L3Handle::new(child));
    }
    println!("[L3] 已以 --gateway 模式启动，等待 L2 审批");
    Ok(())
}

/// 标记使用本地模式（跳过 L2），下次启动直接进入主界面
#[tauri::command]
pub fn set_use_local_mode() -> Result<(), String> {
    let path = gateway_config_path()?;
    let parent = path.parent().ok_or("Invalid path")?;
    fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    let cfg = serde_json::json!({ "use_local": true });
    fs::write(&path, serde_json::to_string_pretty(&cfg).unwrap()).map_err(|e| e.to_string())?;
    Ok(())
}

/// 检查 L3 引擎是否就绪（WebSocket 端口 18881 已监听）
#[tauri::command]
pub fn is_l3_engine_ready() -> Result<bool, String> {
    use std::net::TcpStream;
    TcpStream::connect("127.0.0.1:18881")
        .map(|_| true)
        .or_else(|_| Ok(false))
}
