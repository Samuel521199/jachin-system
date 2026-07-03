use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::collections::{BTreeMap, HashMap, HashSet};
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

const CORE_BUNDLED_SKILL_IDS: &[&str] = &[
    "com.jachin.calendar",
    "com.jachin.files",
    "com.jachin.os-mate",
    "com.jachin.voip",
];

const CORE_MCP_PACKAGE_IDS: &[&str] = &[
    "com.jachin.mcp.official.fetch",
    "com.jachin.mcp.official.filesystem.dirs",
    "com.jachin.mcp.official.git",
    "com.jachin.mcp.official.memory.npx",
    "com.jachin.mcp.official.playwright",
    "com.jachin.mcp.official.puppeteer",
    "com.jachin.mcp.official.sqlite.npx",
    "com.jachin.mcp.official.time",
    "com.jachin.mcp.office.word",
    "com.jachin.mcp.playwright.browser",
    "com.jachin.mcp.sendmail.smtp",
    "com.jachin.mcp.tavily.search",
];

const BUSINESS_SKILL_IDS: &[&str] = &[
    "com.jachin.skill.bi-growth-officer",
    "com.jachin.skill.pmo-copilot",
    "com.jachin.skill.ai-recruiting-director",
    "com.jachin.skill.desktop-execution-agent",
    "com.jachin.skill.game-qa-automation",
    "com.jachin.skill.english-learning-assistant",
];

#[derive(Debug, Clone, Serialize)]
pub struct CapabilityPackageInfo {
    pub id: String,
    pub name: String,
    pub description: Option<String>,
    pub version: String,
    pub kind: String,
    pub tier: String,
    pub path: String,
    pub manifest_path: Option<String>,
    pub portable: bool,
    pub published: bool,
    pub published_version: Option<String>,
    pub last_published_at: Option<String>,
    pub package_path: Option<String>,
    pub sha256_path: Option<String>,
    pub status: String,
    pub l1_published: bool,
    pub l1_version: Option<String>,
    pub l1_review_status: Option<String>,
    pub l1_package_url: Option<String>,
    pub problems: Vec<String>,
}

#[derive(Debug, Clone, Serialize)]
pub struct CapabilityPublishScan {
    pub root: String,
    pub state_path: String,
    pub output_dir: String,
    pub l1_direct: CapabilityL1DirectProfile,
    pub packages: Vec<CapabilityPackageInfo>,
    pub counts: BTreeMap<String, usize>,
}

#[derive(Debug, Clone, Serialize)]
pub struct CapabilityPublishResult {
    pub ok: bool,
    pub id: String,
    pub version: String,
    pub package_path: String,
    pub sha256_path: Option<String>,
    pub published_at: String,
    pub uploaded_to_l1: bool,
    pub l1_status: Option<String>,
    pub l1_response: Option<Value>,
    pub dependency_results: Vec<CapabilityPublishDependencyResult>,
    pub message: String,
}

