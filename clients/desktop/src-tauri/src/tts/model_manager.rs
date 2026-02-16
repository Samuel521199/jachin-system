//! ModelManager - 从 Tier 2 下载 Kokoro 模型
//!
//! 路径解析采用 "Silent Intelligence"：零配置，自动检测最佳存储位置。
//! 优先级：Portable（可执行文件旁）> Standard（OS 数据目录）> 环境变量（开发调试）

use std::env;
use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};

static DOWNLOADED: AtomicBool = AtomicBool::new(false);

/// 默认 Tier 2 URL（可通过环境变量覆盖）
pub const DEFAULT_TIER2_URL: &str = "http://localhost:18888";

/// 模型文件信息
pub const KOKORO_MODEL_FILENAME: &str = "kokoro-v0_19.onnx";
pub const VOICES_FILENAME: &str = "voices.json";

/// 便携模式目录名（可执行文件旁）
const PORTABLE_DATA_DIR: &str = "_portable_data";
const PORTABLE_MODELS_DIR: &str = "models";

/// 开发调试用环境变量（用户无需配置）
const DEBUG_PATH_ENV: &str = "JACHIN_TTS_DEBUG_PATH";

/// 下载进度回调：(已下载字节, 总字节)，需 Send+Sync 以在 async 中跨 await 使用
pub type ProgressCallback = Box<dyn Fn(u64, u64) + Send + Sync>;

/// 检查目录是否存在且可写
fn is_writable(path: &Path) -> bool {
    if !path.exists() || !path.is_dir() {
        return false;
    }
    let test_file = path.join(".tts_write_test");
    fs::File::create(&test_file)
        .and_then(|mut f| f.write_all(b"ok"))
        .and_then(|_| fs::remove_file(&test_file))
        .is_ok()
}

/// 解析 TTS 模型存储路径（零配置，自动检测）
///
/// 优先级：
/// 1. **Portable Mode**: 可执行文件旁的 `_portable_data/tts` 或 `models/tts`，存在且可写则使用
/// 2. **Standard Mode**: `data_local_dir()/tts`（如 Windows: `%LOCALAPPDATA%\jachin\desktop\data\tts`）
/// 3. **Dev Override**: 环境变量 `JACHIN_TTS_DEBUG_PATH`（仅开发调试）
///
/// 若 Standard 路径不存在会尝试静默创建；创建失败（如磁盘满）仅记录日志，不崩溃，
/// 实际错误在 TTS 请求时再抛出。
pub fn resolve_model_path() -> PathBuf {
    // Check 3: 开发调试环境变量
    if let Ok(debug_path) = env::var(DEBUG_PATH_ENV) {
        let p = PathBuf::from(debug_path.trim()).join("tts");
        if let Err(e) = fs::create_dir_all(&p) {
            eprintln!("[TTS] {}: create_dir_all failed: {}", DEBUG_PATH_ENV, e);
        }
        return p;
    }

    // Check 1: Portable Mode
    if let Ok(exe_path) = env::current_exe() {
        if let Some(exe_dir) = exe_path.parent() {
            // _portable_data/tts — power users create _portable_data, we use tts subdir
            let portable_data = exe_dir.join(PORTABLE_DATA_DIR);
            if portable_data.exists() && is_writable(&portable_data) {
                return portable_data.join("tts");
            }
            // models — use directly (kokoro-v0_19.onnx goes in models/)
            let models_dir = exe_dir.join(PORTABLE_MODELS_DIR);
            if models_dir.exists() && is_writable(&models_dir) {
                return models_dir;
            }
        }
    }

    // Check 2: Standard Mode
    // data_local_dir() = e.g. C:\Users\%USERNAME%\AppData\Local\jachin\desktop\data
    let standard = directories::ProjectDirs::from("com", "jachin", "desktop")
        .map(|d| d.data_local_dir().join("tts"))
        .unwrap_or_else(|| PathBuf::from(".data/tts"));

    if let Err(e) = fs::create_dir_all(&standard) {
        eprintln!("[TTS] Failed to create model dir {:?}: {} (will retry on first use)", standard, e);
    }
    standard
}

