//! 飞书 IM / PMO 长连接 — ~/.jachin/config/im_channels.yaml（打包后可改）

use serde::{Deserialize, Serialize};
use std::fs;
use std::path::PathBuf;

const CONFIG_NAME: &str = "im_channels.yaml";

fn jachin_home() -> Result<PathBuf, String> {
    if let Ok(h) = std::env::var("JACHIN_HOME") {
        let p = PathBuf::from(h.trim());
        if !p.as_os_str().is_empty() {
            return Ok(p);
        }
    }
    let home = if cfg!(target_os = "windows") {
        std::env::var("USERPROFILE").map_err(|_| "USERPROFILE not set".to_string())?
    } else {
        std::env::var("HOME").map_err(|_| "HOME not set".to_string())?
    };
    Ok(PathBuf::from(home).join(".jachin"))
}

fn user_config_path() -> Result<PathBuf, String> {
    Ok(jachin_home()?.join("config").join(CONFIG_NAME))
}

fn bundled_template_paths() -> Vec<PathBuf> {
    let mut out = Vec::new();
    if let Some(dir) = crate::l3_spawn::exe_dir() {
        out.push(dir.join("config").join("im_channels.yaml.example"));
    }
    if let Some(root) = crate::l3_spawn::project_root() {
        out.push(root.join("config").join("im_channels.yaml.example"));
    }
    out
}

fn default_channel(enabled: bool) -> ImChannelEntry {
    ImChannelEntry {
        enabled,
        mode: Some("long_connection".to_string()),
        app_id: String::new(),
        chat_ids: Vec::new(),
        exclusive_sessions: true,
        domain: Some("https://open.feishu.cn".to_string()),
    }
}

