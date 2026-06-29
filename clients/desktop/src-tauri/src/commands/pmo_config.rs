//! PMO Copilot 本地 YAML 配置（~/.jachin/config/skills/pmo-copilot/pmo_bitable_watch.yaml）

use serde::{Deserialize, Serialize};
use std::fs;
use std::path::PathBuf;

const SKILL_ID: &str = "pmo-copilot";
const CONFIG_NAME: &str = "pmo_bitable_watch.yaml";

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
    Ok(jachin_home()?.join("config").join("skills").join(SKILL_ID).join(CONFIG_NAME))
}

fn bundled_template_paths() -> Vec<PathBuf> {
    let mut out = Vec::new();
    if let Some(dir) = crate::l3_spawn::exe_dir() {
        out.push(
            dir.join("config")
                .join("skills")
                .join(SKILL_ID)
                .join(CONFIG_NAME),
        );
    }
    if let Some(root) = crate::l3_spawn::project_root() {
        out.push(
            root.join("config")
                .join("skills")
                .join(SKILL_ID)
                .join(CONFIG_NAME),
        );
    }
    out
}

fn ensure_user_config() -> Result<PathBuf, String> {
    let dst = user_config_path()?;
    if dst.is_file() {
        return Ok(dst);
    }
    if let Some(parent) = dst.parent() {
        fs::create_dir_all(parent).map_err(|e| format!("创建配置目录失败: {e}"))?;
    }
    for src in bundled_template_paths() {
        if src.is_file() {
            fs::copy(&src, &dst).map_err(|e| format!("复制 PMO 配置模板失败: {e}"))?;
            return Ok(dst);
        }
    }
    let default_yaml = r#"# PMO 多维表变更监控（由桌面端自动创建）
enabled: false
mode: webhook
table_id: tblfK9gk6vTQpJtB
view_id: vewpI8lyYw
wiki_url: "https://ssgkm409t6q5.sg.larksuite.com/wiki/B19Iww8tBiXZqfky1hhlIZ6kg0P?table=tblfK9gk6vTQpJtB&view=vewpI8lyYw"
chat_id: "${PMO_CHANGE_ALERT_CHAT_ID}"
monitor_chat_id: "${PMO_CHANGE_ALERT_MONITOR_CHAT_ID}"
app_id: "${PMO_BITABLE_WATCH_APP_ID}"
app_secret: "${PMO_BITABLE_WATCH_APP_SECRET}"
idle_seconds: 20
debounce_check_seconds: 5
poll_interval_seconds: 15
max_records: 5000
push_change_summary: false
run_change_alert: false
persist_local: true
"#;
    fs::write(&dst, default_yaml).map_err(|e| format!("写入默认 PMO 配置失败: {e}"))?;
    Ok(dst)
}

#[derive(Debug, Serialize, Deserialize)]
pub struct PmoSkillConfigInfo {
    pub path: String,
    pub yaml: String,
    pub exists: bool,
    pub seeded: bool,
}

/// 读取 PMO 技能 YAML；若用户目录不存在则从安装包/仓库模板复制。
#[tauri::command]
pub fn read_pmo_skill_config() -> Result<PmoSkillConfigInfo, String> {
    let before_exists = user_config_path()?.is_file();
    let path = ensure_user_config()?;
    let yaml = fs::read_to_string(&path).map_err(|e| format!("读取 PMO 配置失败: {e}"))?;
    Ok(PmoSkillConfigInfo {
        path: path.to_string_lossy().into_owned(),
        yaml,
        exists: true,
        seeded: !before_exists,
    })
}

/// 保存 PMO 技能 YAML 到 ~/.jachin/config/skills/pmo-copilot/
#[tauri::command]
pub fn write_pmo_skill_config(yaml: String) -> Result<PmoSkillConfigInfo, String> {
    let path = ensure_user_config()?;
    fs::write(&path, yaml.as_bytes()).map_err(|e| format!("保存 PMO 配置失败: {e}"))?;
    Ok(PmoSkillConfigInfo {
        path: path.to_string_lossy().into_owned(),
        yaml,
        exists: true,
        seeded: false,
    })
}

/// 在资源管理器中打开 PMO 配置目录（Windows: explorer；macOS: open；Linux: xdg-open）
#[tauri::command]
pub fn open_pmo_skill_config_dir() -> Result<String, String> {
    let path = ensure_user_config()?;
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