/// ModelManager - 管理 Kokoro 模型下载
pub struct ModelManager {
    /// 模型存储目录（由 resolve_model_path 或显式传入）
    data_dir: PathBuf,
    tier2_base_url: String,
}

impl ModelManager {
    /// 使用 app_data_dir 创建 ModelManager
    /// data_dir: 若为 None，则调用 resolve_model_path() 自动解析；若为 Some，则视为已含 tts 子路径
    pub fn new(tier2_base_url: impl Into<String>, data_dir: Option<PathBuf>) -> Self {
        let data_dir = data_dir.unwrap_or_else(resolve_model_path);

        Self {
            data_dir,
            tier2_base_url: tier2_base_url.into(),
        }
    }

    /// 获取模型所在目录
    pub fn data_dir(&self) -> &PathBuf {
        &self.data_dir
    }

    /// 检查 kokoro-v0_19.onnx 是否存在
    pub fn has_model(&self) -> bool {
        self.data_dir.join(KOKORO_MODEL_FILENAME).exists()
    }

    /// 获取模型路径，若不存在则下载
    /// on_progress: 可选进度回调 (downloaded_bytes, total_bytes)，total=0 表示未知
    pub async fn ensure_model(
        &self,
        on_progress: Option<ProgressCallback>,
    ) -> Result<(PathBuf, PathBuf), String> {
        let model_path = self.data_dir.join(KOKORO_MODEL_FILENAME);
        let voices_path = self.data_dir.join(VOICES_FILENAME);

        if model_path.exists() && voices_path.exists() {
            return Ok((model_path, voices_path));
        }

        std::fs::create_dir_all(&self.data_dir).map_err(|e| e.to_string())?;

        if !model_path.exists() {
            self.download_file(
                KOKORO_MODEL_FILENAME,
                &model_path,
                on_progress.as_ref(),
            )
            .await?;
        }

        if !voices_path.exists() {
            self.download_file(VOICES_FILENAME, &voices_path, None)
                .await?;
        }

        DOWNLOADED.store(true, Ordering::Relaxed);
        Ok((model_path, voices_path))
    }

    async fn download_file(
        &self,
        filename: &str,
        dest_path: &PathBuf,
        on_progress: Option<&ProgressCallback>,
    ) -> Result<(), String> {
        let base = self.tier2_base_url.trim_end_matches('/');
        let url = format!("{}/api/v2/tts/models/{}", base, filename);

        let client = reqwest::Client::new();
        let res = client
            .get(&url)
            .send()
            .await
            .map_err(|e| e.to_string())?;

        if !res.status().is_success() {
            return Err(format!("Download failed {}: {}", url, res.status()));
        }

        let total = res.content_length().unwrap_or(0);
        let mut stream = res.bytes_stream();
        let mut downloaded: u64 = 0;
        let mut buf = Vec::with_capacity(total as usize);

        use futures_util::StreamExt;
        while let Some(chunk) = stream.next().await {
            let chunk = chunk.map_err(|e| e.to_string())?;
            let len = chunk.len() as u64;
            downloaded += len;
            buf.extend_from_slice(&chunk);
            if let Some(ref cb) = on_progress {
                cb(downloaded, if total > 0 { total } else { downloaded });
            }
        }

        std::fs::write(dest_path, &buf).map_err(|e| e.to_string())?;
        Ok(())
    }

    /// 模型是否已下载
    pub fn is_downloaded(&self) -> bool {
        DOWNLOADED.load(Ordering::Relaxed)
            || (self.data_dir.join(KOKORO_MODEL_FILENAME).exists()
                && self.data_dir.join(VOICES_FILENAME).exists())
    }
}