#[derive(Debug, Clone, Serialize)]
pub struct CapabilityPublishDependencyResult {
    pub id: String,
    pub version: String,
    pub kind: String,
    pub package_path: String,
    pub uploaded_to_l1: bool,
    pub l1_status: Option<String>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct CapabilityPublishInput {
    pub path: String,
    pub version: Option<String>,
    pub notes: Option<String>,
    pub upload_to_l1: Option<bool>,
    pub l1_base_url: Option<String>,
    pub l1_token: Option<String>,
    pub visibility: Option<String>,
}

#[derive(Debug, Clone, Serialize)]
pub struct CapabilityL1DirectProfile {
    pub config_path: String,
    pub base_url: String,
    pub developer_id: String,
    pub token_present: bool,
    pub token_preview: Option<String>,
    pub visibility: String,
    pub upload_by_default: bool,
    pub l2_required: bool,
}

#[derive(Debug, Clone, Deserialize)]
pub struct CapabilityL1DirectProfileInput {
    pub base_url: String,
    pub developer_id: Option<String>,
    pub token: Option<String>,
    pub clear_token: Option<bool>,
    pub visibility: Option<String>,
    pub upload_by_default: Option<bool>,
}

#[derive(Debug, Clone, Serialize)]
pub struct CapabilityL1DirectTestResult {
    pub ok: bool,
    pub base_url: String,
    pub developer_id: Option<String>,
    pub catalog_reachable: bool,
    pub developer_items_count: Option<usize>,
    pub message: String,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
struct L1DirectConfig {
    base_url: String,
    developer_id: Option<String>,
    developer_token: Option<String>,
    visibility: Option<String>,
    upload_by_default: Option<bool>,
}

const DEFAULT_L1_BASE_URL: &str = "http://localhost:3000";

#[derive(Debug, Clone, Default)]
struct RemotePackageRecord {
    version: String,
    review_status: Option<String>,
    package_url: Option<String>,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
struct PublishState {
    packages: HashMap<String, PublishRecord>,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
struct PublishRecord {
    version: String,
    package_path: String,
    sha256_path: Option<String>,
    published_at: String,
    notes: Option<String>,
    uploaded_to_l1: bool,
    l1_status: Option<String>,
}

#[derive(Debug, Clone)]
struct InternalPublishOutput {
    info: CapabilityPackageInfo,
    package_path: String,
    sha256_path: Option<String>,
    uploaded_to_l1: bool,
    l1_status: Option<String>,
    l1_response: Option<Value>,
}

#[tauri::command]
pub fn capability_publish_scan() -> Result<CapabilityPublishScan, String> {
    let root = project_root()?;
    let state_path = state_path(&root);
    let output_dir = package_output_dir(&root);
    let state = read_state(&state_path);
    let l1_config = read_l1_direct_config();
    let remote_packages = fetch_l1_developer_packages(&l1_config).unwrap_or_default();
    let mut packages = scan_capability_packages(&root, &state)?;
    for pkg in &mut packages {
        if let Some(remote) = remote_packages.get(&pkg.id) {
            pkg.l1_published = true;
            pkg.l1_version = Some(remote.version.clone());
            pkg.l1_review_status = remote.review_status.clone();
            pkg.l1_package_url = remote.package_url.clone();
            if remote.version == pkg.version {
                pkg.published = true;
                pkg.published_version = Some(remote.version.clone());
                if pkg.status == "unpublished" {
                    pkg.status = "published".to_string();
                }
            } else if pkg.status == "unpublished" || pkg.status == "published" {
                pkg.status = "update_available".to_string();
            }
        }
    }
    packages.sort_by(|a, b| {
        business_skill_rank(&a.id)
            .cmp(&business_skill_rank(&b.id))
            .then_with(|| {
                if a.kind == "skill" && b.kind != "skill" {
                    std::cmp::Ordering::Less
                } else if a.kind != "skill" && b.kind == "skill" {
                    std::cmp::Ordering::Greater
                } else {
                    std::cmp::Ordering::Equal
                }
            })
            .then_with(|| {
                a.tier
                    .cmp(&b.tier)
                    .then_with(|| a.kind.cmp(&b.kind))
                    .then_with(|| a.id.cmp(&b.id))
            })
    });

    let mut counts = BTreeMap::new();
    counts.insert("total".to_string(), packages.len());
    counts.insert(
        "published".to_string(),
        packages.iter().filter(|p| p.status == "published").count(),
    );
    counts.insert(
        "unpublished".to_string(),
        packages
            .iter()
            .filter(|p| p.status == "unpublished")
            .count(),
    );
    counts.insert(
        "update_available".to_string(),
        packages
            .iter()
            .filter(|p| p.status == "update_available")
            .count(),
    );
    counts.insert(
        "blocked".to_string(),
        packages.iter().filter(|p| !p.problems.is_empty()).count(),
    );

    Ok(CapabilityPublishScan {
        root: root.display().to_string(),
        state_path: state_path.display().to_string(),
        output_dir: output_dir.display().to_string(),
        l1_direct: l1_profile_from_config(&l1_config),
        packages,
        counts,
    })
}

#[tauri::command]
pub fn capability_publish_l1_direct_get() -> Result<CapabilityL1DirectProfile, String> {
    Ok(l1_profile_from_config(&read_l1_direct_config()))
}

#[tauri::command]
pub fn capability_publish_l1_direct_set(
    input: CapabilityL1DirectProfileInput,
) -> Result<CapabilityL1DirectProfile, String> {
    let base_url = input.base_url.trim().trim_end_matches('/').to_string();
    if base_url.is_empty() {
        return Err("L1 base URL is required".to_string());
    }
    if !base_url.starts_with("http://") && !base_url.starts_with("https://") {
        return Err("L1 base URL must start with http:// or https://".to_string());
    }

    let mut previous = read_l1_direct_config();
    previous.base_url = base_url;
    previous.developer_id = input
        .developer_id
        .as_deref()
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(|s| s.to_string());
    if input.clear_token.unwrap_or(false) {
        previous.developer_token = None;
    } else if let Some(token) = input
        .token
        .as_deref()
        .map(str::trim)
        .filter(|s| !s.is_empty())
    {
        previous.developer_token = Some(token.to_string());
    }
    previous.visibility = Some(input.visibility.unwrap_or_else(|| "PUBLIC".to_string()));
    previous.upload_by_default = Some(input.upload_by_default.unwrap_or(true));
    write_l1_direct_config(&previous)?;
    Ok(l1_profile_from_config(&previous))
}

#[tauri::command]
pub fn capability_publish_l1_direct_test() -> Result<CapabilityL1DirectTestResult, String> {
    let config = read_l1_direct_config();
    test_l1_direct_config(&config)
}

#[tauri::command]
pub fn capability_publish_package(
    input: CapabilityPublishInput,
) -> Result<CapabilityPublishResult, String> {
    let root = project_root()?;
    let source = normalize_inside_root(&root, &input.path)?;
    if !source.is_dir() {
        return Err(format!("能力包目录不存在: {}", source.display()));
    }

    let primary_version = input
        .version
        .as_deref()
        .map(str::trim)
        .filter(|s| !s.is_empty());
    if let Some(version) = primary_version {
        validate_semver(version)?;
    }
    let preview = read_package_info(&root, &source, &PublishState::default())?;
    if !preview.portable {
        return Err(format!(
            "能力包暂不可发布 {}: {}",
            preview.id,
            preview.problems.join("; ")
        ));
    }
    let published_at = timestamp_string();

    let dependency_plan = if preview.kind == "skill" {
        collect_dependency_publish_plan(&root, &source)?
    } else {
        Vec::new()
    };

    let mut dependency_outputs = Vec::new();
    for dep in dependency_plan {
        let dep_source = normalize_inside_root(&root, &dep.path)?;
        let output = package_and_upload_capability(&root, &dep_source, None, &input)?;
        dependency_outputs.push(output);
    }

    let primary_output = package_and_upload_capability(&root, &source, primary_version, &input)?;

    let mut state = read_state(&state_path(&root));
    for dep in &dependency_outputs {
        state.packages.insert(
            dep.info.id.clone(),
            PublishRecord {
                version: dep.info.version.clone(),
                package_path: dep.package_path.clone(),
                sha256_path: dep.sha256_path.clone(),
                published_at: published_at.clone(),
                notes: Some(format!(
                    "auto-published dependency for {}",
                    primary_output.info.id
                )),
                uploaded_to_l1: dep.uploaded_to_l1,
                l1_status: dep.l1_status.clone(),
            },
        );
    }
    state.packages.insert(
        primary_output.info.id.clone(),
        PublishRecord {
            version: primary_output.info.version.clone(),
            package_path: primary_output.package_path.clone(),
            sha256_path: primary_output.sha256_path.clone(),
            published_at: published_at.clone(),
            notes: input.notes,
            uploaded_to_l1: primary_output.uploaded_to_l1,
            l1_status: primary_output.l1_status.clone(),
        },
    );
    write_state(&state_path(&root), &state)?;

    let dependency_results = dependency_outputs
        .iter()
        .map(|dep| CapabilityPublishDependencyResult {
            id: dep.info.id.clone(),
            version: dep.info.version.clone(),
            kind: dep.info.kind.clone(),
            package_path: dep.package_path.clone(),
            uploaded_to_l1: dep.uploaded_to_l1,
            l1_status: dep.l1_status.clone(),
        })
        .collect::<Vec<_>>();

    let message = if dependency_results.is_empty() {
        if primary_output.uploaded_to_l1 {
            "已打包并上传到 L1，发布记录已更新".to_string()
        } else {
            "已生成 L1 发布包并记录本地发布状态".to_string()
        }
    } else if primary_output.uploaded_to_l1 {
        format!(
            "已级联发布 {} 个依赖，并上传业务 Skill 到 L1",
            dependency_results.len()
        )
    } else {
        format!(
            "已级联打包 {} 个依赖，并生成业务 Skill 发布包",
            dependency_results.len()
        )
    };

    Ok(CapabilityPublishResult {
        ok: true,
        id: primary_output.info.id,
        version: primary_output.info.version,
        package_path: primary_output.package_path,
        sha256_path: primary_output.sha256_path,
        published_at,
        uploaded_to_l1: primary_output.uploaded_to_l1,
        l1_status: primary_output.l1_status,
        l1_response: primary_output.l1_response,
        dependency_results,
        message,
    })
}

#[tauri::command]
pub fn capability_publish_open_path(path: String) -> Result<(), String> {
    let p = PathBuf::from(path);
    if !p.exists() {
        return Err(format!("路径不存在: {}", p.display()));
    }
    let mut cmd = Command::new("explorer");
    if p.is_file() {
        cmd.arg(format!("/select,{}", p.display()));
    } else {
        cmd.arg(&p);
    }
    cmd.spawn()
        .map(|_| ())
        .map_err(|e| format!("打开资源管理器失败: {e}"))
}

fn package_and_upload_capability(
    root: &Path,
    source: &Path,
    version: Option<&str>,
    input: &CapabilityPublishInput,
) -> Result<InternalPublishOutput, String> {
    if let Some(version) = version.map(str::trim).filter(|s| !s.is_empty()) {
        validate_semver(version)?;
        set_package_version(source, version)?;
    }

    let info = read_package_info(root, source, &PublishState::default())?;
    if !info.portable {
        return Err(format!(
            "能力包暂不可发布 {}: {}",
            info.id,
            info.problems.join("; ")
        ));
    }

    let out_dir = package_output_dir(root);
    fs::create_dir_all(&out_dir).map_err(|e| format!("创建输出目录失败: {e}"))?;
    let script = root.join("scripts").join("package_l1_capability.py");
    if !script.exists() {
        return Err(format!("找不到打包脚本: {}", script.display()));
    }

    let output = Command::new("python")
        .arg(&script)
        .arg(source)
        .arg("--out")
        .arg(&out_dir)
        .current_dir(root)
        .output()
        .map_err(|e| format!("启动打包脚本失败: {e}"))?;
    let stdout = String::from_utf8_lossy(&output.stdout);
    let stderr = String::from_utf8_lossy(&output.stderr);
    if !output.status.success() {
        return Err(format!(
            "打包失败 {}: {}\n{}",
            info.id,
            stdout.trim(),
            stderr.trim()
        ));
    }

    let package_path = parse_prefixed_path(&stdout, "Packaged:")
        .ok_or_else(|| format!("打包脚本没有返回 Packaged 路径:\n{stdout}"))?;
    let sha256_path = parse_prefixed_path(&stdout, "SHA256:");

    let mut uploaded_to_l1 = false;
    let mut l1_status = None;
    let mut l1_response = None;
    if input.upload_to_l1.unwrap_or(false) {
        let l1_config = read_l1_direct_config();
        let base_owned = input
            .l1_base_url
            .as_deref()
            .map(str::trim)
            .filter(|s| !s.is_empty())
            .map(|s| s.trim_end_matches('/').to_string())
            .or_else(|| {
                let s = l1_config.base_url.trim().trim_end_matches('/').to_string();
                if s.is_empty() {
                    None
                } else {
                    Some(s)
                }
            })
            .ok_or_else(|| "L1 upload is enabled, but L1 base URL is missing".to_string())?;
        let token_owned = input
            .l1_token
            .as_deref()
            .map(str::trim)
            .filter(|s| !s.is_empty())
            .map(|s| s.to_string())
            .or_else(|| l1_config.developer_token.clone())
            .ok_or_else(|| "L1 upload is enabled, but Developer Token is missing".to_string())?;
        let response = upload_package_to_l1(
            &base_owned,
            &token_owned,
            Path::new(&package_path),
            input
                .visibility
                .as_deref()
                .or(l1_config.visibility.as_deref())
                .unwrap_or("PUBLIC"),
        )?;
        uploaded_to_l1 = true;
        l1_status = response
            .get("status")
            .and_then(Value::as_str)
            .map(|s| s.to_string())
            .or_else(|| {
                response
                    .get("message")
                    .and_then(Value::as_str)
                    .map(|s| s.to_string())
            });
        l1_response = Some(response);
    }

    Ok(InternalPublishOutput {
        info,
        package_path,
        sha256_path,
        uploaded_to_l1,
        l1_status,
        l1_response,
    })
}

fn collect_dependency_publish_plan(
    root: &Path,
    source: &Path,
) -> Result<Vec<CapabilityPackageInfo>, String> {
    let state = PublishState::default();
    let packages = scan_capability_packages(root, &state)?;
    let by_id = packages
        .into_iter()
        .map(|pkg| (pkg.id.to_lowercase(), pkg))
        .collect::<HashMap<_, _>>();
    let primary_info = read_package_info(root, source, &state)?;
    let mut out = Vec::new();
    let mut visiting = HashSet::new();
    let mut visited = HashSet::new();
    visit_package_dependencies(
        root,
        source,
        &primary_info.id,
        &by_id,
        &mut visiting,
        &mut visited,
        &mut out,
    )?;
    Ok(out)
}

fn visit_package_dependencies(
    root: &Path,
    source: &Path,
    root_id: &str,
    by_id: &HashMap<String, CapabilityPackageInfo>,
    visiting: &mut HashSet<String>,
    visited: &mut HashSet<String>,
    out: &mut Vec<CapabilityPackageInfo>,
) -> Result<(), String> {
    let info = read_package_info(root, source, &PublishState::default())?;
    let current = info.id.to_lowercase();
    if !visiting.insert(current.clone()) {
        return Err(format!("能力依赖存在循环引用: {}", info.id));
    }

    for dep_raw in read_package_dependency_ids(source)? {
        let dep_id = normalize_dependency_id(&dep_raw);
        if dep_id.is_empty() {
            continue;
        }
        let dep_key = dep_id.to_lowercase();
        if dep_key == root_id.to_lowercase() {
            return Err(format!("业务 Skill 依赖指向自身: {dep_id}"));
        }
        if visited.contains(&dep_key) {
            continue;
        }
        let Some(dep_info) = by_id.get(&dep_key).cloned() else {
            return Err(format!(
                "业务 Skill 依赖未找到本地能力包: {dep_id}。请先在 skills_repo/l1_upload_stubs、l3_client/local_mcps 或 models_repo 中补齐该依赖。"
            ));
        };
        if !dep_info.portable {
            return Err(format!(
                "依赖能力包不可发布 {}: {}",
                dep_info.id,
                dep_info.problems.join("; ")
            ));
        }
        let dep_source = normalize_inside_root(root, &dep_info.path)?;
        visit_package_dependencies(root, &dep_source, root_id, by_id, visiting, visited, out)?;
        visited.insert(dep_key);
        out.push(dep_info);
    }
    visiting.remove(&current);
    Ok(())
}

fn read_package_dependency_ids(source: &Path) -> Result<Vec<String>, String> {
    let plugin_path = source.join("plugin.json");
    if !plugin_path.exists() {
        return Ok(Vec::new());
    }
    let text = read_text_strip_bom(&plugin_path)?;
    let value: Value = serde_json::from_str(&text)
        .map_err(|e| format!("解析 plugin.json 失败 {}: {e}", plugin_path.display()))?;
    let mut out = Vec::new();
    collect_json_string_list(value.get("required_mcps"), &mut out);
    collect_json_string_list(value.get("dependencies"), &mut out);
    collect_json_string_list(value.get("required_models"), &mut out);
    let mut seen = HashSet::new();
    out.retain(|item| seen.insert(normalize_dependency_id(item).to_lowercase()));
    Ok(out)
}

fn collect_json_string_list(raw: Option<&Value>, out: &mut Vec<String>) {
    match raw {
        Some(Value::String(s)) => out.push(s.trim().to_string()),
        Some(Value::Array(items)) => {
            for item in items {
                if let Some(s) = item.as_str().map(str::trim).filter(|s| !s.is_empty()) {
                    out.push(s.to_string());
                }
            }
        }
        _ => {}
    }
}

fn normalize_dependency_id(raw: &str) -> String {
    raw.trim()
        .trim_start_matches("mcp:")
        .trim_start_matches("MCP:")
        .trim_start_matches("model:")
        .trim_start_matches("MODEL:")
        .trim()
        .to_string()
}

fn project_root() -> Result<PathBuf, String> {
    if let Ok(raw) = std::env::var("JACHIN_APP_ROOT") {
        let p = PathBuf::from(raw);
        if p.join("skills_repo").exists() {
            return p
                .canonicalize()
                .map_err(|e| format!("解析 JACHIN_APP_ROOT 失败: {e}"));
        }
    }
    let mut cur = std::env::current_dir().map_err(|e| format!("读取当前目录失败: {e}"))?;
    loop {
        if cur.join("skills_repo").exists() && cur.join("scripts").exists() {
            return cur
                .canonicalize()
                .map_err(|e| format!("解析项目根目录失败: {e}"));
        }
        if !cur.pop() {
            break;
        }
    }
    Err("无法定位 Jachin 项目根目录".to_string())
}

fn state_path(root: &Path) -> PathBuf {
    root.join("output").join("capability_publish_state.json")
}

fn package_output_dir(root: &Path) -> PathBuf {
    root.join("output").join("l1_capability_packages")
}

fn normalize_inside_root(root: &Path, raw: &str) -> Result<PathBuf, String> {
    let p = PathBuf::from(raw);
    let full = if p.is_absolute() { p } else { root.join(p) };
    let canonical = full
        .canonicalize()
        .map_err(|e| format!("解析路径失败 {}: {e}", full.display()))?;
    if !canonical.starts_with(root) {
        return Err(format!("只能发布项目内能力包: {}", canonical.display()));
    }
    Ok(canonical)
}

fn read_state(path: &Path) -> PublishState {
    fs::read_to_string(path)
        .ok()
        .and_then(|s| serde_json::from_str::<PublishState>(&s).ok())
        .unwrap_or_default()
}

fn write_state(path: &Path, state: &PublishState) -> Result<(), String> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|e| format!("创建状态目录失败: {e}"))?;
    }
    let text =
        serde_json::to_string_pretty(state).map_err(|e| format!("序列化发布状态失败: {e}"))?;
    fs::write(path, text).map_err(|e| format!("写入发布状态失败: {e}"))
}

fn scan_capability_packages(
    root: &Path,
    state: &PublishState,
) -> Result<Vec<CapabilityPackageInfo>, String> {
    let roots = [
        root.join("skills_repo"),
        root.join("l3_client").join("local_mcps"),
        root.join("models_repo"),
    ];
    let mut found = Vec::new();
    for scan_root in roots {
        if !scan_root.exists() {
            continue;
        }
        scan_dir(root, &scan_root, state, &mut found, 0)?;
    }
    Ok(found)
}

fn scan_dir(
    root: &Path,
    dir: &Path,
    state: &PublishState,
    found: &mut Vec<CapabilityPackageInfo>,
    depth: usize,
) -> Result<(), String> {
    if depth > 5 || should_skip_dir(dir) {
        return Ok(());
    }
    if is_capability_package_dir(dir) {
        found.push(read_package_info(root, dir, state)?);
        return Ok(());
    }
    for entry in fs::read_dir(dir).map_err(|e| format!("扫描目录失败 {}: {e}", dir.display()))?
    {
        let entry = entry.map_err(|e| format!("读取目录项失败: {e}"))?;
        let path = entry.path();
        if path.is_dir() {
            scan_dir(root, &path, state, found, depth + 1)?;
        }
    }
    Ok(())
}

fn should_skip_dir(dir: &Path) -> bool {
    let name = dir.file_name().and_then(|s| s.to_str()).unwrap_or("");
    matches!(
        name,
        ".git"
            | ".pytest_cache"
            | "__pycache__"
            | "node_modules"
            | "dist"
            | "build"
            | "target"
            | "output"
            | "data"
            | "logs"
    )
}

fn is_capability_package_dir(dir: &Path) -> bool {
    dir.join("plugin.json").exists()
        || dir.join("manifest.yaml").exists()
        || dir.join("SKILL.md").exists()
        || dir.join("server.py").exists()
        || dir.join("__main__.py").exists()
}

fn read_package_info(
    root: &Path,
    dir: &Path,
    state: &PublishState,
) -> Result<CapabilityPackageInfo, String> {
    let mut problems = Vec::new();
    let manifest_path;
    let mut id = dir
        .file_name()
        .and_then(|s| s.to_str())
        .unwrap_or("unknown")
        .to_string();
    let mut name = id.clone();
    let mut description: Option<String> = None;
    let mut version = "0.0.0".to_string();
    let mut kind = "skill".to_string();

    if dir.join("plugin.json").exists() {
        manifest_path = Some(dir.join("plugin.json"));
        let text = read_text_strip_bom(&dir.join("plugin.json"))?;
        let value: Value = serde_json::from_str(&text)
            .map_err(|e| format!("解析 plugin.json 失败 {}: {e}", dir.display()))?;
        id = value
            .get("id")
            .or_else(|| value.get("plugin_id"))
            .and_then(Value::as_str)
            .unwrap_or(&id)
            .to_string();
        name = value
            .get("name")
            .and_then(Value::as_str)
            .unwrap_or(&name)
            .to_string();
        description = value
            .get("description")
            .and_then(Value::as_str)
            .map(|s| s.trim().to_string())
            .filter(|s| !s.is_empty());
        version = value
            .get("version")
            .and_then(Value::as_str)
            .unwrap_or(&version)
            .to_string();
        let item_type = value
            .get("item_type")
            .or_else(|| value.get("type"))
            .and_then(Value::as_str)
            .unwrap_or("plugin")
            .to_lowercase();
        kind = if item_type == "mcp" {
            "mcp"
        } else if item_type == "model" {
            "model"
        } else {
            "skill"
        }
        .to_string();
        if kind == "model" {
            validate_model_package_files(dir, &value, &mut problems);
        }
        if value
            .get("version")
            .and_then(Value::as_str)
            .unwrap_or("")
            .is_empty()
        {
            problems.push("plugin.json 缺少 version".to_string());
        }
    } else if dir.join("manifest.yaml").exists() {
        manifest_path = Some(dir.join("manifest.yaml"));
        let yaml = read_yaml_summary(&dir.join("manifest.yaml"))?;
        id = yaml
            .get("id")
            .or_else(|| yaml.get("name"))
            .cloned()
            .unwrap_or(id);
        name = yaml
            .get("display_name")
            .or_else(|| yaml.get("name"))
            .cloned()
            .unwrap_or(name);
        description = yaml
            .get("description")
            .cloned()
            .map(|s| s.trim().to_string())
            .filter(|s| !s.is_empty());
        version = yaml.get("version").cloned().unwrap_or(version);
        kind = "skill".to_string();
    } else if dir.join("SKILL.md").exists() {
        manifest_path = Some(dir.join("SKILL.md"));
        name = read_skill_title(&dir.join("SKILL.md")).unwrap_or_else(|| name.clone());
        kind = "skill".to_string();
    } else {
        manifest_path = None;
        kind = "mcp".to_string();
        id = format!("local.mcp.{}", id);
        problems
            .push("本地 MCP 缺少 plugin.json / manifest.yaml；请补齐清单后再发布到 L1".to_string());
    }

    if !is_semver(&version) {
        problems.push(format!("版本号不是语义化版本: {version}"));
    }
    let tier = package_tier(&id);
    let portable = problems.is_empty();
    let record = state.packages.get(&id);
    let (published, published_version, last_published_at, package_path, sha256_path) =
        if let Some(r) = record {
            (
                true,
                Some(r.version.clone()),
                Some(r.published_at.clone()),
                Some(r.package_path.clone()),
                r.sha256_path.clone(),
            )
        } else {
            (false, None, None, None, None)
        };
    let status = if !published {
        "unpublished"
    } else if published_version.as_deref() == Some(version.as_str()) {
        "published"
    } else {
        "update_available"
    }
    .to_string();

    let path = dir.strip_prefix(root).unwrap_or(dir).display().to_string();
    Ok(CapabilityPackageInfo {
        id,
        name,
        description,
        version,
        kind,
        tier,
        path,
        manifest_path: manifest_path.map(|p| p.display().to_string()),
        portable,
        published,
        published_version,
        last_published_at,
        package_path,
        sha256_path,
        status,
        l1_published: false,
        l1_version: None,
        l1_review_status: None,
        l1_package_url: None,
        problems,
    })
}

fn validate_model_package_files(dir: &Path, manifest: &Value, problems: &mut Vec<String>) {
    let required = manifest
        .get("model_asset")
        .and_then(|v| v.get("required_files"))
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    if required.is_empty() {
        problems
            .push("MODEL 包缺少 model_asset.required_files，无法确认下载后是否可用".to_string());
        return;
    }
    for item in required {
        let Some(rel) = item.as_str().map(str::trim).filter(|s| !s.is_empty()) else {
            continue;
        };
        let path = dir.join(rel.replace('/', std::path::MAIN_SEPARATOR_STR));
        if !path.is_file() {
            problems.push(format!(
                "MODEL 包缺少模型文件: {rel}；请先运行模型准备脚本再发布"
            ));
        }
    }
}

fn package_tier(id: &str) -> String {
    let lower = id.to_lowercase();
    if CORE_BUNDLED_SKILL_IDS.iter().any(|x| *x == lower.as_str())
        || CORE_MCP_PACKAGE_IDS.iter().any(|x| *x == lower.as_str())
    {
        "core".to_string()
    } else if BUSINESS_SKILL_IDS.iter().any(|x| *x == lower.as_str()) {
        "business".to_string()
    } else {
        "extension".to_string()
    }
}

fn business_skill_rank(id: &str) -> usize {
    let lower = id.to_lowercase();
    BUSINESS_SKILL_IDS
        .iter()
        .position(|x| *x == lower.as_str())
        .unwrap_or(usize::MAX)
}

fn read_text_strip_bom(path: &Path) -> Result<String, String> {
    let mut text =
        fs::read_to_string(path).map_err(|e| format!("读取文件失败 {}: {e}", path.display()))?;
    if text.starts_with('\u{feff}') {
        text.remove(0);
    }
    Ok(text)
}

fn read_yaml_summary(path: &Path) -> Result<HashMap<String, String>, String> {
    let text = read_text_strip_bom(path)?;
    let mut out = HashMap::new();
    for line in text.lines() {
        let trimmed = line.trim();
        if trimmed.starts_with('#') || !trimmed.contains(':') {
            continue;
        }
        let mut parts = trimmed.splitn(2, ':');
        let key = parts.next().unwrap_or("").trim();
        let value = parts
            .next()
            .unwrap_or("")
            .trim()
            .trim_matches('"')
            .trim_matches('\'')
            .to_string();
        if matches!(
            key,
            "id" | "name" | "display_name" | "version" | "description"
        ) && !value.is_empty()
        {
            out.insert(key.to_string(), value);
        }
    }
    Ok(out)
}

fn read_skill_title(path: &Path) -> Option<String> {
    let text = read_text_strip_bom(path).ok()?;
    for line in text.lines() {
        let s = line.trim();
        if let Some(title) = s.strip_prefix("# ") {
            return Some(title.trim().to_string());
        }
    }
    None
}

fn set_package_version(source: &Path, version: &str) -> Result<(), String> {
    let plugin_path = source.join("plugin.json");
    if plugin_path.exists() {
        let text = read_text_strip_bom(&plugin_path)?;
        let mut value: Value = serde_json::from_str(&text)
            .map_err(|e| format!("解析 plugin.json 失败 {}: {e}", plugin_path.display()))?;
        let obj = value
            .as_object_mut()
            .ok_or_else(|| "plugin.json 顶层必须是对象".to_string())?;
        obj.insert("version".to_string(), json!(version));
        let pretty = serde_json::to_string_pretty(&value)
            .map_err(|e| format!("序列化 plugin.json 失败: {e}"))?;
        fs::write(&plugin_path, format!("{pretty}\n"))
            .map_err(|e| format!("写入 plugin.json 失败 {}: {e}", plugin_path.display()))?;
        return Ok(());
    }

    let manifest_path = source.join("manifest.yaml");
    if manifest_path.exists() {
        let text = read_text_strip_bom(&manifest_path)?;
        let mut replaced = false;
        let lines = text
            .lines()
            .map(|line| {
                if line.trim_start().starts_with("version:") {
                    replaced = true;
                    let indent = line
                        .chars()
                        .take_while(|c| c.is_whitespace())
                        .collect::<String>();
                    format!("{indent}version: \"{version}\"")
                } else {
                    line.to_string()
                }
            })
            .collect::<Vec<_>>();
        let next = if replaced {
            lines.join("\n")
        } else {
            format!("{}\nversion: \"{}\"", text.trim_end(), version)
        };
        fs::write(&manifest_path, format!("{next}\n"))
            .map_err(|e| format!("写入 manifest.yaml 失败 {}: {e}", manifest_path.display()))?;
        return Ok(());
    }

    let skill_path = source.join("SKILL.md");
    if skill_path.exists() {
        let id = source
            .file_name()
            .and_then(|s| s.to_str())
            .unwrap_or("jachin-skill")
            .to_string();
        let name = read_skill_title(&skill_path).unwrap_or_else(|| id.clone());
        let plugin = json!({
            "id": id,
            "name": name,
            "version": version,
            "description": "Jachin Skill package generated by Capability Publish workbench.",
            "item_type": "SKILL",
            "type": "SKILL",
            "runtime_tier": "L3_LOCAL"
        });
        let pretty = serde_json::to_string_pretty(&plugin)
            .map_err(|e| format!("序列化生成 plugin.json 失败: {e}"))?;
        fs::write(source.join("plugin.json"), format!("{pretty}\n"))
            .map_err(|e| format!("生成 plugin.json 失败: {e}"))?;
        return Ok(());
    }

    Err("该目录缺少 plugin.json 或 manifest.yaml，无法更新版本号".to_string())
}

fn is_semver(version: &str) -> bool {
    let main = version.split(['-', '+']).next().unwrap_or(version);
    let parts: Vec<_> = main.split('.').collect();
    parts.len() == 3
        && parts
            .iter()
            .all(|p| !p.is_empty() && p.chars().all(|c| c.is_ascii_digit()))
}

fn validate_semver(version: &str) -> Result<(), String> {
    if is_semver(version) {
        Ok(())
    } else {
        Err(format!(
            "版本号必须是语义化版本，例如 1.0.1，当前: {version}"
        ))
    }
}

fn parse_prefixed_path(output: &str, prefix: &str) -> Option<String> {
    output.lines().find_map(|line| {
        line.trim()
            .strip_prefix(prefix)
            .map(|s| s.trim().to_string())
            .filter(|s| !s.is_empty())
    })
}

fn timestamp_string() -> String {
    let secs = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or_default();
    secs.to_string()
}

fn jachin_home_dir() -> PathBuf {
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

fn l1_direct_config_path() -> PathBuf {
    jachin_home_dir().join("l1_direct_publish.json")
}

fn read_l1_direct_config() -> L1DirectConfig {
    let path = l1_direct_config_path();
    let mut cfg = if let Ok(raw) = fs::read_to_string(path) {
        serde_json::from_str(raw.trim_start_matches('\u{feff}')).unwrap_or_default()
    } else {
        L1DirectConfig::default()
    };
    merge_l1_defaults_from_nexus(&mut cfg);
    cfg
}

fn merge_l1_defaults_from_nexus(config: &mut L1DirectConfig) {
    if config.base_url.trim().is_empty() {
        config.base_url = crate::nexus_config::nexus_base_url()
            .unwrap_or_else(|| DEFAULT_L1_BASE_URL.to_string());
    }
    if config
        .developer_id
        .as_deref()
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .is_none()
    {
        config.developer_id = crate::nexus_config::l1_user_id();
    }
    if config
        .developer_token
        .as_deref()
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .is_none()
    {
        config.developer_token = crate::nexus_config::access_token();
    }
    if config.visibility.as_deref().unwrap_or("").trim().is_empty() {
        config.visibility = Some("PUBLIC".to_string());
    }
    if config.upload_by_default.is_none() {
        config.upload_by_default = Some(true);
    }
}

fn write_l1_direct_config(config: &L1DirectConfig) -> Result<(), String> {
    let path = l1_direct_config_path();
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|e| format!("create L1 config dir failed: {e}"))?;
    }
    let text = serde_json::to_string_pretty(config)
        .map_err(|e| format!("serialize L1 config failed: {e}"))?;
    fs::write(&path, format!("{text}\n")).map_err(|e| format!("write L1 config failed: {e}"))
}

