//! UserSettings - 持久化用户配置
//!
//! 允许用户在自动生成的策略之上手动覆盖（如强制本地 LLM、指定模型路径等）

use serde::{Deserialize, Serialize};
use std::env;
use std::fs;
use std::path::PathBuf;

const SETTINGS_FILENAME: &str = "settings.json";
const PORTABLE_DATA_DIR: &str = "_portable_data";
const DEBUG_PATH_ENV: &str = "JACHIN_TTS_DEBUG_PATH";

/// 用户设置（持久化到 settings.json）
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct UserSettings {
    /// LLM 提供者覆盖：Some("local") | Some("cloud") | None = Auto
    pub llm_provider_override: Option<String>,

    /// STT 提供者覆盖
    pub stt_provider_override: Option<String>,

    /// TTS 提供者覆盖
    pub tts_provider_override: Option<String>,

    /// 运行模式覆盖：e.g. "standalone", "client"
    pub run_mode_override: Option<String>,

    /// 自定义模型路径（额外模型文件夹）
    pub custom_model_path: Option<String>,
}

/// 获取应用数据根目录（与 TTS 路径策略一致）
fn get_root() -> PathBuf {
    if let Ok(debug_path) = env::var(DEBUG_PATH_ENV) {
        return PathBuf::from(debug_path.trim());
    }
    if let Ok(exe_path) = env::current_exe() {
        if let Some(exe_dir) = exe_path.parent() {
            let portable_data = exe_dir.join(PORTABLE_DATA_DIR);
            if portable_data.exists() {
                return portable_data;
            }
        }
    }
    directories::ProjectDirs::from("com", "jachin", "desktop")
        .map(|d| d.data_local_dir().to_path_buf())
        .unwrap_or_else(|| PathBuf::from(".data"))
}

impl UserSettings {
    /// 从 `{root}/settings.json` 加载。若文件不存在，返回 `Default`（全为 None）
    pub fn load() -> Self {
        let path = get_root().join(SETTINGS_FILENAME);
        match fs::read_to_string(&path) {
            Ok(s) => serde_json::from_str(&s).unwrap_or_default(),
            Err(_) => Self::default(),
        }
    }

    /// 将当前配置写入 `{root}/settings.json`
    #[allow(dead_code)]
    pub fn save(&self) -> Result<(), String> {
        let root = get_root();
        fs::create_dir_all(&root).map_err(|e| e.to_string())?;
        let path = root.join(SETTINGS_FILENAME);
        let s = serde_json::to_string_pretty(self).map_err(|e| e.to_string())?;
        fs::write(&path, s).map_err(|e| e.to_string())
    }
}
