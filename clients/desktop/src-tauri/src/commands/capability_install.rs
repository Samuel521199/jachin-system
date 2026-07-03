use serde::{Deserialize, Serialize};
use serde_json::Value;
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, HashMap, HashSet};
use std::fs;
use std::io::Read;
use std::path::{Path, PathBuf};
use std::time::{Duration, SystemTime, UNIX_EPOCH};
use zip::ZipArchive;

#[derive(Debug, Clone, Serialize)]
pub struct CapabilityInstallScan {
    pub l1_base_url: String,
    pub active_l1_profile_id: Option<String>,
    pub registry_path: String,
    pub mcp_cache_dir: String,
    pub skill_cache_dir: String,
    pub model_cache_dir: String,
    pub source_store_dir: String,
    pub download_dir: String,
    pub items: Vec<CapabilityInstallItem>,
    pub counts: BTreeMap<String, usize>,
}

#[derive(Debug, Clone, Serialize)]
pub struct CapabilityInstallItem {
    pub id: String,
    pub name: String,
    pub kind: String,
    pub description: Option<String>,
    pub l1_version: Option<String>,
    pub local_version: Option<String>,
    pub package_url: Option<String>,
    pub package_sha256: Option<String>,
    pub installed_sha256: Option<String>,
    pub installed_path: Option<String>,
    pub source_store_path: Option<String>,
    pub enabled: bool,
    pub source: String,
    pub source_l1_base_url: Option<String>,
    pub source_l1_profile_id: Option<String>,
    pub current_l1_match: bool,
    pub current_l1_cached: bool,
    pub l1_status: Option<String>,
    pub status: String,
    pub problems: Vec<String>,
    pub dependencies: Vec<String>,
}

#[derive(Debug, Clone, Serialize)]
pub struct CapabilityInstallResult {
    pub ok: bool,
    pub id: String,
    pub version: String,
    pub kind: String,
    pub installed_path: String,
    pub package_sha256: String,
    pub message: String,
}

#[derive(Debug, Clone, Serialize)]
pub struct CapabilityL1Profile {
    pub id: String,
    pub name: String,
    pub base_url: String,
    pub developer_id: Option<String>,
    pub token_present: bool,
    pub token_preview: Option<String>,
    pub active: bool,
}

#[derive(Debug, Clone, Serialize)]
pub struct CapabilityL1ProfilesResult {
    pub active_profile_id: Option<String>,
    pub profiles: Vec<CapabilityL1Profile>,
    pub config_path: String,
}

