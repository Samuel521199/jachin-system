//! L3 冷启动技能同步：从 L2 拉取 Wasm 技能清单，下载缺失包并校验 SHA-256。
//!
//! 非阻塞：用户可先进行基础对话，技能在后台静默加载。
//! 同步完成后派发 inventory-updated 事件，技能面板首次展示即为完整数据。

use serde::{Deserialize, Serialize};
use tauri::Emitter;
use sha2::{Digest, Sha256};
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};

/// L2 技能元数据（与 GET /api/v2/inventory/skills 响应一致）
#[derive(Debug, Clone, Deserialize)]
struct L2Skill {
    id: String,
    #[serde(rename = "item_id")]
    item_id: String,
    name: String,
    description: Option<String>,
    sha256: Option<String>,
    entry: Option<String>,
    params: Option<Vec<String>>,
}

#[derive(Debug, Deserialize)]
struct L2SkillsResponse {
    skills: Vec<L2Skill>,
    #[allow(dead_code)]
    count: usize,
}

/// 同步结果
#[derive(Debug, Serialize)]
pub struct SyncResult {
    pub synced: usize,
    pub skipped: usize,
    pub failed: Vec<String>,
    pub total: usize,
}

static SYNC_IN_PROGRESS: AtomicBool = AtomicBool::new(false);

/// 是否正在同步
#[tauri::command]
pub fn is_skill_sync_in_progress() -> bool {
    SYNC_IN_PROGRESS.load(Ordering::Relaxed)
}

/// L3 技能缓存目录：~/.jachin/l3_skill_cache/（与 L2 inventory 约定一致）
fn l3_skill_cache_dir() -> PathBuf {
    let home = std::env::var("HOME")
        .or_else(|_| std::env::var("USERPROFILE"))
        .map(PathBuf::from)
        .unwrap_or_else(|_| PathBuf::from("."));
    home.join(".jachin").join("l3_skill_cache")
}

/// 从 l2_gateway_config.json 读取 sub_account_id（L2 鉴权必需）
fn sub_account_id_from_config() -> Option<String> {
    let home = std::env::var("HOME").ok().or_else(|| std::env::var("USERPROFILE").ok())?;
    let path = std::path::Path::new(&home).join(".jachin").join("l2_gateway_config.json");
    let content = std::fs::read_to_string(&path).ok()?;
    let json: serde_json::Value = serde_json::from_str(&content).ok()?;
    json.get("sub_account_id")
        .and_then(|v| v.as_str())
        .map(|s| s.to_string())
        .filter(|s| !s.is_empty())
}

fn compute_sha256_hex(data: &[u8]) -> String {
    let mut hasher = Sha256::new();
    hasher.update(data);
    format!("{:x}", hasher.finalize())
}