fn default_root() -> ImChannelsRoot {
    ImChannelsRoot {
        lark: default_channel(false),
        lark_hr: default_channel(false),
        lark_pmo_bitable: ImBitableChannelEntry {
            enabled: false,
            app_id: String::new(),
            domain: Some("https://open.feishu.cn".to_string()),
        },
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct ImChannelEntry {
    #[serde(default)]
    pub enabled: bool,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub mode: Option<String>,
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub app_id: String,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub chat_ids: Vec<String>,
    /// true = 仅处理 chat_ids 白名单；保存时默认 true（与 L3 should_handle_chat 缺省一致）
    #[serde(default = "default_exclusive_sessions")]
    pub exclusive_sessions: bool,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub domain: Option<String>,
}

fn default_exclusive_sessions() -> bool {
    true
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct ImBitableChannelEntry {
    #[serde(default)]
    pub enabled: bool,
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub app_id: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub domain: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct ImChannelsRoot {
    #[serde(default)]
    pub lark: ImChannelEntry,
    #[serde(default)]
    pub lark_hr: ImChannelEntry,
    #[serde(default)]
    pub lark_pmo_bitable: ImBitableChannelEntry,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct ImChannelsFile {
    #[serde(default)]
    im_channels: ImChannelsRoot,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct ImChannelsUiConfig {
    pub path: String,
    pub exists: bool,
    pub seeded: bool,
    pub lark: ImChannelEntry,
    pub lark_hr: ImChannelEntry,
    pub lark_pmo_bitable: ImBitableChannelEntry,
}

#[derive(Debug, Deserialize)]
pub struct ImChannelsUiPatch {
    pub lark: ImChannelEntry,
    pub lark_hr: ImChannelEntry,
    pub lark_pmo_bitable: ImBitableChannelEntry,
}

fn load_file() -> Result<(PathBuf, ImChannelsFile, bool), String> {
    let path = user_config_path()?;
    let existed = path.is_file();
    if !existed {
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent).map_err(|e| format!("创建配置目录失败: {e}"))?;
        }
        let mut seeded = false;
        for src in bundled_template_paths() {
            if src.is_file() {
                fs::copy(&src, &path).map_err(|e| format!("复制 im_channels 模板失败: {e}"))?;
                seeded = true;
                break;
            }
        }
        if !seeded {
            let file = ImChannelsFile {
                im_channels: default_root(),
            };
            let yaml =
                serde_yaml::to_string(&file).map_err(|e| format!("序列化默认配置失败: {e}"))?;
            fs::write(&path, yaml.as_bytes())
                .map_err(|e| format!("写入默认 im_channels 失败: {e}"))?;
            seeded = true;
        }
        let raw = fs::read_to_string(&path).map_err(|e| format!("读取 im_channels 失败: {e}"))?;
        let parsed: ImChannelsFile =
            serde_yaml::from_str(&raw).unwrap_or_else(|_| ImChannelsFile {
                im_channels: default_root(),
            });
        return Ok((path, parsed, seeded));
    }
    let raw = fs::read_to_string(&path).map_err(|e| format!("读取 im_channels 失败: {e}"))?;
    let mut parsed: ImChannelsFile =
        serde_yaml::from_str(&raw).unwrap_or_else(|_| ImChannelsFile {
            im_channels: default_root(),
        });
    if parsed.im_channels.lark.mode.is_none() {
        parsed.im_channels.lark.mode = Some("long_connection".to_string());
    }
    if parsed.im_channels.lark_hr.mode.is_none() {
        parsed.im_channels.lark_hr.mode = Some("long_connection".to_string());
    }
    Ok((path, parsed, false))
}

fn merge_channel(existing: &ImChannelEntry, patch: &ImChannelEntry) -> ImChannelEntry {
    ImChannelEntry {
        enabled: patch.enabled,
        mode: patch
            .mode
            .clone()
            .or_else(|| existing.mode.clone())
            .or_else(|| Some("long_connection".to_string())),
        app_id: if patch.app_id.is_empty() {
            existing.app_id.clone()
        } else {
            patch.app_id.clone()
        },
        chat_ids: patch.chat_ids.clone(),
        exclusive_sessions: if patch.chat_ids.is_empty() {
            false
        } else {
            patch.exclusive_sessions
        },
        domain: patch
            .domain
            .clone()
            .or_else(|| existing.domain.clone())
            .or_else(|| Some("https://open.feishu.cn".to_string())),
    }
}

fn merge_bitable(
    existing: &ImBitableChannelEntry,
    patch: &ImBitableChannelEntry,
) -> ImBitableChannelEntry {
    ImBitableChannelEntry {
        enabled: patch.enabled,
        app_id: if patch.app_id.is_empty() {
            existing.app_id.clone()
        } else {
            patch.app_id.clone()
        },
        domain: patch
            .domain
            .clone()
            .or_else(|| existing.domain.clone())
            .or_else(|| Some("https://open.feishu.cn".to_string())),
    }
}

/// 读取 im_channels 供设置页展示（不含 app_secret）。
#[tauri::command]
pub fn read_im_channels_config() -> Result<ImChannelsUiConfig, String> {
    let (path, file, seeded) = load_file()?;
    Ok(ImChannelsUiConfig {
        path: path.to_string_lossy().into_owned(),
        exists: true,
        seeded,
        lark: file.im_channels.lark,
        lark_hr: file.im_channels.lark_hr,
        lark_pmo_bitable: file.im_channels.lark_pmo_bitable,
    })
}

/// 保存设置页 patch（enabled / chat_ids / domain）；保留 yaml 内已有 secret 等字段。
#[tauri::command]
pub fn write_im_channels_config(patch: ImChannelsUiPatch) -> Result<ImChannelsUiConfig, String> {
    let (path, mut file, _) = load_file()?;
    file.im_channels.lark = merge_channel(&file.im_channels.lark, &patch.lark);
    file.im_channels.lark_hr = merge_channel(&file.im_channels.lark_hr, &patch.lark_hr);
    file.im_channels.lark_pmo_bitable =
        merge_bitable(&file.im_channels.lark_pmo_bitable, &patch.lark_pmo_bitable);
    let yaml = serde_yaml::to_string(&file).map_err(|e| format!("序列化 im_channels 失败: {e}"))?;
    fs::write(&path, yaml.as_bytes()).map_err(|e| format!("保存 im_channels 失败: {e}"))?;
    read_im_channels_config()
}

#[tauri::command]
pub fn open_im_channels_config_dir() -> Result<String, String> {
    if !user_config_path()?.is_file() {
        let _ = load_file()?;
    }
    let path = user_config_path()?;
    let dir = path
        .parent()
        .ok_or_else(|| "无效配置路径".to_string())?
        .to_path_buf();
    #[cfg(windows)]
    {
        std::process::Command::new("explorer")
            .arg(dir.as_os_str())
            .spawn()
            .map_err(|e| format!("打开目录失败: {e}"))?;
    }
    #[cfg(target_os = "macos")]
    {
        std::process::Command::new("open")
            .arg(&dir)
            .spawn()
            .map_err(|e| format!("打开目录失败: {e}"))?;
    }
    #[cfg(all(not(windows), not(target_os = "macos")))]
    {
        std::process::Command::new("xdg-open")
            .arg(&dir)
            .spawn()
            .map_err(|e| format!("打开目录失败: {e}"))?;
    }
    Ok(dir.to_string_lossy().into_owned())
}
