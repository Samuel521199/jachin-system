//! 读取 `~/.jachin/nexus_config.json`（与 L2 配对后写入的 access_token / nexus_base_url）。
//! 热更新 Bearer 优先 `desktop_update_token`（与 L1 `DESKTOP_UPDATE_BEARER` 一致），否则用 `access_token`（edge 用户凭证）。
//! 热更新端点 URL 须在 `tauri.conf.json` 的 `plugins.updater.endpoints` 与 `nexus_base_url` 主机一致。
//!
//! 首次启动：若 `nexus_config.json` 不存在，则从打包资源 `nexus_config.example.json`（或编译期嵌入的同文件）写入，与手动复制示例一致。

use serde_json::Value;
use std::fs;
use std::path::PathBuf;
use tauri::path::BaseDirectory;
use tauri::Manager;

fn jachin_dir() -> PathBuf {
    if cfg!(target_os = "windows") {
        std::env::var("USERPROFILE")
            .map(PathBuf::from)
            .unwrap_or_default()
            .join(".jachin")
    } else {
        std::env::var("HOME")
            .map(PathBuf::from)
            .unwrap_or_default()
            .join(".jachin")
    }
}

fn read_nexus_config_value() -> Value {
    let p = jachin_dir().join("nexus_config.json");
    if !p.exists() {
        return Value::Null;
    }
    let raw = fs::read_to_string(&p).unwrap_or_default();
    // Windows 记事本等会以 UTF-8 BOM 保存；serde_json 不接受文件首字节为 BOM，会整份解析失败。
    let raw = raw.trim_start_matches('\u{feff}').trim_start();
    serde_json::from_str(raw).unwrap_or(Value::Null)
}

/// L1 基址（无尾部斜杠），用于文档与调试；updater 端点以 `tauri.conf.json` 为准。
#[allow(dead_code)]
pub fn nexus_base_url() -> Option<String> {
    let v = read_nexus_config_value();
    v.get("nexus_base_url")
        .and_then(|x| x.as_str())
        .map(|s| s.trim().trim_end_matches('/').to_string())
        .filter(|s| !s.is_empty())
}

#[allow(dead_code)]
pub fn access_token() -> Option<String> {
    let v = read_nexus_config_value();
    v.get("access_token")
        .and_then(|x| x.as_str())
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
}

/// 供 Tauri updater 请求头使用：优先专用令牌，避免与普通 L2 access_token（非 edge_agents）混用。
pub fn updater_bearer_token() -> Option<String> {
    let v = read_nexus_config_value();
    let from_key = |k: &str| {
        v.get(k)
            .and_then(|x| x.as_str())
            .map(|s| s.trim().to_string())
            .filter(|s| !s.is_empty())
    };
    from_key("desktop_update_token").or_else(|| from_key("access_token"))
}

/// 调试摘要：路径、是否存在、各字段长度（不记录 token 明文）。
pub fn updater_debug_summary() -> (PathBuf, bool, String) {
    let path = jachin_dir().join("nexus_config.json");
    let exists = path.is_file();
    let v = read_nexus_config_value();
    let mut parts: Vec<String> = vec![];
    if let Some(s) = v
        .get("desktop_update_token")
        .and_then(|x| x.as_str())
        .map(|s| s.trim())
        .filter(|s| !s.is_empty())
    {
        parts.push(format!("desktop_update_token_len={}", s.len()));
    }
    if let Some(s) = v
        .get("access_token")
        .and_then(|x| x.as_str())
        .map(|s| s.trim())
        .filter(|s| !s.is_empty())
    {
        parts.push(format!("access_token_len={}", s.len()));
    }
    if let Some(url) = nexus_base_url() {
        parts.push(format!("nexus_base_url={}", url));
    }
    let summary = if parts.is_empty() {
        "no_token_fields".into()
    } else {
        parts.join(" ")
    };
    (path, exists, summary)
}

/// 若 `~/.jachin/nexus_config.json` 尚不存在，则从 `nexus_config.example.json` 创建（不覆盖已有文件）。
pub fn ensure_default_nexus_config_from_example(app: &tauri::AppHandle) {
    let dest = jachin_dir().join("nexus_config.json");
    if dest.is_file() {
        return;
    }

    let mut content: Option<String> = None;
    if let Ok(p) = app
        .path()
        .resolve("nexus_config.example.json", BaseDirectory::Resource)
    {
        if p.is_file() {
            content = fs::read_to_string(&p).ok();
        }
    }
    if content.is_none() {
        content = Some(
            include_str!(concat!(
                env!("CARGO_MANIFEST_DIR"),
                "/../nexus_config.example.json"
            ))
            .to_string(),
        );
    }

    let Some(raw) = content else {
        return;
    };

    if let Some(parent) = dest.parent() {
        if let Err(e) = fs::create_dir_all(parent) {
            eprintln!("[nexus_config] 无法创建目录 {}: {}", parent.display(), e);
            return;
        }
    }
    match fs::write(&dest, raw) {
        Ok(()) => eprintln!(
            "[nexus_config] 已写入默认配置 {}",
            dest.display()
        ),
        Err(e) => eprintln!(
            "[nexus_config] 写入 {} 失败: {}",
            dest.display(),
            e
        ),
    }
}
