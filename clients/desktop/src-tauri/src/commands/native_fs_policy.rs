//! Native 文件系统策略：读写 `~/.jachin/config/native_fs_policy.json`
//! 与 `l3_node/primitives/native_fs_policy_store.py`、L3 HTTP `/api/v3/config/native-fs-policy` 共用同一文件。

use serde::{Deserialize, Serialize};
use std::fs;
use std::path::{Path, PathBuf};

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

fn policy_file_path() -> Result<PathBuf, String> {
    Ok(jachin_home()?.join("config").join("native_fs_policy.json"))
}

#[derive(Debug, Default, Deserialize, Serialize)]
struct PolicyFile {
    version: Option<u32>,
    write_allowlist_extra: Option<Vec<String>>,
    read_blacklist_extra: Option<Vec<String>>,
}

/// 与 `fs_path_blacklist.READ_BLACKLIST_BUILTIN_LINES` 对齐（供设置页在无 L3 时展示）
const BUILTIN_READ_BLACKLIST_LINES: &[&str] = &[
    "密钥与云凭证目录：.ssh、.aws、.kube、.gnupg",
    "环境变量文件：路径段含 .env 或 credentials",
    "Windows 系统目录（含 System32、SysWOW64、WindowsApps 及 C:\\Windows\\…）",
    "SAM/SECURITY 注册表配置单元",
    "路径段名为 etc（含 C:\\etc、/etc/…）",
    "Linux /boot（根下 boot 段）及盘符根下 Boot",
    "/var/log 及 /etc/shadow、/etc/passwd",
    "Chromium 系浏览器用户数据中的 Cookies / Login Data",
];

#[derive(Debug, Serialize)]
#[serde(rename_all = "snake_case")]
pub struct NativeFsPolicyPayload {
    pub ok: bool,
    pub policy_file: String,
    /// 无 L3 解析时为空；桌面端依赖 L3 HTTP 填充
    pub builtin_write_roots: Vec<String>,
    pub custom_write_roots: Vec<String>,
    pub builtin_read_blacklist_lines: Vec<String>,
    pub custom_read_blacklist_roots: Vec<String>,
}

fn is_acceptable_extra_path(raw: &str) -> bool {
    let s = raw.trim();
    if s.is_empty() {
        return false;
    }
    let p = Path::new(s);
    p.is_absolute()
}

/// 供前端在无 L3 HTTP 时拉取策略（与 GET /api/v3/config/native-fs-policy 形状一致）
#[tauri::command]
pub fn native_fs_policy_get() -> Result<NativeFsPolicyPayload, String> {
    let path = policy_file_path()?;
    let policy_file = path.to_string_lossy().into_owned();

    let mut file: PolicyFile = PolicyFile::default();
    if path.is_file() {
        let text = fs::read_to_string(&path).map_err(|e| format!("读取策略文件失败: {e}"))?;
        file = serde_json::from_str(&text).unwrap_or_default();
    }

    let custom_write = file
        .write_allowlist_extra
        .unwrap_or_default()
        .into_iter()
        .filter(|s| !s.trim().is_empty())
        .collect();
    let custom_read = file
        .read_blacklist_extra
        .unwrap_or_default()
        .into_iter()
        .filter(|s| !s.trim().is_empty())
        .collect();

    Ok(NativeFsPolicyPayload {
        ok: true,
        policy_file,
        builtin_write_roots: Vec::new(),
        custom_write_roots: custom_write,
        builtin_read_blacklist_lines: BUILTIN_READ_BLACKLIST_LINES
            .iter()
            .map(|s| (*s).to_string())
            .collect(),
        custom_read_blacklist_roots: custom_read,
    })
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "snake_case")]
pub struct NativeFsPolicySetInput {
    pub write_allowlist_extra: Vec<String>,
    pub read_blacklist_extra: Vec<String>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "snake_case")]
pub struct NativeFsPolicySetResult {
    pub ok: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub message: Option<String>,
}

/// 保存用户扩展路径（校验绝对路径后写入 JSON，与 Python `save_policy` 格式一致）
#[tauri::command]
pub fn native_fs_policy_set(
    input: NativeFsPolicySetInput,
) -> Result<NativeFsPolicySetResult, String> {
    let mut errs: Vec<String> = Vec::new();
    let mut w_clean: Vec<String> = Vec::new();
    for (i, s) in input.write_allowlist_extra.iter().enumerate() {
        if is_acceptable_extra_path(s) {
            w_clean.push(s.trim().to_string());
        } else {
            errs.push(format!("写入白名单第 {} 项无效: {:?}", i + 1, s));
        }
    }
    let mut r_clean: Vec<String> = Vec::new();
    for (i, s) in input.read_blacklist_extra.iter().enumerate() {
        if is_acceptable_extra_path(s) {
            r_clean.push(s.trim().to_string());
        } else {
            errs.push(format!("读取黑名单第 {} 项无效: {:?}", i + 1, s));
        }
    }
    if !errs.is_empty() {
        return Ok(NativeFsPolicySetResult {
            ok: false,
            error: Some(errs.join("；")),
            message: None,
        });
    }

    let path = policy_file_path()?;
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|e| format!("创建配置目录失败: {e}"))?;
    }
    let payload = PolicyFile {
        version: Some(1),
        write_allowlist_extra: Some(w_clean),
        read_blacklist_extra: Some(r_clean),
    };
    let json = serde_json::to_string_pretty(&payload).map_err(|e| format!("序列化失败: {e}"))?;
    fs::write(&path, format!("{json}\n")).map_err(|e| format!("写入策略文件失败: {e}"))?;

    Ok(NativeFsPolicySetResult {
        ok: true,
        error: None,
        message: Some("ok".to_string()),
    })
}