#[derive(Debug, Clone, Deserialize)]
pub struct CapabilityInstallInput {
    pub id: String,
    pub package_url: Option<String>,
    pub kind: Option<String>,
    pub repair: Option<bool>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct CapabilityL1ProfileInput {
    pub id: Option<String>,
    pub name: Option<String>,
    pub base_url: String,
    pub developer_id: Option<String>,
    pub developer_token: Option<String>,
    pub activate: Option<bool>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct CapabilityL1ProfileActivateInput {
    pub id: String,
}

#[derive(Debug, Clone, Deserialize)]
pub struct CapabilityEnableInput {
    pub id: String,
    pub enabled: bool,
}

#[derive(Debug, Clone, Deserialize)]
pub struct CapabilityUninstallInput {
    pub id: String,
    pub confirm: bool,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
struct L1DirectConfig {
    base_url: String,
    developer_id: Option<String>,
    developer_token: Option<String>,
    visibility: Option<String>,
    upload_by_default: Option<bool>,
    #[serde(default)]
    profile_id: Option<String>,
    #[serde(default)]
    profile_name: Option<String>,
}

const DEFAULT_L1_BASE_URL: &str = "http://47.86.39.173:3000";

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
struct L1ProfilesFile {
    active_profile_id: Option<String>,
    profiles: BTreeMap<String, L1ProfileRecord>,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
struct L1ProfileRecord {
    id: String,
    name: String,
    base_url: String,
    developer_id: Option<String>,
    developer_token: Option<String>,
    visibility: Option<String>,
    upload_by_default: Option<bool>,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
struct InstalledRegistry {
    packages: HashMap<String, InstalledRecord>,
    #[serde(default)]
    source_packages: HashMap<String, InstalledRecord>,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
struct InstalledRecord {
    id: String,
    name: String,
    version: String,
    kind: String,
    source: String,
    #[serde(default)]
    source_l1_base_url: Option<String>,
    #[serde(default)]
    source_l1_profile_id: Option<String>,
    #[serde(default)]
    source_store_path: Option<String>,
    package_url: Option<String>,
    package_sha256: Option<String>,
    installed_path: String,
    installed_at: String,
    enabled: bool,
    #[serde(default)]
    package_assets: Vec<PackageAssetMeta>,
    #[serde(default)]
    preserve_user_data: Vec<String>,
    #[serde(default)]
    dependencies: Vec<String>,
}

#[derive(Debug, Clone, Default)]
struct RemoteItem {
    id: String,
    name: String,
    kind: String,
    description: Option<String>,
    version: Option<String>,
    package_url: Option<String>,
    package_sha256: Option<String>,
    status: Option<String>,
    source: String,
    dependencies: Vec<String>,
}

#[derive(Debug, Clone, Default, Deserialize)]
struct PackageMeta {
    id: Option<String>,
    kind: Option<String>,
    version: Option<String>,
    #[serde(default)]
    package_assets: Vec<PackageAssetMeta>,
    #[serde(default)]
    preserve_user_data: Vec<String>,
    #[serde(default)]
    required_mcps: Vec<String>,
    #[serde(default)]
    required_models: Vec<String>,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
struct PackageAssetMeta {
    from: Option<String>,
    to: Option<String>,
    sha256: Option<String>,
    size: Option<u64>,
}

#[tauri::command]
pub fn capability_install_scan() -> Result<CapabilityInstallScan, String> {
    let cfg = read_l1_direct_config();
    let mut registry = read_installed_registry();
    let remote = fetch_l1_items(&cfg).unwrap_or_default();
    merge_disk_installs(&mut registry);
    let mut items = Vec::new();
    let mut seen: HashMap<String, bool> = HashMap::new();

    for item in remote.values() {
        let rec = registry.packages.get(&item.id);
        let source_rec = registry
            .source_packages
            .get(&source_record_key_for(&cfg, &item.kind, &item.id));
        items.push(install_item_from_remote(item, rec, source_rec, &cfg));
        seen.insert(item.id.clone(), true);
    }
    for rec in registry.packages.values() {
        if seen.contains_key(&rec.id) {
            continue;
        }
        items.push(install_item_local_only(rec));
    }
    items.sort_by(|a, b| a.status.cmp(&b.status).then_with(|| a.id.cmp(&b.id)));

    let mut counts = BTreeMap::new();
    for key in [
        "total",
        "installed",
        "not_installed",
        "update_available",
        "repair_needed",
        "disabled",
        "local_only",
        "source_mismatch",
        "blocked",
        "model",
    ] {
        let v = if key == "total" {
            items.len()
        } else if key == "model" {
            items.iter().filter(|i| i.kind == "model").count()
        } else {
            items.iter().filter(|i| i.status == key).count()
        };
        counts.insert(key.to_string(), v);
    }

    Ok(CapabilityInstallScan {
        l1_base_url: cfg.base_url.trim().trim_end_matches('/').to_string(),
        active_l1_profile_id: cfg.profile_id.clone(),
        registry_path: installed_registry_path().display().to_string(),
        mcp_cache_dir: l3_mcp_cache_dir().display().to_string(),
        skill_cache_dir: l3_skill_cache_dir().display().to_string(),
        model_cache_dir: model_cache_dir().display().to_string(),
        source_store_dir: source_store_root().display().to_string(),
        download_dir: download_dir().display().to_string(),
        items,
        counts,
    })
}

#[tauri::command]
pub fn capability_install_local_inventory() -> Result<Vec<CapabilityInstallItem>, String> {
    let mut registry = read_installed_registry();
    merge_disk_installs(&mut registry);
    let mut items: Vec<CapabilityInstallItem> = registry
        .packages
        .values()
        .map(install_item_local_only)
        .collect();
    items.sort_by(|a, b| a.id.cmp(&b.id));
    Ok(items)
}

#[tauri::command]
pub fn capability_l1_profiles_get() -> Result<CapabilityL1ProfilesResult, String> {
    let profiles = read_l1_profiles_file();
    Ok(profiles_result(&profiles))
}

#[tauri::command]
pub fn capability_l1_profile_save(
    input: CapabilityL1ProfileInput,
) -> Result<CapabilityL1ProfilesResult, String> {
    let base_url = normalize_base_url(&input.base_url);
    if base_url.is_empty() {
        return Err("L1 base_url is required".to_string());
    }
    if !base_url.starts_with("http://") && !base_url.starts_with("https://") {
        return Err("L1 base_url must start with http:// or https://".to_string());
    }
    let mut file = read_l1_profiles_file();
    let id = input
        .id
        .as_deref()
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(str::to_string)
        .unwrap_or_else(|| profile_id_for_base_url(&base_url));
    let previous = file.profiles.get(&id).cloned().unwrap_or_default();
    let token = input
        .developer_token
        .as_deref()
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(str::to_string)
        .or(previous.developer_token);
    let record = L1ProfileRecord {
        id: id.clone(),
        name: input
            .name
            .as_deref()
            .map(str::trim)
            .filter(|s| !s.is_empty())
            .map(str::to_string)
            .unwrap_or_else(|| default_profile_name(&base_url)),
        base_url,
        developer_id: input
            .developer_id
            .as_deref()
            .map(str::trim)
            .filter(|s| !s.is_empty())
            .map(str::to_string)
            .or(previous.developer_id),
        developer_token: token,
        visibility: previous.visibility,
        upload_by_default: previous.upload_by_default,
    };
    file.profiles.insert(id.clone(), record);
    if input.activate.unwrap_or(true) || file.active_profile_id.is_none() {
        file.active_profile_id = Some(id);
    }
    write_l1_profiles_file(&file)?;
    sync_active_profile_to_legacy_config(&file)?;
    Ok(profiles_result(&file))
}

#[tauri::command]
pub fn capability_l1_profile_activate(
    input: CapabilityL1ProfileActivateInput,
) -> Result<CapabilityL1ProfilesResult, String> {
    let mut file = read_l1_profiles_file();
    let id = input.id.trim();
    if !file.profiles.contains_key(id) {
        return Err(format!("L1 profile not found: {id}"));
    }
    file.active_profile_id = Some(id.to_string());
    write_l1_profiles_file(&file)?;
    sync_active_profile_to_legacy_config(&file)?;
    Ok(profiles_result(&file))
}

#[tauri::command]
pub fn capability_install_package(
    input: CapabilityInstallInput,
) -> Result<CapabilityInstallResult, String> {
    let cfg = read_l1_direct_config();
    let remote = fetch_l1_items(&cfg)?;
    let mut visiting = HashSet::new();
    let mut installed = HashSet::new();
    install_package_recursive(&cfg, &remote, input, &mut visiting, &mut installed)
}

fn install_package_recursive(
    cfg: &L1DirectConfig,
    remote: &HashMap<String, RemoteItem>,
    input: CapabilityInstallInput,
    visiting: &mut HashSet<String>,
    installed: &mut HashSet<String>,
) -> Result<CapabilityInstallResult, String> {
    let target_id = normalize_dependency_id(&input.id);
    let item = remote
        .get(&target_id)
        .ok_or_else(|| format!("package not found on L1: {}", target_id))?;

    if item_is_ready(item) && !input.repair.unwrap_or(false) {
        let registry = read_installed_registry();
        if let Some(rec) = registry.packages.get(&target_id) {
            return Ok(CapabilityInstallResult {
                ok: true,
                id: rec.id.clone(),
                version: rec.version.clone(),
                kind: rec.kind.clone(),
                installed_path: rec.installed_path.clone(),
                package_sha256: rec.package_sha256.clone().unwrap_or_default(),
                message: "already installed".to_string(),
            });
        }
    }

    if !visiting.insert(target_id.clone()) {
        return Err(format!(
            "dependency cycle detected while installing {}",
            target_id
        ));
    }

    for dep_raw in &item.dependencies {
        let dep_id = normalize_dependency_id(dep_raw);
        if dep_id.is_empty() || dep_id == target_id {
            continue;
        }
        let dep_item = remote.get(&dep_id).ok_or_else(|| {
            format!(
                "dependency {} required by {} is missing from L1 catalog",
                dep_raw, target_id
            )
        })?;
        if installed.contains(&dep_id) || item_is_ready(dep_item) {
            continue;
        }
        let dep_input = CapabilityInstallInput {
            id: dep_id.clone(),
            package_url: dep_item.package_url.clone(),
            kind: Some(dep_item.kind.clone()),
            repair: Some(false),
        };
        install_package_recursive(cfg, remote, dep_input, visiting, installed)?;
        installed.insert(dep_id);
    }
    visiting.remove(&target_id);

    let result = install_single_package(
        cfg,
        item,
        CapabilityInstallInput {
            id: target_id.clone(),
            package_url: input.package_url.or_else(|| item.package_url.clone()),
            kind: input.kind.or_else(|| Some(item.kind.clone())),
            repair: input.repair,
        },
    )?;
    installed.insert(target_id);
    Ok(result)
}

fn item_is_ready(item: &RemoteItem) -> bool {
    let cfg = read_l1_direct_config();
    let mut registry = read_installed_registry();
    merge_disk_installs(&mut registry);
    let source_rec = registry
        .source_packages
        .get(&source_record_key_for(&cfg, &item.kind, &item.id));
    let probe = install_item_from_remote(item, registry.packages.get(&item.id), source_rec, &cfg);
    probe.enabled && probe.status == "installed"
}

fn install_single_package(
    cfg: &L1DirectConfig,
    item: &RemoteItem,
    input: CapabilityInstallInput,
) -> Result<CapabilityInstallResult, String> {
    let url = input
        .package_url
        .as_deref()
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(|s| s.to_string())
        .or_else(|| item.package_url.clone())
        .ok_or_else(|| format!("package_url is missing for {}", input.id))?;
    let full_url = resolve_package_url(&cfg.base_url, &url)?;
    let kind_hint = input.kind.as_deref().unwrap_or(&item.kind);
    let remote_kind = normalize_kind(kind_hint);
    if !input.repair.unwrap_or(false) {
        if let Some(result) = try_activate_cached_source(cfg, item, &remote_kind, &full_url)? {
            return Ok(result);
        }
    }
    let downloaded = download_package(&full_url, &input.id)?;
    let actual_sha = sha256_file(&downloaded)?;
    if let Some(expected) = item
        .package_sha256
        .as_deref()
        .map(str::trim)
        .filter(|s| !s.is_empty())
    {
        if !expected.eq_ignore_ascii_case(&actual_sha) {
            return Err(format!(
                "sha256 mismatch for {}: expected {}, got {}",
                input.id, expected, actual_sha
            ));
        }
    }

    let staging = staging_dir(&input.id);
    if staging.exists() {
        fs::remove_dir_all(&staging).map_err(|e| format!("clear staging failed: {e}"))?;
    }
    fs::create_dir_all(&staging).map_err(|e| format!("create staging failed: {e}"))?;
    extract_zip_archive(&downloaded, &staging)?;
    let meta = read_package_meta(&staging).unwrap_or_default();
    let id = meta.id.clone().unwrap_or_else(|| input.id.clone());
    let kind = normalize_kind(meta.kind.as_deref().unwrap_or(kind_hint));
    let version = meta
        .version
        .clone()
        .or_else(|| item.version.clone())
        .unwrap_or_else(|| "0.0.0".to_string());
    let name = item.name.clone();
    let final_dir = install_dir_for(&kind, &id);
    let source_dir = source_store_dir_for(cfg, &kind, &id);
    if source_dir.exists() {
        fs::remove_dir_all(&source_dir)
            .map_err(|e| format!("remove previous source package failed: {e}"))?;
    }
    if let Some(parent) = source_dir.parent() {
        fs::create_dir_all(parent)
            .map_err(|e| format!("create source package parent failed: {e}"))?;
    }
    fs::rename(&staging, &source_dir)
        .map_err(|e| format!("move package into source store failed: {e}"))?;
    materialize_active_install(&source_dir, &final_dir, &meta.preserve_user_data)?;
    let dependencies = merge_package_dependencies(&item.dependencies, &meta);

    let mut registry = read_installed_registry();
    let record = InstalledRecord {
        id: id.clone(),
        name,
        version: version.clone(),
        kind: kind.clone(),
        source: "l1".to_string(),
        source_l1_base_url: Some(normalize_base_url(&cfg.base_url)),
        source_l1_profile_id: cfg.profile_id.clone(),
        source_store_path: Some(source_dir.display().to_string()),
        package_url: Some(full_url),
        package_sha256: Some(actual_sha.clone()),
        installed_path: final_dir.display().to_string(),
        installed_at: timestamp_string(),
        enabled: true,
        package_assets: meta.package_assets,
        preserve_user_data: meta.preserve_user_data,
        dependencies,
    };
    registry.source_packages.insert(
        source_record_key_from_parts(
            record.source_l1_profile_id.as_deref(),
            &kind,
            &id,
            &record.source_l1_base_url,
        ),
        record.clone(),
    );
    registry.packages.insert(id.clone(), record);
    write_installed_registry(&registry)?;

    Ok(CapabilityInstallResult {
        ok: true,
        id,
        version,
        kind,
        installed_path: final_dir.display().to_string(),
        package_sha256: actual_sha,
        message: if input.repair.unwrap_or(false) {
            "repaired from L1 package".to_string()
        } else {
            "installed from L1 package".to_string()
        },
    })
}

fn try_activate_cached_source(
    cfg: &L1DirectConfig,
    item: &RemoteItem,
    kind: &str,
    full_url: &str,
) -> Result<Option<CapabilityInstallResult>, String> {
    let mut registry = read_installed_registry();
    let key = source_record_key_for(cfg, kind, &item.id);
    let Some(source_rec) = registry.source_packages.get(&key).cloned() else {
        return Ok(None);
    };
    if let Some(remote_version) = item.version.as_deref() {
        if remote_version != source_rec.version {
            return Ok(None);
        }
    }
    if let Some(remote_sha) = item
        .package_sha256
        .as_deref()
        .map(str::trim)
        .filter(|s| !s.is_empty())
    {
        if source_rec
            .package_sha256
            .as_deref()
            .map(|local| remote_sha.eq_ignore_ascii_case(local))
            != Some(true)
        {
            return Ok(None);
        }
    }
    let source_dir = source_rec
        .source_store_path
        .as_deref()
        .map(PathBuf::from)
        .unwrap_or_else(|| source_store_dir_for(cfg, &source_rec.kind, &source_rec.id));
    if !source_dir.is_dir() {
        return Ok(None);
    }
    let final_dir = install_dir_for(&source_rec.kind, &source_rec.id);
    materialize_active_install(&source_dir, &final_dir, &source_rec.preserve_user_data)?;

    let mut active = source_rec.clone();
    active.package_url = Some(full_url.to_string());
    active.installed_path = final_dir.display().to_string();
    active.enabled = true;
    active.installed_at = timestamp_string();
    registry.packages.insert(active.id.clone(), active.clone());
    registry.source_packages.insert(key, active.clone());
    write_installed_registry(&registry)?;

    Ok(Some(CapabilityInstallResult {
        ok: true,
        id: active.id,
        version: active.version,
        kind: active.kind,
        installed_path: final_dir.display().to_string(),
        package_sha256: active.package_sha256.unwrap_or_default(),
        message: "activated from cached L1 source package".to_string(),
    }))
}

#[tauri::command]
pub fn capability_install_set_enabled(
    input: CapabilityEnableInput,
) -> Result<CapabilityInstallResult, String> {
    let mut registry = read_installed_registry();
    let rec = registry
        .packages
        .get_mut(&input.id)
        .ok_or_else(|| format!("not installed: {}", input.id))?;
    if input.enabled && !rec.enabled {
        let current = PathBuf::from(&rec.installed_path);
        let target = install_dir_for(&rec.kind, &rec.id);
        if current.is_dir() {
            if target.exists() {
                fs::remove_dir_all(&target)
                    .map_err(|e| format!("remove stale cache dir failed: {e}"))?;
            }
            if let Some(parent) = target.parent() {
                fs::create_dir_all(parent)
                    .map_err(|e| format!("create cache parent failed: {e}"))?;
            }
            fs::rename(&current, &target)
                .map_err(|e| format!("move package back to cache failed: {e}"))?;
            rec.installed_path = target.display().to_string();
        } else if let Some(source) = rec.source_store_path.as_deref().map(PathBuf::from) {
            if !source.is_dir() {
                return Err(format!(
                    "source package path is missing: {}",
                    source.display()
                ));
            }
            materialize_active_install(&source, &target, &rec.preserve_user_data)?;
            rec.installed_path = target.display().to_string();
        } else {
            return Err(format!(
                "disabled package path is missing: {}",
                current.display()
            ));
        }
    } else if !input.enabled && rec.enabled {
        let current = PathBuf::from(&rec.installed_path);
        if current.is_dir() {
            let target = disabled_dir().join(safe_id(&rec.id));
            if target.exists() {
                fs::remove_dir_all(&target)
                    .map_err(|e| format!("remove stale disabled dir failed: {e}"))?;
            }
            if let Some(parent) = target.parent() {
                fs::create_dir_all(parent)
                    .map_err(|e| format!("create disabled parent failed: {e}"))?;
            }
            fs::rename(&current, &target)
                .map_err(|e| format!("move package to disabled cache failed: {e}"))?;
            rec.installed_path = target.display().to_string();
        }
    }
    rec.enabled = input.enabled;
    let out = CapabilityInstallResult {
        ok: true,
        id: rec.id.clone(),
        version: rec.version.clone(),
        kind: rec.kind.clone(),
        installed_path: rec.installed_path.clone(),
        package_sha256: rec.package_sha256.clone().unwrap_or_default(),
        message: if input.enabled { "enabled" } else { "disabled" }.to_string(),
    };
    write_installed_registry(&registry)?;
    Ok(out)
}

#[tauri::command]
pub fn capability_install_uninstall(input: CapabilityUninstallInput) -> Result<(), String> {
    if !input.confirm {
        return Err("uninstall requires confirm=true".to_string());
    }
    let mut registry = read_installed_registry();
    let rec = registry
        .packages
        .remove(&input.id)
        .ok_or_else(|| format!("not installed: {}", input.id))?;
    let p = PathBuf::from(&rec.installed_path);
    if p.exists() {
        let cache_roots = [
            l3_mcp_cache_dir(),
            l3_skill_cache_dir(),
            model_cache_dir(),
            source_store_root(),
            legacy_skills_dir(),
            disabled_dir(),
        ];
        let safe = cache_roots.iter().any(|root| p.starts_with(root));
        if !safe {
            return Err(format!(
                "refuse to remove path outside capability caches: {}",
                p.display()
            ));
        }
        fs::remove_dir_all(&p).map_err(|e| format!("remove install dir failed: {e}"))?;
    }
    let source_keys: Vec<String> = registry
        .source_packages
        .iter()
        .filter_map(|(key, source_rec)| {
            if source_rec.id == rec.id {
                Some(key.clone())
            } else {
                None
            }
        })
        .collect();
    for key in source_keys {
        if let Some(source_rec) = registry.source_packages.remove(&key) {
            if let Some(source) = source_rec.source_store_path.as_deref().map(PathBuf::from) {
                if source.exists() {
                    let safe = source.starts_with(source_store_root());
                    if !safe {
                        return Err(format!(
                            "refuse to remove source path outside capability source store: {}",
                            source.display()
                        ));
                    }
                    fs::remove_dir_all(&source)
                        .map_err(|e| format!("remove source package dir failed: {e}"))?;
                }
            }
        }
    }
    write_installed_registry(&registry)?;
    Ok(())
}

fn install_item_from_remote(
    item: &RemoteItem,
    rec: Option<&InstalledRecord>,
    source_rec: Option<&InstalledRecord>,
    cfg: &L1DirectConfig,
) -> CapabilityInstallItem {
    let mut problems = Vec::new();
    let current_source_cached = source_rec
        .and_then(|r| r.source_store_path.as_deref())
        .map(PathBuf::from)
        .map(|p| p.is_dir())
        .unwrap_or(false);
    if item.package_url.as_deref().unwrap_or("").trim().is_empty() && !current_source_cached {
        problems.push("missing package_url".to_string());
    }
    let (local_version, installed_sha256, installed_path, enabled, status) = if let Some(r) = rec {
        let path = PathBuf::from(&r.installed_path);
        let exists = path.is_dir();
        let current_l1_match = record_matches_current_l1(r, cfg);
        let sha_match = match (item.package_sha256.as_deref(), r.package_sha256.as_deref()) {
            (Some(remote), Some(local)) if !remote.trim().is_empty() => {
                remote.eq_ignore_ascii_case(local)
            }
            _ => true,
        };
        let status = if !current_l1_match {
            if current_source_cached {
                "source_cached"
            } else {
                "source_mismatch"
            }
        } else if !exists {
            "repair_needed"
        } else if !r.enabled {
            "disabled"
        } else if item.version.as_deref().unwrap_or("") != r.version && item.version.is_some() {
            "update_available"
        } else if !sha_match {
            "repair_needed"
        } else if !problems.is_empty() {
            "blocked"
        } else {
            "installed"
        };
        (
            Some(r.version.clone()),
            r.package_sha256.clone(),
            Some(r.installed_path.clone()),
            r.enabled,
            status.to_string(),
        )
    } else if !problems.is_empty() {
        (None, None, None, false, "blocked".to_string())
    } else if let Some(r) = source_rec {
        (
            Some(r.version.clone()),
            r.package_sha256.clone(),
            r.source_store_path.clone(),
            false,
            if current_source_cached {
                "source_cached".to_string()
            } else {
                "repair_needed".to_string()
            },
        )
    } else {
        (None, None, None, false, "not_installed".to_string())
    };

    CapabilityInstallItem {
        id: item.id.clone(),
        name: item.name.clone(),
        kind: item.kind.clone(),
        description: item.description.clone(),
        l1_version: item.version.clone(),
        local_version,
        package_url: item.package_url.clone(),
        package_sha256: item.package_sha256.clone(),
        installed_sha256,
        installed_path,
        source_store_path: rec.or(source_rec).and_then(|r| r.source_store_path.clone()),
        enabled,
        source: item.source.clone(),
        source_l1_base_url: rec
            .or(source_rec)
            .and_then(|r| r.source_l1_base_url.clone()),
        source_l1_profile_id: rec
            .or(source_rec)
            .and_then(|r| r.source_l1_profile_id.clone()),
        current_l1_match: rec
            .map(|r| record_matches_current_l1(r, cfg))
            .unwrap_or(false),
        current_l1_cached: current_source_cached,
        l1_status: item.status.clone(),
        status,
        problems,
        dependencies: item.dependencies.clone(),
    }
}

fn install_item_local_only(rec: &InstalledRecord) -> CapabilityInstallItem {
    let exists = PathBuf::from(&rec.installed_path).is_dir();
    CapabilityInstallItem {
        id: rec.id.clone(),
        name: rec.name.clone(),
        kind: rec.kind.clone(),
        description: None,
        l1_version: None,
        local_version: Some(rec.version.clone()),
        package_url: rec.package_url.clone(),
        package_sha256: None,
        installed_sha256: rec.package_sha256.clone(),
        installed_path: Some(rec.installed_path.clone()),
        source_store_path: rec.source_store_path.clone(),
        enabled: rec.enabled,
        source: rec.source.clone(),
        source_l1_base_url: rec.source_l1_base_url.clone(),
        source_l1_profile_id: rec.source_l1_profile_id.clone(),
        current_l1_match: false,
        current_l1_cached: false,
        l1_status: None,
        status: if !exists {
            "repair_needed".to_string()
        } else if !rec.enabled {
            "disabled".to_string()
        } else {
            "local_only".to_string()
        },
        problems: Vec::new(),
        dependencies: rec.dependencies.clone(),
    }
}

fn fetch_l1_items(config: &L1DirectConfig) -> Result<HashMap<String, RemoteItem>, String> {
    let base = config.base_url.trim().trim_end_matches('/').to_string();
    if base.is_empty() {
        return Ok(HashMap::new());
    }
    let client = http_client()?;
    let mut out = HashMap::new();
    fetch_catalog_items(&client, &base, &mut out)?;
    if let Some(dev) = config
        .developer_id
        .as_deref()
        .map(str::trim)
        .filter(|s| !s.is_empty())
    {
        fetch_developer_items(&client, &base, dev, &mut out)?;
    }
    Ok(out)
}

fn fetch_catalog_items(
    client: &reqwest::blocking::Client,
    base: &str,
    out: &mut HashMap<String, RemoteItem>,
) -> Result<(), String> {
    let url = format!("{}/api/v1/store/catalog?limit=500", base);
    let resp = client
        .get(url)
        .send()
        .map_err(|e| format!("fetch L1 catalog failed: {e}"))?;
    if !resp.status().is_success() {
        return Err(format!(
            "fetch L1 catalog failed HTTP {}",
            resp.status().as_u16()
        ));
    }
    let value: Value = resp
        .json()
        .map_err(|e| format!("parse L1 catalog failed: {e}"))?;
    if let Some(items) = value.get("data").and_then(Value::as_array) {
        for item in items {
            if let Some(r) = remote_item_from_value(item, "catalog") {
                out.entry(r.id.clone()).or_insert(r);
            }
        }
    }
    Ok(())
}

fn fetch_developer_items(
    client: &reqwest::blocking::Client,
    base: &str,
    developer_id: &str,
    out: &mut HashMap<String, RemoteItem>,
) -> Result<(), String> {
    let url = format!(
        "{}/api/v1/developer/plugins?developer_id={}",
        base, developer_id
    );
    let resp = client
        .get(url)
        .send()
        .map_err(|e| format!("fetch L1 developer packages failed: {e}"))?;
    if !resp.status().is_success() {
        return Err(format!(
            "fetch L1 developer packages failed HTTP {}",
            resp.status().as_u16()
        ));
    }
    let value: Value = resp
        .json()
        .map_err(|e| format!("parse L1 developer packages failed: {e}"))?;
    if let Some(items) = value.get("data").and_then(Value::as_array) {
        for item in items {
            if let Some(r) = remote_item_from_value(item, "developer") {
                out.insert(r.id.clone(), r);
            }
        }
    }
    Ok(())
}

fn remote_item_from_value(item: &Value, source: &str) -> Option<RemoteItem> {
    let id = item
        .get("plugin_id")
        .or_else(|| item.get("id"))
        .and_then(Value::as_str)?
        .trim()
        .to_string();
    if id.is_empty() {
        return None;
    }
    let kind = normalize_kind(
        item.get("item_type")
            .or_else(|| item.get("type"))
            .and_then(Value::as_str)
            .unwrap_or("SKILL"),
    );
    Some(RemoteItem {
        id,
        name: item
            .get("name")
            .and_then(Value::as_str)
            .unwrap_or("Unnamed")
            .to_string(),
        kind,
        description: item
            .get("description")
            .and_then(Value::as_str)
            .map(|s| s.to_string()),
        version: item
            .get("version")
            .and_then(Value::as_str)
            .map(|s| s.to_string()),
        package_url: item
            .get("package_url")
            .and_then(Value::as_str)
            .map(|s| s.to_string()),
        package_sha256: item
            .get("package_sha256")
            .and_then(Value::as_str)
            .map(|s| s.to_string()),
        status: item
            .get("status")
            .and_then(Value::as_str)
            .map(|s| s.to_string()),
        source: source.to_string(),
        dependencies: remote_dependencies_from_value(item),
    })
}

fn remote_dependencies_from_value(item: &Value) -> Vec<String> {
    let mut out = Vec::new();
    let mut seen = HashSet::new();
    for key in ["required_mcps", "dependencies"] {
        if let Some(arr) = item.get(key).and_then(Value::as_array) {
            for dep in arr.iter().filter_map(Value::as_str) {
                let dep = dep.trim();
                if dep.is_empty() {
                    continue;
                }
                let normalized = normalize_dependency_id(dep);
                if seen.insert(normalized) {
                    out.push(dep.to_string());
                }
            }
        }
    }
    if let Some(arr) = item.get("required_models").and_then(Value::as_array) {
        for dep in arr.iter().filter_map(Value::as_str) {
            let dep = dep.trim();
            if dep.is_empty() {
                continue;
            }
            let raw = if dep.to_ascii_lowercase().starts_with("model:") {
                dep.to_string()
            } else {
                format!("model:{dep}")
            };
            let normalized = normalize_dependency_id(&raw);
            if seen.insert(normalized) {
                out.push(raw);
            }
        }
    }
    out
}

fn merge_disk_installs(registry: &mut InstalledRegistry) {
    for (base, kind) in [
        (l3_mcp_cache_dir(), "mcp"),
        (l3_skill_cache_dir(), "skill"),
        (model_cache_dir(), "model"),
        (legacy_skills_dir(), "skill"),
    ] {
        if !base.is_dir() {
            continue;
        }
        let Ok(entries) = fs::read_dir(&base) else {
            continue;
        };
        for entry in entries.flatten() {
            let path = entry.path();
            if !path.is_dir() {
                continue;
            }
            let id = package_id_from_dir(&path).unwrap_or_else(|| {
                path.file_name()
                    .and_then(|s| s.to_str())
                    .unwrap_or("unknown")
                    .to_string()
            });
            registry
                .packages
                .entry(id.clone())
                .or_insert_with(|| InstalledRecord {
                    id: id.clone(),
                    name: id.clone(),
                    version: package_version_from_dir(&path).unwrap_or_else(|| "0.0.0".to_string()),
                    kind: kind.to_string(),
                    source: "local_cache".to_string(),
                    source_l1_base_url: None,
                    source_l1_profile_id: None,
                    source_store_path: None,
                    package_url: None,
                    package_sha256: None,
                    installed_path: path.display().to_string(),
                    installed_at: timestamp_string(),
                    enabled: true,
                    package_assets: Vec::new(),
                    preserve_user_data: Vec::new(),
                    dependencies: Vec::new(),
                });
        }
    }
}

fn normalize_dependency_id(raw: &str) -> String {
    let s = raw.trim();
    s.strip_prefix("model:")
        .or_else(|| s.strip_prefix("MODEL:"))
        .or_else(|| s.strip_prefix("mcp:"))
        .or_else(|| s.strip_prefix("MCP:"))
        .unwrap_or(s)
        .trim()
        .to_string()
}

fn merge_package_dependencies(remote: &[String], meta: &PackageMeta) -> Vec<String> {
    let mut out = Vec::new();
    let mut seen = HashSet::new();
    for dep in remote
        .iter()
        .cloned()
        .chain(meta.required_mcps.iter().cloned())
    {
        let dep = dep.trim();
        if dep.is_empty() {
            continue;
        }
        let key = normalize_dependency_id(dep);
        if seen.insert(key) {
            out.push(dep.to_string());
        }
    }
    for dep in &meta.required_models {
        let dep = dep.trim();
        if dep.is_empty() {
            continue;
        }
        let raw = if dep.to_ascii_lowercase().starts_with("model:") {
            dep.to_string()
        } else {
            format!("model:{dep}")
        };
        let key = normalize_dependency_id(&raw);
        if seen.insert(key) {
            out.push(raw);
        }
    }
    out
}

fn replace_existing_install_preserving_user_data(
    final_dir: &Path,
    preserve: &[String],
) -> Result<(), String> {
    let local_preserve = local_preserve_paths(preserve);
    if local_preserve.is_empty() {
        return Ok(());
    }
    let backup = final_dir.with_file_name(format!(
        "{}.__jachin_preserve_{}",
        final_dir
            .file_name()
            .and_then(|s| s.to_str())
            .unwrap_or("capability"),
        timestamp_string()
    ));
    if backup.exists() {
        fs::remove_dir_all(&backup)
            .map_err(|e| format!("remove stale preserve backup failed: {e}"))?;
    }
    fs::rename(final_dir, &backup)
        .map_err(|e| format!("move previous install to preserve backup failed: {e}"))?;
    let marker = final_dir.with_extension("preserve_marker");
    let payload = serde_json::json!({
        "backup": backup.display().to_string(),
        "paths": local_preserve,
    });
    fs::write(
        marker,
        serde_json::to_string(&payload)
            .map_err(|e| format!("serialize preserve marker failed: {e}"))?,
    )
    .map_err(|e| format!("write preserve marker failed: {e}"))?;
    Ok(())
}

fn materialize_active_install(
    source_dir: &Path,
    final_dir: &Path,
    preserve: &[String],
) -> Result<(), String> {
    if !source_dir.is_dir() {
        return Err(format!("source package missing: {}", source_dir.display()));
    }
    if final_dir.exists() {
        replace_existing_install_preserving_user_data(final_dir, preserve)?;
    }
    if final_dir.exists() {
        fs::remove_dir_all(final_dir)
            .map_err(|e| format!("remove previous active install failed: {e}"))?;
    }
    copy_dir_recursive(source_dir, final_dir)?;
    restore_preserved_user_data(final_dir, preserve)?;
    Ok(())
}

fn copy_dir_recursive(src: &Path, dst: &Path) -> Result<(), String> {
    if !src.is_dir() {
        return Err(format!("copy source is not a directory: {}", src.display()));
    }
    fs::create_dir_all(dst).map_err(|e| format!("create copy destination failed: {e}"))?;
    for entry in fs::read_dir(src).map_err(|e| format!("read copy source failed: {e}"))? {
        let entry = entry.map_err(|e| format!("read copy entry failed: {e}"))?;
        let src_path = entry.path();
        let dst_path = dst.join(entry.file_name());
        let ty = entry
            .file_type()
            .map_err(|e| format!("read copy entry type failed: {e}"))?;
        if ty.is_dir() {
            copy_dir_recursive(&src_path, &dst_path)?;
        } else if ty.is_file() {
            if let Some(parent) = dst_path.parent() {
                fs::create_dir_all(parent)
                    .map_err(|e| format!("create copied file parent failed: {e}"))?;
            }
            fs::copy(&src_path, &dst_path).map_err(|e| {
                format!(
                    "copy file failed {} -> {}: {e}",
                    src_path.display(),
                    dst_path.display()
                )
            })?;
        }
    }
    Ok(())
}

fn restore_preserved_user_data(final_dir: &Path, preserve: &[String]) -> Result<(), String> {
    let marker = final_dir.with_extension("preserve_marker");
    if !marker.is_file() {
        return Ok(());
    }
    let raw =
        fs::read_to_string(&marker).map_err(|e| format!("read preserve marker failed: {e}"))?;
    let value: Value =
        serde_json::from_str(&raw).map_err(|e| format!("parse preserve marker failed: {e}"))?;
    let backup = value
        .get("backup")
        .and_then(Value::as_str)
        .map(PathBuf::from)
        .ok_or_else(|| "preserve marker missing backup path".to_string())?;
    for rel in local_preserve_paths(preserve) {
        let src = backup.join(&rel);
        if !src.exists() {
            continue;
        }
        let dst = final_dir.join(&rel);
        if dst.exists() {
            continue;
        }
        if let Some(parent) = dst.parent() {
            fs::create_dir_all(parent)
                .map_err(|e| format!("create preserved data parent failed: {e}"))?;
        }
        fs::rename(&src, &dst)
            .map_err(|e| format!("restore preserved data failed {}: {e}", rel.display()))?;
    }
    if backup.exists() {
        fs::remove_dir_all(&backup).map_err(|e| format!("remove preserve backup failed: {e}"))?;
    }
    let _ = fs::remove_file(marker);
    Ok(())
}

fn local_preserve_paths(preserve: &[String]) -> Vec<PathBuf> {
    preserve
        .iter()
        .map(|s| s.trim())
        .filter(|s| !s.is_empty())
        .filter(|s| {
            !s.starts_with('$')
                && !s.starts_with("localStorage:")
                && !s.starts_with("http://")
                && !s.starts_with("https://")
                && !Path::new(s).is_absolute()
        })
        .filter_map(|s| {
            let p = PathBuf::from(s.replace('\\', "/"));
            if p.components().any(|c| {
                matches!(
                    c,
                    std::path::Component::ParentDir
                        | std::path::Component::RootDir
                        | std::path::Component::Prefix(_)
                )
            }) {
                None
            } else {
                Some(p)
            }
        })
        .collect()
}

fn package_id_from_dir(path: &Path) -> Option<String> {
    read_package_meta(path).ok().and_then(|m| m.id).or_else(|| {
        let pj = path.join("plugin.json");
        let raw = fs::read_to_string(pj).ok()?;
        let v: Value = serde_json::from_str(raw.trim_start_matches('\u{feff}')).ok()?;
        v.get("id").and_then(Value::as_str).map(|s| s.to_string())
    })
}

fn package_version_from_dir(path: &Path) -> Option<String> {
    read_package_meta(path)
        .ok()
        .and_then(|m| m.version)
        .or_else(|| {
            let pj = path.join("plugin.json");
            let raw = fs::read_to_string(pj).ok()?;
            let v: Value = serde_json::from_str(raw.trim_start_matches('\u{feff}')).ok()?;
            v.get("version")
                .and_then(Value::as_str)
                .map(|s| s.to_string())
        })
}

fn read_package_meta(path: &Path) -> Result<PackageMeta, String> {
    let p = path.join(".jachin-package.json");
    let raw = fs::read_to_string(&p)
        .map_err(|e| format!("read package meta failed {}: {e}", p.display()))?;
    serde_json::from_str(raw.trim_start_matches('\u{feff}'))
        .map_err(|e| format!("parse package meta failed: {e}"))
}

fn download_package(url: &str, id: &str) -> Result<PathBuf, String> {
    let dir = download_dir();
    fs::create_dir_all(&dir).map_err(|e| format!("create download dir failed: {e}"))?;
    let path = dir.join(format!("{}-{}.zip", safe_id(id), timestamp_string()));
    let mut resp = http_client()?
        .get(url)
        .send()
        .map_err(|e| format!("download package failed: {e}"))?;
    if !resp.status().is_success() {
        return Err(format!(
            "download package failed HTTP {}",
            resp.status().as_u16()
        ));
    }
    let mut file =
        fs::File::create(&path).map_err(|e| format!("create package file failed: {e}"))?;
    std::io::copy(&mut resp, &mut file).map_err(|e| format!("write package file failed: {e}"))?;
    Ok(path)
}

fn extract_zip_archive(zip_path: &Path, dest: &Path) -> Result<(), String> {
    fs::create_dir_all(dest).map_err(|e| format!("create extract dir failed: {e}"))?;
    let file = fs::File::open(zip_path)
        .map_err(|e| format!("open zip failed {}: {e}", zip_path.display()))?;
    let mut archive = ZipArchive::new(file).map_err(|e| format!("read zip archive failed: {e}"))?;
    for i in 0..archive.len() {
        let mut entry = archive
            .by_index(i)
            .map_err(|e| format!("read zip entry #{i} failed: {e}"))?;
        let rel = entry
            .enclosed_name()
            .ok_or_else(|| format!("unsafe zip entry: {}", entry.name()))?
            .to_owned();
        let out = dest.join(rel);
        if entry.is_dir() {
            fs::create_dir_all(&out)
                .map_err(|e| format!("create extracted directory failed {}: {e}", out.display()))?;
            continue;
        }
        if let Some(parent) = out.parent() {
            fs::create_dir_all(parent).map_err(|e| {
                format!(
                    "create extracted file parent failed {}: {e}",
                    parent.display()
                )
            })?;
        }
        let mut output = fs::File::create(&out)
            .map_err(|e| format!("create extracted file failed {}: {e}", out.display()))?;
        std::io::copy(&mut entry, &mut output)
            .map_err(|e| format!("write extracted file failed {}: {e}", out.display()))?;
        #[cfg(unix)]
        if let Some(mode) = entry.unix_mode() {
            use std::os::unix::fs::PermissionsExt;
            fs::set_permissions(&out, fs::Permissions::from_mode(mode)).map_err(|e| {
                format!(
                    "set extracted file permissions failed {}: {e}",
                    out.display()
                )
            })?;
        }
    }
    Ok(())
}

fn sha256_file(path: &Path) -> Result<String, String> {
    let mut f = fs::File::open(path).map_err(|e| format!("open file for sha256 failed: {e}"))?;
    let mut hasher = Sha256::new();
    let mut buf = [0u8; 1024 * 64];
    loop {
        let n = f
            .read(&mut buf)
            .map_err(|e| format!("read file for sha256 failed: {e}"))?;
        if n == 0 {
            break;
        }
        hasher.update(&buf[..n]);
    }
    Ok(format!("{:x}", hasher.finalize()))
}

fn resolve_package_url(base_url: &str, raw: &str) -> Result<String, String> {
    let s = raw.trim();
    if s.starts_with("http://") || s.starts_with("https://") {
        return Ok(s.to_string());
    }
    let base = base_url.trim().trim_end_matches('/');
    if base.is_empty() {
        return Err("relative package_url requires L1 base URL".to_string());
    }
    Ok(format!("{}/{}", base, s.trim_start_matches('/')))
}

fn install_dir_for(kind: &str, id: &str) -> PathBuf {
    let base = match normalize_kind(kind).as_str() {
        "mcp" => l3_mcp_cache_dir(),
        "model" => model_cache_dir(),
        _ => l3_skill_cache_dir(),
    };
    base.join(safe_id(id))
}

fn source_store_dir_for(cfg: &L1DirectConfig, kind: &str, id: &str) -> PathBuf {
    let profile = cfg
        .profile_id
        .as_deref()
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(str::to_string)
        .unwrap_or_else(|| profile_id_for_base_url(&cfg.base_url));
    source_store_root()
        .join(safe_id(&profile))
        .join(normalize_kind(kind))
        .join(safe_id(id))
}

fn source_record_key_for(cfg: &L1DirectConfig, kind: &str, id: &str) -> String {
    source_record_key_from_parts(
        cfg.profile_id.as_deref(),
        kind,
        id,
        &Some(normalize_base_url(&cfg.base_url)),
    )
}

fn source_record_key_from_parts(
    profile_id: Option<&str>,
    kind: &str,
    id: &str,
    base_url: &Option<String>,
) -> String {
    let profile = profile_id
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(str::to_string)
        .or_else(|| base_url.as_deref().map(profile_id_for_base_url))
        .unwrap_or_else(|| "unknown_l1".to_string());
    format!(
        "{}/{}/{}",
        safe_id(&profile),
        normalize_kind(kind),
        safe_id(id)
    )
}

fn normalize_kind(raw: &str) -> String {
    let raw = raw.trim();
    if raw.eq_ignore_ascii_case("mcp") {
        return "mcp".to_string();
    }
    if raw.eq_ignore_ascii_case("model") {
        return "model".to_string();
    }
    "skill".to_string()
}

fn safe_id(id: &str) -> String {
    id.chars()
        .map(|c| match c {
            '\\' | '/' | ':' | '*' | '?' | '"' | '<' | '>' | '|' => '_',
            _ => c,
        })
        .collect()
}

fn read_l1_direct_config() -> L1DirectConfig {
    let profiles = read_l1_profiles_file();
    if let Some(active_id) = profiles.active_profile_id.as_deref() {
        if let Some(profile) = profiles.profiles.get(active_id) {
            return L1DirectConfig {
                base_url: normalize_base_url(&profile.base_url),
                developer_id: profile.developer_id.clone(),
                developer_token: profile.developer_token.clone(),
                visibility: profile.visibility.clone(),
                upload_by_default: profile.upload_by_default,
                profile_id: Some(profile.id.clone()),
                profile_name: Some(profile.name.clone()),
            };
        }
    }

    let mut cfg = read_legacy_l1_direct_config_raw();
    if cfg.base_url.trim().is_empty() {
        cfg.base_url = DEFAULT_L1_BASE_URL.to_string();
    }
    if cfg
        .developer_id
        .as_deref()
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .is_none()
    {
        cfg.developer_id = crate::nexus_config::l1_user_id();
    }
    if cfg
        .developer_token
        .as_deref()
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .is_none()
    {
        cfg.developer_token = crate::nexus_config::access_token();
    }
    cfg.base_url = normalize_base_url(&cfg.base_url);
    if cfg.profile_id.is_none() {
        cfg.profile_id = Some(profile_id_for_base_url(&cfg.base_url));
    }
    cfg
}

fn read_legacy_l1_direct_config_raw() -> L1DirectConfig {
    let path = legacy_l1_direct_config_path();
    if let Ok(raw) = fs::read_to_string(path) {
        serde_json::from_str(raw.trim_start_matches('\u{feff}')).unwrap_or_default()
    } else {
        L1DirectConfig::default()
    }
}

fn read_l1_profiles_file() -> L1ProfilesFile {
    let path = l1_profiles_path();
    let mut file = if let Ok(raw) = fs::read_to_string(&path) {
        serde_json::from_str(raw.trim_start_matches('\u{feff}')).unwrap_or_default()
    } else {
        L1ProfilesFile::default()
    };
    if file.profiles.is_empty() {
        let mut cfg = read_legacy_l1_direct_config_raw();
        if cfg.base_url.trim().is_empty() {
            cfg.base_url = DEFAULT_L1_BASE_URL.to_string();
        }
        if cfg
            .developer_id
            .as_deref()
            .map(str::trim)
            .filter(|s| !s.is_empty())
            .is_none()
        {
            cfg.developer_id = crate::nexus_config::l1_user_id();
        }
        if cfg
            .developer_token
            .as_deref()
            .map(str::trim)
            .filter(|s| !s.is_empty())
            .is_none()
        {
            cfg.developer_token = crate::nexus_config::access_token();
        }
        cfg.base_url = normalize_base_url(&cfg.base_url);
        let id = cfg
            .profile_id
            .clone()
            .unwrap_or_else(|| profile_id_for_base_url(&cfg.base_url));
        file.active_profile_id = Some(id.clone());
        file.profiles.insert(
            id.clone(),
            L1ProfileRecord {
                id,
                name: cfg
                    .profile_name
                    .clone()
                    .unwrap_or_else(|| default_profile_name(&cfg.base_url)),
                base_url: cfg.base_url,
                developer_id: cfg.developer_id,
                developer_token: cfg.developer_token,
                visibility: cfg.visibility,
                upload_by_default: cfg.upload_by_default,
            },
        );
        let _ = write_l1_profiles_file(&file);
    }
    if file.active_profile_id.is_none() {
        file.active_profile_id = file.profiles.keys().next().cloned();
    }
    file
}

fn write_l1_profiles_file(file: &L1ProfilesFile) -> Result<(), String> {
    let path = l1_profiles_path();
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|e| format!("create L1 profiles dir failed: {e}"))?;
    }
    let text = serde_json::to_string_pretty(file)
        .map_err(|e| format!("serialize L1 profiles failed: {e}"))?;
    fs::write(path, format!("{text}\n")).map_err(|e| format!("write L1 profiles failed: {e}"))
}

fn sync_active_profile_to_legacy_config(file: &L1ProfilesFile) -> Result<(), String> {
    let Some(active_id) = file.active_profile_id.as_deref() else {
        return Ok(());
    };
    let Some(profile) = file.profiles.get(active_id) else {
        return Ok(());
    };
    let cfg = L1DirectConfig {
        base_url: normalize_base_url(&profile.base_url),
        developer_id: profile.developer_id.clone(),
        developer_token: profile.developer_token.clone(),
        visibility: profile.visibility.clone(),
        upload_by_default: profile.upload_by_default,
        profile_id: Some(profile.id.clone()),
        profile_name: Some(profile.name.clone()),
    };
    write_legacy_l1_direct_config(&cfg)
}

fn write_legacy_l1_direct_config(config: &L1DirectConfig) -> Result<(), String> {
    let path = legacy_l1_direct_config_path();
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|e| format!("create L1 config dir failed: {e}"))?;
    }
    let text = serde_json::to_string_pretty(config)
        .map_err(|e| format!("serialize L1 config failed: {e}"))?;
    fs::write(path, format!("{text}\n")).map_err(|e| format!("write L1 config failed: {e}"))
}

fn profiles_result(file: &L1ProfilesFile) -> CapabilityL1ProfilesResult {
    let active = file.active_profile_id.clone();
    let mut profiles: Vec<CapabilityL1Profile> = file
        .profiles
        .values()
        .map(|profile| CapabilityL1Profile {
            id: profile.id.clone(),
            name: profile.name.clone(),
            base_url: normalize_base_url(&profile.base_url),
            developer_id: profile.developer_id.clone(),
            token_present: profile
                .developer_token
                .as_deref()
                .map(str::trim)
                .filter(|s| !s.is_empty())
                .is_some(),
            token_preview: profile
                .developer_token
                .as_deref()
                .map(mask_token)
                .filter(|s| !s.is_empty()),
            active: active.as_deref() == Some(profile.id.as_str()),
        })
        .collect();
    profiles.sort_by(|a, b| b.active.cmp(&a.active).then_with(|| a.name.cmp(&b.name)));
    CapabilityL1ProfilesResult {
        active_profile_id: active,
        profiles,
        config_path: l1_profiles_path().display().to_string(),
    }
}

fn normalize_base_url(raw: &str) -> String {
    raw.trim().trim_end_matches('/').to_string()
}

fn profile_id_for_base_url(base_url: &str) -> String {
    let normalized = normalize_base_url(base_url);
    let mut hasher = Sha256::new();
    hasher.update(normalized.as_bytes());
    let hex = format!("{:x}", hasher.finalize());
    format!("l1_{}", &hex[..12])
}

fn default_profile_name(base_url: &str) -> String {
    let base = normalize_base_url(base_url);
    if base == DEFAULT_L1_BASE_URL {
        "Jachin Cloud L1".to_string()
    } else {
        base.trim_start_matches("https://")
            .trim_start_matches("http://")
            .to_string()
    }
}

fn mask_token(raw: &str) -> String {
    let token = raw.trim();
    if token.len() <= 8 {
        return "***".to_string();
    }
    format!("{}***{}", &token[..3], &token[token.len() - 4..])
}

fn read_installed_registry() -> InstalledRegistry {
    let path = installed_registry_path();
    let Ok(raw) = fs::read_to_string(path) else {
        return InstalledRegistry::default();
    };
    serde_json::from_str(raw.trim_start_matches('\u{feff}')).unwrap_or_default()
}

fn write_installed_registry(registry: &InstalledRegistry) -> Result<(), String> {
    let path = installed_registry_path();
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|e| format!("create registry dir failed: {e}"))?;
    }
    let text = serde_json::to_string_pretty(registry)
        .map_err(|e| format!("serialize registry failed: {e}"))?;
    fs::write(path, format!("{text}\n")).map_err(|e| format!("write registry failed: {e}"))
}

fn record_matches_current_l1(rec: &InstalledRecord, cfg: &L1DirectConfig) -> bool {
    if rec.source != "l1" {
        return false;
    }
    let Some(source_base) = rec.source_l1_base_url.as_deref() else {
        return true;
    };
    normalize_base_url(source_base).eq_ignore_ascii_case(&normalize_base_url(&cfg.base_url))
}

fn http_client() -> Result<reqwest::blocking::Client, String> {
    reqwest::blocking::Client::builder()
        .timeout(Duration::from_secs(45))
        .build()
        .map_err(|e| format!("create HTTP client failed: {e}"))
}

fn timestamp_string() -> String {
    let secs = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or_default();
    secs.to_string()
}

fn jachin_home_dir() -> PathBuf {
    if let Ok(raw) = std::env::var("JACHIN_HOME") {
        let p = PathBuf::from(raw);
        if !p.as_os_str().is_empty() {
            return p;
        }
    }
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

fn installed_registry_path() -> PathBuf {
    jachin_home_dir()
        .join("capabilities")
        .join("installed.json")
}

fn l1_profiles_path() -> PathBuf {
    jachin_home_dir().join("l1_profiles.json")
}

fn legacy_l1_direct_config_path() -> PathBuf {
    jachin_home_dir().join("l1_direct_publish.json")
}

fn l3_mcp_cache_dir() -> PathBuf {
    jachin_home_dir().join("l3_mcp_cache")
}

fn l3_skill_cache_dir() -> PathBuf {
    jachin_home_dir().join("l3_skill_cache")
}

fn model_cache_dir() -> PathBuf {
    jachin_home_dir().join("models")
}

fn source_store_root() -> PathBuf {
    jachin_home_dir().join("capability_sources")
}

fn legacy_skills_dir() -> PathBuf {
    jachin_home_dir().join("skills")
}

fn download_dir() -> PathBuf {
    jachin_home_dir().join("capabilities").join("downloads")
}

fn disabled_dir() -> PathBuf {
    jachin_home_dir().join("capabilities").join("disabled")
}

fn staging_dir(id: &str) -> PathBuf {
    jachin_home_dir()
        .join("capabilities")
        .join("staging")
        .join(format!("{}-{}", safe_id(id), timestamp_string()))
}
