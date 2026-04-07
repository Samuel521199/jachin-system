//! 读取 `~/.jachin/nexus_config.json`（与 L2 配对后写入的 access_token / nexus_base_url）。
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

pub fn access_token() -> Option<String> {
    let v = read_nexus_config_value();
    v.get("access_token")
        .and_then(|x| x.as_str())
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
}
