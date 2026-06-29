//! ModelManager - 检查本地 MOSS ONNX 模型目录
//!
//! 路径解析采用 "Silent Intelligence"：零配置，自动检测最佳存储位置。
//! 优先级：Portable（可执行文件旁）> Standard（OS 数据目录）> 环境变量（开发调试）

use std::env;
use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};

static DOWNLOADED: AtomicBool = AtomicBool::new(false);

/// 默认本地 voice_server URL（可通过环境变量覆盖）
pub const DEFAULT_VOICE_SERVER_URL: &str = "http://127.0.0.1:18982";

/// MOSS 模型目录信息
pub const MOSS_TTS_DIRNAME: &str = "MOSS-TTS-Nano-100M-ONNX";
pub const MOSS_CODEC_DIRNAME: &str = "MOSS-Audio-Tokenizer-Nano-ONNX";
pub const MOSS_MANIFEST_FILENAME: &str = "browser_poc_manifest.json";

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

/// ModelManager - 管理 MOSS 模型目录状态
pub struct ModelManager {
    /// 模型存储目录（由 resolve_model_path 或显式传入）
    data_dir: PathBuf,
    voice_server_base_url: String,
}

impl ModelManager {
    /// 使用 app_data_dir 创建 ModelManager
    /// data_dir: 若为 None，则调用 resolve_model_path() 自动解析；若为 Some，则视为已含 tts 子路径
    pub fn new(voice_server_base_url: impl Into<String>, data_dir: Option<PathBuf>) -> Self {
        let data_dir = data_dir.unwrap_or_else(resolve_model_path);

        Self {
            data_dir,
            voice_server_base_url: voice_server_base_url.into(),
        }
    }

    /// 获取模型所在目录
    pub fn data_dir(&self) -> &PathBuf {
        &self.data_dir
    }

    fn tts_dir(&self) -> PathBuf {
        self.data_dir.join(MOSS_TTS_DIRNAME)
    }

    fn codec_dir(&self) -> PathBuf {
        self.data_dir.join(MOSS_CODEC_DIRNAME)
    }

    /// 检查 MOSS 模型目录是否存在
    pub fn has_model(&self) -> bool {
        self.tts_dir().join(MOSS_MANIFEST_FILENAME).exists() && self.codec_dir().is_dir()
    }

    /// 获取模型目录；若不存在则返回明确错误（MOSS 模型由部署阶段准备，不再运行时下载）
    /// on_progress: 保持兼容，立即上报完成状态
    pub async fn ensure_model(
        &self,
        on_progress: Option<ProgressCallback>,
    ) -> Result<(PathBuf, PathBuf), String> {
        let tts_path = self.tts_dir();
        let codec_path = self.codec_dir();

        if self.has_model() {
            if let Some(cb) = on_progress {
                cb(1, 1);
            }
            DOWNLOADED.store(true, Ordering::Relaxed);
            return Ok((tts_path, codec_path));
        }

        let base = self.voice_server_base_url.trim_end_matches('/');
        Err(format!(
            "MOSS ONNX models not found. expected: {:?} and {:?}. \
             Please place model folders under data/models/voice/tts, then retry. \
             Local voice_server endpoint: {}",
            tts_path, codec_path, base
        ))
    }

    /// 模型是否已下载
    pub fn is_downloaded(&self) -> bool {
        DOWNLOADED.load(Ordering::Relaxed)
            || self.has_model()
    }
}
