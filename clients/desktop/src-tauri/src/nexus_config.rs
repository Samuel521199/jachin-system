//! 读取 `~/.jachin/nexus_config.json`（与 L2 配对后写入的 access_token / nexus_base_url）。
//! 热更新 Bearer 优先 `desktop_update_token`（与 L1 `DESKTOP_UPDATE_BEARER` 一致），否则用 `access_token`（edge 用户凭证）。
//! 热更新端点 URL 须在 `tauri.conf.json` 的 `plugins.updater.endpoints` 与 `nexus_base_url` 主机一致。

use serde_json::Value;
use std::fs;
use std::path::PathBuf;

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
    serde_json::from_str(&raw).unwrap_or(Value::Null)
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