fn l1_profile_from_config(config: &L1DirectConfig) -> CapabilityL1DirectProfile {
    let token = config.developer_token.as_deref().unwrap_or("").trim();
    let token_preview = if token.is_empty() {
        None
    } else if token.len() <= 8 {
        Some("***".to_string())
    } else {
        Some(format!("{}***{}", &token[..4], &token[token.len() - 4..]))
    };
    CapabilityL1DirectProfile {
        config_path: l1_direct_config_path().display().to_string(),
        base_url: config.base_url.trim().trim_end_matches('/').to_string(),
        developer_id: config
            .developer_id
            .as_deref()
            .unwrap_or("")
            .trim()
            .to_string(),
        token_present: !token.is_empty(),
        token_preview,
        visibility: config.visibility.as_deref().unwrap_or("PUBLIC").to_string(),
        upload_by_default: config.upload_by_default.unwrap_or(true),
        l2_required: false,
    }
}

fn http_client() -> Result<reqwest::blocking::Client, String> {
    reqwest::blocking::Client::builder()
        .timeout(Duration::from_secs(12))
        .build()
        .map_err(|e| format!("create HTTP client failed: {e}"))
}

fn test_l1_direct_config(config: &L1DirectConfig) -> Result<CapabilityL1DirectTestResult, String> {
    let base_url = config.base_url.trim().trim_end_matches('/').to_string();
    if base_url.is_empty() {
        return Ok(CapabilityL1DirectTestResult {
            ok: false,
            base_url,
            developer_id: config.developer_id.clone(),
            catalog_reachable: false,
            developer_items_count: None,
            message: "L1 base URL is empty".to_string(),
        });
    }
    let client = http_client()?;
    let catalog_url = format!("{}/api/v1/store/catalog?limit=1", base_url);
    let catalog_resp = client
        .get(catalog_url)
        .send()
        .map_err(|e| format!("connect L1 catalog failed: {e}"))?;
    let catalog_reachable = catalog_resp.status().is_success();
    if !catalog_reachable {
        return Ok(CapabilityL1DirectTestResult {
            ok: false,
            base_url,
            developer_id: config.developer_id.clone(),
            catalog_reachable: false,
            developer_items_count: None,
            message: format!(
                "L1 catalog returned HTTP {}",
                catalog_resp.status().as_u16()
            ),
        });
    }

    let mut developer_items_count = None;
    if let Some(dev) = config
        .developer_id
        .as_deref()
        .map(str::trim)
        .filter(|s| !s.is_empty())
    {
        let url = format!("{}/api/v1/developer/plugins?developer_id={}", base_url, dev);
        let resp = client
            .get(url)
            .send()
            .map_err(|e| format!("connect L1 developer plugins failed: {e}"))?;
        if resp.status().is_success() {
            let text = resp
                .text()
                .map_err(|e| format!("read L1 developer plugins failed: {e}"))?;
            let value: Value = serde_json::from_str(&text).unwrap_or_else(|_| json!({}));
            developer_items_count = value
                .get("meta")
                .and_then(|m| m.get("total"))
                .and_then(Value::as_u64)
                .map(|n| n as usize)
                .or_else(|| value.get("data").and_then(Value::as_array).map(|a| a.len()));
        } else {
            return Ok(CapabilityL1DirectTestResult {
                ok: false,
                base_url,
                developer_id: config.developer_id.clone(),
                catalog_reachable,
                developer_items_count: None,
                message: format!(
                    "L1 developer plugins returned HTTP {}",
                    resp.status().as_u16()
                ),
            });
        }
    }

    Ok(CapabilityL1DirectTestResult {
        ok: true,
        base_url,
        developer_id: config.developer_id.clone(),
        catalog_reachable,
        developer_items_count,
        message: if developer_items_count.is_some() {
            "L3 can reach L1 directly and developer package list is readable".to_string()
        } else {
            "L3 can reach L1 directly; developer_id is empty so published list was not checked"
                .to_string()
        },
    })
}

