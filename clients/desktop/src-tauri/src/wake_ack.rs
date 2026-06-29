//! 唤醒口头确认（Verbal ACK）预渲染 WAV 解析与选取。

use crate::config::UserSettings;
use std::env;
use std::path::PathBuf;

const PORTABLE_DATA_DIR: &str = "_portable_data";
const DEFAULT_POOL: [&str; 3] = ["im_here", "yes", "how_can_i_help"];

fn data_root() -> PathBuf {
    if let Ok(exe_path) = env::current_exe() {
        if let Some(exe_dir) = exe_path.parent() {
            let portable = exe_dir.join(PORTABLE_DATA_DIR);
            if portable.exists() {
                return portable;
            }
        }
    }
    directories::ProjectDirs::from("com", "jachin", "desktop")
        .map(|d| d.data_local_dir().to_path_buf())
        .unwrap_or_else(|| PathBuf::from(".data"))
}

fn wake_ack_search_dirs() -> Vec<PathBuf> {
    let mut dirs = Vec::new();
    if let Ok(p) = env::var("JACHIN_WAKE_ACK_DEBUG_PATH") {
        dirs.push(PathBuf::from(p.trim()));
    }
    if let Ok(exe_path) = env::current_exe() {
        if let Some(exe_dir) = exe_path.parent() {
            dirs.push(exe_dir.join(PORTABLE_DATA_DIR).join("audio").join("wake_ack"));
        }
    }
    dirs.push(data_root().join("audio").join("wake_ack"));
    dirs
}

fn resolve_wav_path(id: &str) -> Option<PathBuf> {
    let id = id.trim();
    if id.is_empty() {
        return None;
    }
    let file = format!("{}.wav", id);
    for dir in wake_ack_search_dirs() {
        let p = dir.join(&file);
        if p.is_file() {
            return Some(p);
        }
    }
    None
}

fn custom_phrase_path(phrase: &str) -> PathBuf {
    use sha2::{Digest, Sha256};
    let mut hasher = Sha256::new();
    hasher.update(phrase.trim().as_bytes());
    let hash = format!("{:x}", hasher.finalize());
    let short = hash.chars().take(8).collect::<String>();
    data_root()
        .join("audio")
        .join("wake_ack")
        .join(format!("custom_{}.wav", short))
}

pub fn should_play_verbal_ack(settings: &UserSettings) -> bool {
    let mode = settings
        .wake_ack_mode
        .as_deref()
        .unwrap_or("both")
        .trim()
        .to_lowercase();
    mode == "verbal" || mode == "both"
}

pub fn pick_wake_ack_bytes(settings: &UserSettings) -> Option<Vec<u8>> {
    if !should_play_verbal_ack(settings) {
        return None;
    }

    if let Some(phrase) = settings
        .wake_ack_phrase
        .as_ref()
        .map(|s| s.trim())
        .filter(|s| !s.is_empty())
    {
        let custom = custom_phrase_path(phrase);
        if custom.is_file() {
            return std::fs::read(&custom).ok();
        }
        // 无缓存时跳过（避免运行时调 JVS 拖慢唤醒；用户可运行 gen_wake_ack_wavs.py）
        return None;
    }

    let pool: Vec<String> = settings
        .wake_ack_pool
        .clone()
        .unwrap_or_else(|| DEFAULT_POOL.iter().map(|s| s.to_string()).collect());

    if pool.is_empty() {
        return None;
    }

    let idx = (std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_nanos() as usize)
        .unwrap_or(0))
        % pool.len();

    for offset in 0..pool.len() {
        let id = &pool[(idx + offset) % pool.len()];
        if let Some(path) = resolve_wav_path(id) {
            return std::fs::read(path).ok();
        }
    }
    None
}

pub fn resolve_preview_path(id: &str) -> Option<PathBuf> {
    resolve_wav_path(id)
}

pub fn list_preset_ids() -> Vec<String> {
    DEFAULT_POOL.iter().map(|s| s.to_string()).collect()
}

pub fn ensure_wake_ack_dir() -> PathBuf {
    let dir = data_root().join("audio").join("wake_ack");
    let _ = std::fs::create_dir_all(&dir);
    dir
}
