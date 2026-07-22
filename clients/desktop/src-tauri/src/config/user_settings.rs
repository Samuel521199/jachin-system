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

    /// 聊天流式请求是否直连后端（true=本地流式推荐，false=经 Dapr）。None 视为 true
    pub chat_stream_via_direct: Option<bool>,

    /// 桌面精灵语音模式：push_to_talk | wake_up | continuous。None 视为 push_to_talk
    pub sprite_voice_mode: Option<String>,

    /// 唤醒词/名字（模式 B）：说此词或名字时激活 AI 助手。None 或空时使用默认 "Jachin"
    pub wake_word: Option<String>,

    /// 唤醒确认：`earcon_only` | `verbal` | `both`（默认 both）
    pub wake_ack_mode: Option<String>,

    /// 唤醒口头确认预设池 id 列表（随机播放）
    pub wake_ack_pool: Option<Vec<String>>,

    /// 自定义唤醒确认句（非空时优先于池；须预生成 WAV 缓存）
    pub wake_ack_phrase: Option<String>,

    /// HUD 展示唤醒确认 system 气泡
    pub wake_ack_show_in_hud: Option<bool>,

    /// 声纹门总开关（默认开启；无 owner profile 时会回退为仅记录日志）
    pub speaker_verification_enabled: Option<bool>,

    /// 严格模式：SV 服务/profile 异常时 fail-close（默认 false，避免误杀主流程）
    pub speaker_verification_strict: Option<bool>,

    /// LISTENING 阶段主人轨提取开关（默认开启）
    pub speaker_owner_track_enabled: Option<bool>,

    /// Qwen/通义千问 API Key（桌面端保存，会同步到后端覆盖文件）
    pub qwen_api_key: Option<String>,

    /// 桌面控制台 / Omni 对话窗界面语言：`Some("zh")` | `Some("en")`，None 视为 zh
    pub desktop_ui_lang: Option<String>,

    /// Windows 文件操作高危动作是否免二次确认。None/false = 删除、覆盖、危险移动前需要确认。
    pub os_file_dangerous_without_confirm: Option<bool>,

    /// 飞书/Lark 多维表写入是否免二次确认。None/false = 新增/修改记录前需要确认。
    pub lark_bitable_write_without_confirm: Option<bool>,
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

    /// Always-on voice is a per-session switch. If the last session left it on,
    /// start the next desktop session with the mic closed.
    pub fn reset_continuous_voice_on_startup() -> Result<bool, String> {
        let mut settings = Self::load();
        if settings.sprite_voice_mode.as_deref() != Some("continuous") {
            return Ok(false);
        }
        settings.sprite_voice_mode = Some("push_to_talk".to_string());
        settings.save()?;
        Ok(true)
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