fn fetch_l1_developer_packages(
    config: &L1DirectConfig,
) -> Result<HashMap<String, RemotePackageRecord>, String> {
    let base_url = config.base_url.trim().trim_end_matches('/').to_string();
    let developer_id = config
        .developer_id
        .as_deref()
        .map(str::trim)
        .filter(|s| !s.is_empty());
    if base_url.is_empty() || developer_id.is_none() {
        return Ok(HashMap::new());
    }
    let url = format!(
        "{}/api/v1/developer/plugins?developer_id={}",
        base_url,
        developer_id.unwrap()
    );
    let resp = http_client()?
        .get(url)
        .send()
        .map_err(|e| format!("fetch L1 developer packages failed: {e}"))?;
    if !resp.status().is_success() {
        return Err(format!(
            "fetch L1 developer packages failed HTTP {}",
            resp.status().as_u16()
        ));
    }
    let text = resp
        .text()
        .map_err(|e| format!("read L1 developer packages failed: {e}"))?;
    let value: Value =
        serde_json::from_str(&text).map_err(|e| format!("parse L1 packages failed: {e}"))?;
    let mut out = HashMap::new();
    if let Some(items) = value.get("data").and_then(Value::as_array) {
        for item in items {
            let Some(id) = item.get("plugin_id").and_then(Value::as_str) else {
                continue;
            };
            let version = item
                .get("version")
                .and_then(Value::as_str)
                .unwrap_or("0.0.0")
                .to_string();
            out.entry(id.to_string()).or_insert(RemotePackageRecord {
                version,
                review_status: item
                    .get("status")
                    .and_then(Value::as_str)
                    .map(|s| s.to_string()),
                package_url: item
                    .get("package_url")
                    .and_then(Value::as_str)
                    .map(|s| s.to_string()),
            });
        }
    }
    Ok(out)
}

fn upload_package_to_l1(
    base_url: &str,
    token: &str,
    package_path: &Path,
    visibility: &str,
) -> Result<Value, String> {
    let url = format!("{}/api/v1/store/publish", base_url.trim_end_matches('/'));
    let form = reqwest::blocking::multipart::Form::new()
        .file("package", package_path)
        .map_err(|e| format!("读取上传包失败: {e}"))?
        .text("visibility", visibility.to_string());
    let client = reqwest::blocking::Client::new();
    let resp = client
        .post(url)
        .bearer_auth(token)
        .multipart(form)
        .send()
        .map_err(|e| format!("上传 L1 失败: {e}"))?;
    let status = resp.status();
    let text = resp.text().map_err(|e| format!("读取 L1 响应失败: {e}"))?;
    let value: Value = serde_json::from_str(&text).unwrap_or_else(|_| json!({ "raw": text }));
    if !status.is_success() {
        return Err(format!("L1 发布失败 HTTP {}: {}", status.as_u16(), value));
    }
    Ok(value)
}