/// 执行冷启动同步：获取清单、比对本地、下载缺失、SHA-256 校验。
/// 非阻塞：在后台执行，通过 inventory-sync-progress 事件推送进度。
#[tauri::command]
pub async fn perform_startup_sync(
    app: tauri::AppHandle,
    base_url: String,
) -> Result<SyncResult, String> {
    if SYNC_IN_PROGRESS.swap(true, Ordering::Relaxed) {
        return Ok(SyncResult {
            synced: 0,
            skipped: 0,
            failed: vec![],
            total: 0,
        });
    }

    let url = base_url.trim_end_matches('/');
    let list_url = format!("{}/api/v2/inventory/skills", url);
    let cache_dir = l3_skill_cache_dir();

    let sub_account_id = sub_account_id_from_config();
    if sub_account_id.is_none() {
        SYNC_IN_PROGRESS.store(false, Ordering::Relaxed);
        return Err("未找到 sub_account_id，请先完成 L2 网关配对".to_string());
    }
    let sub_account_id = sub_account_id.unwrap();

    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(120))
        .build()
        .map_err(|e| e.to_string())?;

    let resp = client
        .get(&list_url)
        .header("X-Sub-Account-Id", &sub_account_id)
        .send()
        .await
        .map_err(|e| e.to_string())?;

    if !resp.status().is_success() {
        SYNC_IN_PROGRESS.store(false, Ordering::Relaxed);
        return Err(format!("L2 清单请求失败: {} (需 X-Sub-Account-Id)", resp.status()));
    }

    let data: L2SkillsResponse = resp.json().await.map_err(|e| e.to_string())?;
    let skills = data.skills;
    let total = skills.len();

    let mut synced = 0usize;
    let mut skipped = 0usize;
    let mut failed = Vec::new();

    for (i, skill) in skills.iter().enumerate() {
        let _ = app.emit(
            "inventory-sync-progress",
            serde_json::json!({
                "phase": "syncing",
                "current": i + 1,
                "total": total,
                "item_id": skill.item_id,
                "name": skill.name,
            }),
        );

        let skill_dir = cache_dir.join(&skill.item_id);
        let entry_name = skill.entry.as_deref().unwrap_or("main.wasm");
        let wasm_path = skill_dir.join(entry_name);

        let need_download = if wasm_path.exists() {
            if let Some(expected) = &skill.sha256 {
                let bytes = std::fs::read(&wasm_path).map_err(|e| e.to_string())?;
                let actual = compute_sha256_hex(&bytes);
                if actual != expected.to_lowercase() {
                    true
                } else {
                    skipped += 1;
                    false
                }
            } else {
                skipped += 1;
                false
            }
        } else {
            true
        };

        if !need_download {
            continue;
        }

        let download_url = format!("{}/api/v2/inventory/skills/{}/download", url, skill.item_id);
        match client
            .get(&download_url)
            .header("X-Sub-Account-Id", &sub_account_id)
            .send()
            .await
        {
            Ok(r) if r.status().is_success() => {
                let bytes = r.bytes().await.map_err(|e| e.to_string())?;
                let expected_sha = skill.sha256.as_ref().map(|s| s.to_lowercase());

                if let Some(ref exp) = expected_sha {
                    let actual = compute_sha256_hex(bytes.as_ref());
                    if actual != *exp {
                        failed.push(format!("{}: SHA-256 校验失败", skill.item_id));
                        continue;
                    }
                }

                std::fs::create_dir_all(&skill_dir).map_err(|e| e.to_string())?;
                std::fs::write(&wasm_path, bytes.as_ref()).map_err(|e| e.to_string())?;

                let plugin_json = serde_json::json!({
                    "id": skill.id.strip_prefix("jpp:").unwrap_or(&skill.id),
                    "name": skill.name,
                    "description": skill.description.as_deref().unwrap_or(""),
                    "entry": entry_name,
                    "parameters": skill.params.as_ref().map(|p| {
                        p.iter().map(|s| serde_json::json!({"name": s})).collect::<Vec<_>>()
                    }).unwrap_or_default(),
                });
                let plugin_path = skill_dir.join("plugin.json");
                std::fs::write(
                    plugin_path,
                    serde_json::to_string_pretty(&plugin_json).unwrap_or_default(),
                )
                .map_err(|e| e.to_string())?;

                synced += 1;
            }
            Ok(r) => {
                failed.push(format!("{}: HTTP {}", skill.item_id, r.status()));
            }
            Err(e) => {
                failed.push(format!("{}: {}", skill.item_id, e));
            }
        }
    }

    SYNC_IN_PROGRESS.store(false, Ordering::Relaxed);

    let _ = app.emit(
        "inventory-sync-progress",
        serde_json::json!({
            "phase": "complete",
            "synced": synced,
            "skipped": skipped,
            "failed": failed.len(),
            "total": total,
        }),
    );

    let _ = app.emit("inventory-sync-complete", ());

    Ok(SyncResult {
        synced,
        skipped,
        failed,
        total,
    })
}
