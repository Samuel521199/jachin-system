//! OS Assistant evidence browser commands.

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::UNIX_EPOCH;

#[cfg(windows)]
use std::os::windows::process::CommandExt;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct OsEvidenceTimelineEntry {
    pub ts: String,
    pub stage: String,
    pub status: String,
    pub detail: String,
    pub screenshots: Vec<String>,
    pub files: Vec<String>,
    pub ocr_preview: String,
    pub checks: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct OsEvidenceEntry {
    pub id: String,
    pub task: String,
    pub ok: bool,
    pub detail: String,
    pub generated_at: u64,
    pub evidence_path: String,
    pub evidence_panel_path: Option<String>,
    pub report_path: Option<String>,
    pub recipients: Vec<String>,
    pub apps: Vec<String>,
    pub screenshots: Vec<String>,
    pub files: Vec<String>,
    pub message_preview: String,
    pub timeline: Vec<OsEvidenceTimelineEntry>,
    pub diagnosis: String,
    pub intent: Option<Value>,
    pub route: Option<Value>,
    pub clarification: Option<Value>,
    pub tool_result: Option<Value>,
    pub parser: Option<Value>,
    pub memory: Option<Value>,
    pub template: Option<Value>,
    pub mission_preview: Option<Value>,
    pub capability_semantic: Option<Value>,
    pub workflow_composition: Option<Value>,
    pub control: Option<Value>,
    pub plan_preview: Option<Value>,
    pub attempts: Vec<Value>,
    pub retry: Option<Value>,
    pub metrics: Option<Value>,
    pub role_executions: Vec<Value>,
    pub pending_decisions: Vec<Value>,
    pub tool_quality_reports: Vec<Value>,
    pub recovery_scorecards: Vec<Value>,
    pub failure_learning_records: Vec<Value>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct OsEvidenceLaunchInput {
    pub project_name: Option<String>,
    pub project_path: Option<String>,
    pub recipients: Option<Vec<String>>,
    pub wait_seconds: Option<u64>,
    pub dry_run: Option<bool>,
    pub template_id: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct OsEvidenceConfig {
    pub project_name: String,
    pub project_path: String,
    pub recipients: Vec<String>,
    pub wait_seconds: u64,
    pub dry_run: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct OsEvidenceLaunchResult {
    pub ok: bool,
    pub mode: String,
    pub out_dir: String,
    pub pid: Option<u32>,
    pub message: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct OsEvidenceStopResult {
    pub ok: bool,
    pub pid: Option<u32>,
    pub out_dir: Option<String>,
    pub message: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct OsEvidencePreflightResult {
    pub ok: bool,
    pub checks: Value,
    pub raw: Value,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct OsEvidenceStats {
    pub total: usize,
    pub passed: usize,
    pub failed: usize,
    pub success_rate: f64,
    pub avg_duration_ms: u64,
    pub avg_attempts: f64,
    pub failure_top: Vec<(String, usize)>,
    pub workflow_top: Vec<(String, usize)>,
    pub workflow_pass_rate: Vec<(String, usize, usize, f64)>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct OsEvidenceGovernanceSummary {
    pub evidence_count: usize,
    pub quality_reports: usize,
    pub blocked_reports: usize,
    pub block_rate: f64,
    pub recovery_candidates: usize,
    pub failure_learning_records: usize,
    pub tool_quality_top: Vec<(String, usize)>,
    pub quality_issue_top: Vec<(String, usize)>,
    pub failure_class_top: Vec<(String, usize)>,
    pub recovery_strategy_top: Vec<(String, usize)>,
    pub memory_type_top: Vec<(String, usize)>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct OsEvidenceGovernanceSuggestion {
    pub severity: String,
    pub category: String,
    pub message: String,
    pub action: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct OsEvidenceCapabilityHealth {
    pub capability: String,
    pub days: u64,
    pub score: u64,
    pub level: String,
    pub evidence_count: usize,
    pub block_rate: f64,
    pub recovery_density: f64,
    pub learning_density: f64,
    pub top_issue: String,
    pub suggestions: Vec<OsEvidenceGovernanceSuggestion>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct OsEvidenceGovernanceWindow {
    pub days: u64,
    pub capability: String,
    pub summary: OsEvidenceGovernanceSummary,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct OsEvidenceGovernanceIndex {
    pub generated_at: u64,
    pub source_limit: usize,
    pub total_evidence: usize,
    pub index_path: String,
    pub capability_options: Vec<(String, usize)>,
    pub windows: Vec<OsEvidenceGovernanceWindow>,
    pub health: Vec<OsEvidenceCapabilityHealth>,
}

fn project_output_dir() -> Result<PathBuf, String> {
    if let Some(root) = crate::l3_spawn::project_root() {
        return Ok(root.join("output"));
    }
    std::env::current_dir()
        .map(|p| p.join("output"))
        .map_err(|e| format!("resolve output dir failed: {e}"))
}

fn config_path() -> Result<PathBuf, String> {
    Ok(project_output_dir()?.join("os_evidence_console_config.json"))
}

fn default_config() -> OsEvidenceConfig {
    OsEvidenceConfig {
        project_name: "Jachin".to_string(),
        project_path: crate::l3_spawn::project_root()
            .map(|p| p.to_string_lossy().into_owned())
            .unwrap_or_default(),
        recipients: vec!["Vivian".to_string()],
        wait_seconds: 120,
        dry_run: false,
    }
}

fn write_json_file(path: &Path, value: &Value) -> Result<(), String> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|e| format!("create parent dir failed: {e}"))?;
    }
    let text =
        serde_json::to_string_pretty(value).map_err(|e| format!("serialize json failed: {e}"))?;
    fs::write(path, text).map_err(|e| format!("write json failed: {e}"))
}

fn modified_secs(path: &Path) -> u64 {
    fs::metadata(path)
        .and_then(|m| m.modified())
        .ok()
        .and_then(|t| t.duration_since(UNIX_EPOCH).ok())
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

fn compact_preview(text: &str, max_len: usize) -> String {
    let s = text.split_whitespace().collect::<Vec<_>>().join(" ");
    if s.chars().count() <= max_len {
        return s;
    }
    let mut out = s
        .chars()
        .take(max_len.saturating_sub(1))
        .collect::<String>();
    out.push('…');
    out
}

fn as_string_vec(value: Option<&Value>) -> Vec<String> {
    match value {
        Some(Value::Array(items)) => items
            .iter()
            .filter_map(|v| v.as_str().map(|s| s.trim().to_string()))
            .filter(|s| !s.is_empty())
            .collect(),
        Some(Value::String(s)) if !s.trim().is_empty() => vec![s.trim().to_string()],
        _ => Vec::new(),
    }
}

fn get_path(value: &Value, keys: &[&str]) -> Option<String> {
    for key in keys {
        if let Some(s) = value.get(*key).and_then(|v| v.as_str()) {
            if !s.trim().is_empty() {
                return Some(s.trim().to_string());
            }
        }
    }
    None
}

fn collect_paths(value: &Value, files: &mut BTreeSet<String>, screenshots: &mut BTreeSet<String>) {
    match value {
        Value::Object(map) => {
            for (key, item) in map {
                if let Value::String(s) = item {
                    let lower_key = key.to_ascii_lowercase();
                    let lower = s.to_ascii_lowercase();
                    if lower.ends_with(".png")
                        || lower.ends_with(".jpg")
                        || lower.ends_with(".jpeg")
                    {
                        screenshots.insert(s.clone());
                    } else if lower_key.contains("path")
                        || lower.ends_with(".json")
                        || lower.ends_with(".html")
                        || lower.ends_with(".md")
                    {
                        files.insert(s.clone());
                    }
                } else {
                    collect_paths(item, files, screenshots);
                }
            }
        }
        Value::Array(items) => {
            for item in items {
                collect_paths(item, files, screenshots);
            }
        }
        _ => {}
    }
}

fn collect_apps(value: &Value, apps: &mut BTreeSet<String>) {
    match value {
        Value::Object(map) => {
            for key in ["app", "app_key", "active_title", "title"] {
                if let Some(s) = map.get(key).and_then(|v| v.as_str()) {
                    let trimmed = s.trim();
                    if !trimmed.is_empty() {
                        apps.insert(trimmed.to_string());
                    }
                }
            }
            for item in map.values() {
                collect_apps(item, apps);
            }
        }
        Value::Array(items) => {
            for item in items {
                collect_apps(item, apps);
            }
        }
        _ => {}
    }
}

fn collect_recipients(value: &Value) -> Vec<String> {
    let mut out = BTreeSet::new();
    for s in as_string_vec(value.get("recipients")) {
        out.insert(s);
    }
    if let Some(slots) = value.get("intent").and_then(|v| v.get("slots")) {
        for s in as_string_vec(slots.get("recipients")) {
            out.insert(s);
        }
    }
    if let Some(send_ev) = value
        .get("send_result")
        .and_then(|v| v.get("evidence"))
        .or_else(|| {
            value
                .get("run")
                .and_then(|v| v.get("evidence"))
                .and_then(|v| v.get("send_result"))
                .and_then(|v| v.get("evidence"))
        })
    {
        for s in as_string_vec(send_ev.get("recipients")) {
            out.insert(s);
        }
        if let Some(deliveries) = send_ev.get("deliveries").and_then(|v| v.as_array()) {
            for row in deliveries {
                if let Some(s) = row.get("recipient").and_then(|v| v.as_str()) {
                    if !s.trim().is_empty() {
                        out.insert(s.trim().to_string());
                    }
                }
            }
        }
    }
    out.into_iter().collect()
}

fn collect_timeline(value: &Value) -> Vec<OsEvidenceTimelineEntry> {
    let timeline = value
        .get("timeline")
        .and_then(|v| v.as_array())
        .or_else(|| {
            value
                .get("run")
                .and_then(|v| v.get("evidence"))
                .and_then(|v| v.get("timeline"))
                .and_then(|v| v.as_array())
        });
    timeline
        .into_iter()
        .flatten()
        .filter_map(|row| {
            let obj = row.as_object()?;
            let evidence = obj.get("evidence").unwrap_or(&Value::Null);
            let mut files = BTreeSet::new();
            let mut screenshots = BTreeSet::new();
            collect_paths(evidence, &mut files, &mut screenshots);
            let ocr_preview = timeline_ocr_preview(evidence);
            let checks = timeline_checks(evidence);
            Some(OsEvidenceTimelineEntry {
                ts: obj
                    .get("ts")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string(),
                stage: obj
                    .get("stage")
                    .and_then(|v| v.as_str())
                    .unwrap_or("step")
                    .to_string(),
                status: obj
                    .get("status")
                    .and_then(|v| v.as_str())
                    .unwrap_or("done")
                    .to_string(),
                detail: obj
                    .get("detail")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string(),
                screenshots: screenshots.into_iter().take(8).collect(),
                files: files.into_iter().take(8).collect(),
                ocr_preview,
                checks,
            })
        })
        .collect()
}

fn timeline_ocr_preview(value: &Value) -> String {
    fn walk(node: &Value, out: &mut String) {
        if out.chars().count() >= 600 {
            return;
        }
        match node {
            Value::Object(map) => {
                for (key, item) in map {
                    let key_l = key.to_ascii_lowercase();
                    if key_l.contains("ocr") || key_l.contains("visual") {
                        if let Some(s) = item.as_str() {
                            if !s.trim().is_empty() {
                                if !out.is_empty() {
                                    out.push('\n');
                                }
                                out.push_str(s.trim());
                            }
                        }
                    }
                    walk(item, out);
                }
            }
            Value::Array(items) => {
                for item in items {
                    walk(item, out);
                }
            }
            _ => {}
        }
    }
    let mut out = String::new();
    walk(value, &mut out);
    compact_preview(&out, 600)
}

fn timeline_checks(value: &Value) -> Vec<String> {
    fn walk(node: &Value, out: &mut Vec<String>) {
        match node {
            Value::Object(map) => {
                for (key, item) in map {
                    let key_l = key.to_ascii_lowercase();
                    if key_l.contains("check")
                        || key_l.contains("validation")
                        || key_l.contains("visible")
                        || key_l.contains("match")
                    {
                        match item {
                            Value::Bool(v) => out.push(format!("{key}={v}")),
                            Value::Number(v) => out.push(format!("{key}={v}")),
                            Value::String(s) if !s.trim().is_empty() => {
                                out.push(format!("{key}={}", compact_preview(s, 80)));
                            }
                            _ => {}
                        }
                    }
                    walk(item, out);
                }
            }
            Value::Array(items) => {
                for item in items {
                    walk(item, out);
                }
            }
            _ => {}
        }
    }
    let mut out = Vec::new();
    walk(value, &mut out);
    out.truncate(16);
    out
}

fn collect_role_executions(value: &Value, out: &mut Vec<Value>) {
    match value {
        Value::Object(map) => {
            let event_type = map.get("event_type").and_then(|v| v.as_str()).unwrap_or("");
            if event_type == "role_execution_started" || event_type == "role_execution_finished" {
                out.push(value.clone());
            }
            if let Some(payload) = map.get("payload").and_then(|v| v.as_object()) {
                if payload.get("role_id").is_some()
                    && payload.get("adapter_kind").is_some()
                    && payload.get("work_order_id").is_some()
                {
                    out.push(value.clone());
                }
            }
            if map.get("type").and_then(|v| v.as_str()) == Some("role_execution") {
                out.push(value.clone());
            }
            for item in map.values() {
                collect_role_executions(item, out);
            }
        }
        Value::Array(items) => {
            for item in items {
                collect_role_executions(item, out);
            }
        }
        _ => {}
    }
}

fn collect_pending_decisions(value: &Value, out: &mut Vec<Value>) {
    match value {
        Value::Object(map) => {
            let event_type = map.get("event_type").and_then(|v| v.as_str()).unwrap_or("");
            if matches!(
                event_type,
                "confirmation_pending_saved"
                    | "confirmation_resumed"
                    | "confirmation_cancelled"
                    | "confirmation_expired"
            ) {
                out.push(value.clone());
            }
            if let Some(payload) = map.get("payload").and_then(|v| v.as_object()) {
                if payload.get("pending_decision").is_some()
                    || (payload
                        .get("requires_confirmation")
                        .and_then(|v| v.as_bool())
                        == Some(true)
                        && payload.get("decision_id").is_some())
                {
                    out.push(value.clone());
                }
            }
            for item in map.values() {
                collect_pending_decisions(item, out);
            }
        }
        Value::Array(items) => {
            for item in items {
                collect_pending_decisions(item, out);
            }
        }
        _ => {}
    }
}

fn collect_tool_quality_reports(value: &Value, out: &mut Vec<Value>) {
    match value {
        Value::Object(map) => {
            if map.get("type").and_then(|v| v.as_str()) == Some("tool_quality")
                || (map.get("quality_level").is_some()
                    && map.get("score").is_some()
                    && map.get("issues").is_some())
            {
                out.push(value.clone());
            }
            for item in map.values() {
                collect_tool_quality_reports(item, out);
            }
        }
        Value::Array(items) => {
            for item in items {
                collect_tool_quality_reports(item, out);
            }
        }
        _ => {}
    }
}

fn collect_recovery_scorecards(value: &Value, out: &mut Vec<Value>) {
    match value {
        Value::Object(map) => {
            if let Some(scorecard) = map.get("adaptive_scorecard") {
                out.push(scorecard.clone());
            }
            if map.get("current_failure_class").is_some()
                && map.get("candidate_strategy").is_some()
                && map.get("score").is_some()
            {
                out.push(value.clone());
            }
            for item in map.values() {
                collect_recovery_scorecards(item, out);
            }
        }
        Value::Array(items) => {
            for item in items {
                collect_recovery_scorecards(item, out);
            }
        }
        _ => {}
    }
}

fn collect_failure_learning_records(value: &Value, out: &mut Vec<Value>) {
    match value {
        Value::Object(map) => {
            let event_type = map.get("event_type").and_then(|v| v.as_str()).unwrap_or("");
            if event_type == "failure_learning_recorded" {
                out.push(value.clone());
                for (key, item) in map {
                    if key != "payload" {
                        collect_failure_learning_records(item, out);
                    }
                }
                return;
            } else if map.get("failure_class").is_some()
                && map.get("next_strategy").is_some()
                && map.get("memory_write").is_some()
            {
                out.push(value.clone());
            }
            for item in map.values() {
                collect_failure_learning_records(item, out);
            }
        }
        Value::Array(items) => {
            for item in items {
                collect_failure_learning_records(item, out);
            }
        }
        _ => {}
    }
}

fn diagnose(value: &Value, ok: bool, detail: &str) -> String {
    let validation_ok = value
        .get("validation")
        .and_then(|v| v.get("ok"))
        .and_then(|v| v.as_bool())
        .unwrap_or(true);
    if !validation_ok {
        return "Codex 输出不可信：项目名/关键文件/结论校验未通过，未发送或需要人工确认。"
            .to_string();
    }
    let send_result = value.get("send_result").and_then(|v| v.as_object());
    if let Some(send) = send_result {
        if send.get("ok").and_then(|v| v.as_bool()).unwrap_or(false) {
            return "已完成：Lark 发送和视觉/OCR 校验通过。".to_string();
        }
        let send_detail = send.get("detail").and_then(|v| v.as_str()).unwrap_or("");
        if send_detail.contains("lark_open_failed") {
            return "失败点：没打开 Lark。请确认 Lark 已安装、已登录，或检查窗口权限。".to_string();
        }
        let deliveries = send
            .get("evidence")
            .and_then(|v| v.get("deliveries"))
            .and_then(|v| v.as_array());
        if let Some(rows) = deliveries {
            for row in rows {
                if !row.get("ok").and_then(|v| v.as_bool()).unwrap_or(false) {
                    let recipient = row
                        .get("recipient")
                        .and_then(|v| v.as_str())
                        .unwrap_or("目标对象");
                    let stage = row
                        .get("failure_stage")
                        .and_then(|v| v.as_str())
                        .unwrap_or("");
                    if stage.contains("preview") {
                        return format!(
                            "失败点：{} 的粘贴预览不匹配或没找到正确聊天对象。",
                            recipient
                        );
                    }
                    if !row
                        .get("recipient_visible")
                        .and_then(|v| v.as_bool())
                        .unwrap_or(false)
                    {
                        return format!("失败点：没找到或没聚焦到 {} 的聊天窗口。", recipient);
                    }
                    if !row
                        .get("message_visible")
                        .and_then(|v| v.as_bool())
                        .unwrap_or(false)
                    {
                        return format!(
                            "失败点：OCR 没看到 {} 的发送结果，可能未发送或消息太长需要滚动校验。",
                            recipient
                        );
                    }
                }
            }
        }
        return "失败点：Lark 发送链路未完全通过视觉校验，请打开 evidence 查看截图和时间线。"
            .to_string();
    }
    if ok {
        "已完成：任务 evidence 已生成。".to_string()
    } else if detail.contains("codex_open_failed") {
        "失败点：没打开 Codex。请确认 Codex 已安装或窗口可聚焦。".to_string()
    } else {
        format!("需要检查：{}", detail)
    }
}

fn evidence_entry(path: &Path) -> Option<OsEvidenceEntry> {
    let text = fs::read_to_string(path).ok()?;
    let value: Value = serde_json::from_str(&text).ok()?;
    let task = value
        .get("task")
        .and_then(|v| v.as_str())
        .or_else(|| {
            value
                .get("run")
                .and_then(|v| v.get("task"))
                .and_then(|v| v.as_str())
        })
        .unwrap_or_else(|| {
            path.file_stem()
                .and_then(|s| s.to_str())
                .unwrap_or("evidence")
        })
        .to_string();
    let ok = value
        .get("ok")
        .and_then(|v| v.as_bool())
        .or_else(|| {
            value
                .get("run")
                .and_then(|v| v.get("ok"))
                .and_then(|v| v.as_bool())
        })
        .unwrap_or(true);
    let detail = value
        .get("detail")
        .and_then(|v| v.as_str())
        .or_else(|| {
            value
                .get("run")
                .and_then(|v| v.get("detail"))
                .and_then(|v| v.as_str())
        })
        .unwrap_or("evidence_ready")
        .to_string();
    let message = value
        .get("message_text")
        .and_then(|v| v.as_str())
        .or_else(|| value.get("summary").and_then(|v| v.as_str()))
        .or_else(|| value.get("message_preview").and_then(|v| v.as_str()))
        .or_else(|| {
            value
                .get("send_result")
                .and_then(|v| v.get("evidence"))
                .and_then(|v| v.get("message"))
                .and_then(|v| v.as_str())
        })
        .unwrap_or("");
    let mut files = BTreeSet::new();
    let mut screenshots = BTreeSet::new();
    collect_paths(&value, &mut files, &mut screenshots);
    let mut apps = BTreeSet::new();
    collect_apps(&value, &mut apps);
    let mut role_executions = Vec::new();
    collect_role_executions(&value, &mut role_executions);
    role_executions.truncate(40);
    let mut pending_decisions = Vec::new();
    collect_pending_decisions(&value, &mut pending_decisions);
    pending_decisions.truncate(20);
    let mut tool_quality_reports = Vec::new();
    collect_tool_quality_reports(&value, &mut tool_quality_reports);
    tool_quality_reports.truncate(40);
    let mut recovery_scorecards = Vec::new();
    collect_recovery_scorecards(&value, &mut recovery_scorecards);
    recovery_scorecards.truncate(40);
    let mut failure_learning_records = Vec::new();
    collect_failure_learning_records(&value, &mut failure_learning_records);
    failure_learning_records.truncate(40);
    let evidence_panel_path = get_path(&value, &["evidence_panel_path"]);
    let report_path = get_path(&value, &["report_path"]);
    let id = path.to_string_lossy().into_owned();
    Some(OsEvidenceEntry {
        id: id.clone(),
        task,
        ok,
        detail: detail.clone(),
        generated_at: modified_secs(path),
        evidence_path: id,
        evidence_panel_path,
        report_path,
        recipients: collect_recipients(&value),
        apps: apps.into_iter().take(12).collect(),
        screenshots: screenshots.into_iter().take(12).collect(),
        files: files.into_iter().take(24).collect(),
        message_preview: compact_preview(message, 360),
        timeline: collect_timeline(&value),
        diagnosis: diagnose(&value, ok, &detail),
        intent: value.get("intent").cloned(),
        route: value.get("route").cloned(),
        clarification: value.get("clarification").cloned(),
        tool_result: value.get("tool_result").cloned(),
        parser: value.get("parser").cloned(),
        memory: value.get("memory").cloned(),
        template: value.get("template").cloned(),
        mission_preview: value.get("mission_preview").cloned(),
        capability_semantic: value.get("capability_semantic").cloned(),
        workflow_composition: value.get("workflow_composition").cloned(),
        control: value.get("control").cloned(),
        plan_preview: value.get("plan_preview").cloned(),
        attempts: value
            .get("attempts")
            .and_then(|v| v.as_array())
            .cloned()
            .unwrap_or_default(),
        retry: value.get("retry").cloned(),
        metrics: value.get("metrics").cloned(),
        role_executions,
        pending_decisions,
        tool_quality_reports,
        recovery_scorecards,
        failure_learning_records,
    })
}

fn cognitive_kernel_ledger_dirs() -> Vec<PathBuf> {
    let mut dirs = Vec::new();
    if let Ok(root) = std::env::var("JACHIN_COGNITIVE_KERNEL_HOME") {
        if !root.trim().is_empty() {
            dirs.push(PathBuf::from(root).join("ledger"));
        }
    }
    if let Ok(profile) = std::env::var("USERPROFILE").or_else(|_| std::env::var("HOME")) {
        if !profile.trim().is_empty() {
            dirs.push(
                PathBuf::from(profile)
                    .join(".jachin")
                    .join("cognitive_kernel")
                    .join("ledger"),
            );
        }
    }
    if let Ok(output) = project_output_dir() {
        dirs.push(output.join("cognitive_kernel").join("ledger"));
        dirs.push(output.join("cognitive_kernel_stage_e_smoke").join("ledger"));
    }
    let mut seen = BTreeSet::new();
    dirs.into_iter()
        .filter(|p| seen.insert(p.to_string_lossy().to_string()))
        .collect()
}

fn visit_cognitive_ledger_files(dir: &Path, out: &mut Vec<PathBuf>) {
    let Ok(read_dir) = fs::read_dir(dir) else {
        return;
    };
    for entry in read_dir.flatten() {
        let path = entry.path();
        if path.is_dir() {
            visit_cognitive_ledger_files(&path, out);
        } else if path
            .file_name()
            .and_then(|s| s.to_str())
            .map(|s| s.starts_with("cognitive_kernel_") && s.ends_with(".jsonl"))
            .unwrap_or(false)
        {
            out.push(path);
        }
    }
}

fn cognitive_ledger_entries(path: &Path) -> Vec<OsEvidenceEntry> {
    let Ok(text) = fs::read_to_string(path) else {
        return Vec::new();
    };
    let mut by_turn: BTreeMap<String, Vec<Value>> = BTreeMap::new();
    for line in text.lines() {
        let trimmed = line.trim();
        if trimmed.is_empty() {
            continue;
        }
        let Ok(event) = serde_json::from_str::<Value>(trimmed) else {
            continue;
        };
        let turn_id = event
            .get("turn_id")
            .and_then(|v| v.as_str())
            .unwrap_or("unknown")
            .to_string();
        by_turn.entry(turn_id).or_default().push(event);
    }
    by_turn
        .into_iter()
        .filter_map(|(turn_id, events)| cognitive_turn_entry(path, &turn_id, events))
        .collect()
}

fn cognitive_turn_entry(path: &Path, turn_id: &str, events: Vec<Value>) -> Option<OsEvidenceEntry> {
    if events.is_empty() {
        return None;
    }
    let mut generated_at = modified_secs(path);
    let mut task = String::new();
    let mut detail = String::new();
    let mut ok = true;
    let mut message_preview = String::new();
    let mut files = BTreeSet::new();
    let mut screenshots = BTreeSet::new();
    let mut apps = BTreeSet::new();
    let mut recipients = BTreeSet::new();
    let mut role_executions = Vec::new();
    let mut pending_decisions = Vec::new();
    let mut tool_quality_reports = Vec::new();
    let mut recovery_scorecards = Vec::new();
    let mut failure_learning_records = Vec::new();
    let mut timeline = Vec::new();
    let mut metrics_start_ms: Option<u64> = None;
    let mut metrics_end_ms: Option<u64> = None;
    let mut decision: Option<Value> = None;
    let mut route: Option<Value> = None;
    let mut memory: Option<Value> = None;
    let mut retry: Option<Value> = None;
    let mut attempts: Vec<Value> = Vec::new();

    let aggregate = json!({ "events": events.clone() });
    collect_paths(&aggregate, &mut files, &mut screenshots);
    collect_apps(&aggregate, &mut apps);
    collect_role_executions(&aggregate, &mut role_executions);
    collect_pending_decisions(&aggregate, &mut pending_decisions);
    collect_tool_quality_reports(&aggregate, &mut tool_quality_reports);
    collect_recovery_scorecards(&aggregate, &mut recovery_scorecards);
    collect_failure_learning_records(&aggregate, &mut failure_learning_records);

    for event in events.iter() {
        let event_type = event
            .get("event_type")
            .and_then(|v| v.as_str())
            .unwrap_or("");
        let payload = event.get("payload").unwrap_or(&Value::Null);
        let ts_ms = event.get("ts_ms").and_then(|v| v.as_u64()).unwrap_or(0);
        if ts_ms > 0 {
            generated_at = generated_at.max(ts_ms / 1000);
            metrics_start_ms = Some(metrics_start_ms.map(|v| v.min(ts_ms)).unwrap_or(ts_ms));
            metrics_end_ms = Some(metrics_end_ms.map(|v| v.max(ts_ms)).unwrap_or(ts_ms));
        }
        match event_type {
            "decision_contract" => {
                decision = Some(payload.clone());
                task = payload
                    .get("goal")
                    .and_then(|v| v.as_str())
                    .unwrap_or(task.as_str())
                    .to_string();
                route = Some(json!({
                    "workflow_id": payload.get("selected_workflow").cloned().unwrap_or(Value::Null),
                    "selected_roles": payload.get("selected_roles").cloned().unwrap_or(Value::Null),
                    "risk_level": payload.get("risk_level").cloned().unwrap_or(Value::Null),
                }));
            }
            "turn_started" => {
                if task.is_empty() {
                    task = payload
                        .get("input_envelope")
                        .and_then(|v| v.get("normalized_text"))
                        .and_then(|v| v.as_str())
                        .unwrap_or("")
                        .to_string();
                }
                memory = payload.get("memory_bundle").cloned();
            }
            "work_order" => {
                if task.is_empty() {
                    task = payload
                        .get("task")
                        .and_then(|v| v.as_str())
                        .unwrap_or("")
                        .to_string();
                }
            }
            "verification_report" => {
                let report_ok = payload.get("ok").and_then(|v| v.as_bool()).unwrap_or(false);
                ok = report_ok;
                if !report_ok {
                    detail = payload
                        .get("failure_reason")
                        .and_then(|v| v.as_str())
                        .unwrap_or("verification_failed")
                        .to_string();
                }
                if message_preview.is_empty() {
                    message_preview = payload
                        .get("evidence")
                        .and_then(|v| v.as_array())
                        .and_then(|rows| {
                            rows.iter()
                                .find_map(|row| row.get("preview").and_then(|v| v.as_str()))
                        })
                        .unwrap_or("")
                        .to_string();
                }
            }
            "recovery_plan" => {
                retry = Some(payload.clone());
            }
            "recovery_execution_started" | "recovery_execution_finished" => {
                attempts.push(payload.clone());
            }
            "turn_closure" => {
                let closure_type = payload
                    .get("closure_type")
                    .and_then(|v| v.as_str())
                    .unwrap_or("");
                let verification_status = payload
                    .get("verification_status")
                    .and_then(|v| v.as_str())
                    .unwrap_or("");
                ok = !matches!(
                    closure_type,
                    "blocked" | "failed_recoverable" | "failed_final" | "waiting_user"
                ) && verification_status != "failed";
                if task.is_empty() {
                    task = payload
                        .get("final_user_message_intent")
                        .and_then(|v| v.as_str())
                        .unwrap_or("")
                        .to_string();
                }
            }
            _ => {}
        }
        collect_recipient_hints(payload, &mut recipients);
        timeline.push(cognitive_timeline_entry(event_type, ts_ms, payload));
    }
    timeline.sort_by_key(|row| {
        let ts = row.ts.parse::<u64>().unwrap_or(0);
        (cognitive_event_order(&row.stage), ts)
    });

    if task.trim().is_empty() {
        task = format!("Cognitive Kernel turn {turn_id}");
    }
    if detail.trim().is_empty() {
        detail = if ok {
            "cognitive_kernel_turn_visible".to_string()
        } else {
            "cognitive_kernel_turn_failed".to_string()
        };
    }
    role_executions.truncate(60);
    pending_decisions.truncate(20);
    tool_quality_reports.truncate(40);
    recovery_scorecards.truncate(40);
    failure_learning_records.truncate(40);
    let duration_ms = metrics_start_ms
        .zip(metrics_end_ms)
        .map(|(start, end)| end.saturating_sub(start))
        .unwrap_or(0);
    let evidence_path = format!("{}#{}", path.to_string_lossy(), turn_id);
    Some(OsEvidenceEntry {
        id: evidence_path.clone(),
        task,
        ok,
        detail: detail.clone(),
        generated_at,
        evidence_path,
        evidence_panel_path: None,
        report_path: None,
        recipients: recipients.into_iter().take(16).collect(),
        apps: apps.into_iter().take(12).collect(),
        screenshots: screenshots.into_iter().take(12).collect(),
        files: files.into_iter().take(24).collect(),
        message_preview: compact_preview(&message_preview, 360),
        timeline,
        diagnosis: if ok {
            "Cognitive Kernel ledger 已记录 DecisionContract -> WorkOrder -> RoleExecutor -> Verification。".to_string()
        } else {
            format!("Cognitive Kernel ledger 显示该 turn 未完全通过：{}", detail)
        },
        intent: decision,
        route,
        clarification: None,
        tool_result: None,
        parser: None,
        memory,
        template: None,
        mission_preview: None,
        capability_semantic: None,
        workflow_composition: None,
        control: Some(json!({ "turn_id": turn_id, "ledger_path": path.to_string_lossy() })),
        plan_preview: None,
        attempts,
        retry,
        metrics: Some(json!({
            "workflow_id": "cognitive_kernel_ledger",
            "duration_ms": duration_ms,
            "attempt_count": role_executions.len(),
        })),
        role_executions,
        pending_decisions,
        tool_quality_reports,
        recovery_scorecards,
        failure_learning_records,
    })
}

fn cognitive_timeline_entry(
    event_type: &str,
    ts_ms: u64,
    payload: &Value,
) -> OsEvidenceTimelineEntry {
    let mut files = BTreeSet::new();
    let mut screenshots = BTreeSet::new();
    collect_paths(payload, &mut files, &mut screenshots);
    let status = match event_type {
        "verification_report" => {
            if payload.get("ok").and_then(|v| v.as_bool()).unwrap_or(false) {
                "passed"
            } else {
                "failed"
            }
        }
        "role_execution_finished" | "recovery_execution_finished" => {
            if payload.get("ok").and_then(|v| v.as_bool()).unwrap_or(false) {
                "done"
            } else {
                "failed"
            }
        }
        "work_order" => payload
            .get("status")
            .and_then(|v| v.as_str())
            .unwrap_or("recorded"),
        _ => "recorded",
    };
    let detail = payload
        .get("goal")
        .or_else(|| payload.get("task"))
        .or_else(|| payload.get("rationale"))
        .or_else(|| payload.get("failure_reason"))
        .or_else(|| payload.get("observation_preview"))
        .and_then(|v| v.as_str())
        .unwrap_or(event_type);
    OsEvidenceTimelineEntry {
        ts: if ts_ms > 0 {
            ts_ms.to_string()
        } else {
            String::new()
        },
        stage: event_type.to_string(),
        status: status.to_string(),
        detail: compact_preview(detail, 220),
        screenshots: screenshots.into_iter().take(8).collect(),
        files: files.into_iter().take(8).collect(),
        ocr_preview: timeline_ocr_preview(payload),
        checks: timeline_checks(payload),
    }
}

fn cognitive_event_order(event_type: &str) -> u8 {
    match event_type {
        "turn_started" => 0,
        "review_board_summary" => 10,
        "decision_contract" | "arbiter_decision" => 20,
        "confirmation_pending_saved" => 25,
        "work_order" | "arbiter_work_order_created" => 30,
        "role_execution_started" => 40,
        "role_execution_finished" => 50,
        "verification_report" => 60,
        "confirmation_resumed" => 65,
        "confirmation_cancelled" | "confirmation_expired" => 66,
        "recovery_plan" => 70,
        "recovery_execution_started" => 80,
        "recovery_execution_finished" => 90,
        "turn_closure" => 100,
        "kernel_planning_finished" => 110,
        _ => 120,
    }
}

fn collect_recipient_hints(value: &Value, out: &mut BTreeSet<String>) {
    match value {
        Value::Object(map) => {
            for key in [
                "recipient",
                "recipients",
                "recipient_hints",
                "chat_id",
                "chat_ids",
                "to",
            ] {
                if let Some(item) = map.get(key) {
                    for s in as_string_vec(Some(item)) {
                        out.insert(s);
                    }
                }
            }
            for item in map.values() {
                collect_recipient_hints(item, out);
            }
        }
        Value::Array(items) => {
            for item in items {
                collect_recipient_hints(item, out);
            }
        }
        _ => {}
    }
}

fn visit_evidence_files(dir: &Path, out: &mut Vec<PathBuf>, limit: usize) {
    if limit > 0 && out.len() >= limit {
        return;
    }
    let Ok(read_dir) = fs::read_dir(dir) else {
        return;
    };
    for entry in read_dir.flatten() {
        let path = entry.path();
        if path.is_dir() {
            visit_evidence_files(&path, out, limit);
        } else if path
            .file_name()
            .and_then(|s| s.to_str())
            .map(|s| s.ends_with(".evidence.json"))
            .unwrap_or(false)
        {
            out.push(path);
            if limit > 0 && out.len() >= limit {
                return;
            }
        }
    }
}

#[tauri::command]
pub fn os_evidence_list(limit: Option<usize>) -> Result<Vec<OsEvidenceEntry>, String> {
    let root = project_output_dir()?;
    let max_items = limit.unwrap_or(80).clamp(1, 300);
    let mut paths = Vec::new();
    visit_evidence_files(&root, &mut paths, 0);
    let mut rows = paths
        .iter()
        .filter_map(|p| evidence_entry(p))
        .collect::<Vec<_>>();
    let mut ledger_paths = Vec::new();
    for dir in cognitive_kernel_ledger_dirs() {
        visit_cognitive_ledger_files(&dir, &mut ledger_paths);
    }
    for ledger_path in ledger_paths {
        rows.extend(cognitive_ledger_entries(&ledger_path));
    }
    rows.sort_by(|a, b| b.generated_at.cmp(&a.generated_at));
    rows.truncate(max_items);
    Ok(rows)
}

fn now_secs() -> u64 {
    UNIX_EPOCH.elapsed().map(|d| d.as_secs()).unwrap_or(0)
}

fn payload_or_self(value: &Value) -> &Value {
    value.get("payload").unwrap_or(value)
}

fn string_array(value: Option<&Value>) -> Vec<String> {
    value
        .and_then(|v| v.as_array())
        .map(|items| {
            items
                .iter()
                .filter_map(|item| item.as_str().map(|s| s.trim().to_string()))
                .filter(|s| !s.is_empty())
                .collect()
        })
        .unwrap_or_default()
}

fn bump(map: &mut BTreeMap<String, usize>, key: impl Into<String>) {
    let key = key.into();
    let normalized = if key.trim().is_empty() {
        "unknown".to_string()
    } else {
        key.trim().to_string()
    };
    *map.entry(normalized).or_insert(0) += 1;
}

fn top_counts(map: BTreeMap<String, usize>, limit: usize) -> Vec<(String, usize)> {
    let mut rows = map.into_iter().collect::<Vec<_>>();
    rows.sort_by(|a, b| b.1.cmp(&a.1).then_with(|| a.0.cmp(&b.0)));
    rows.truncate(limit);
    rows
}

fn evidence_capability_label(row: &OsEvidenceEntry) -> String {
    row.workflow_composition
        .as_ref()
        .and_then(|v| v.get("selected_capability_id"))
        .and_then(|v| v.as_str())
        .or_else(|| {
            row.capability_semantic
                .as_ref()
                .and_then(|v| v.get("selected"))
                .and_then(|v| v.get("id"))
                .and_then(|v| v.as_str())
        })
        .or_else(|| {
            row.workflow_composition
                .as_ref()
                .and_then(|v| v.get("workflow_id"))
                .and_then(|v| v.as_str())
        })
        .or_else(|| {
            row.mission_preview
                .as_ref()
                .and_then(|v| v.get("workflow_id"))
                .and_then(|v| v.as_str())
        })
        .or_else(|| {
            row.template
                .as_ref()
                .and_then(|v| v.get("workflow_id"))
                .and_then(|v| v.as_str())
        })
        .or_else(|| {
            row.route
                .as_ref()
                .and_then(|v| v.get("workflow_id"))
                .and_then(|v| v.as_str())
        })
        .or_else(|| {
            row.metrics
                .as_ref()
                .and_then(|v| v.get("workflow_id"))
                .and_then(|v| v.as_str())
        })
        .or_else(|| {
            row.intent
                .as_ref()
                .and_then(|v| v.get("task_type"))
                .and_then(|v| v.as_str())
        })
        .or_else(|| {
            row.metrics
                .as_ref()
                .and_then(|v| v.get("task_type"))
                .and_then(|v| v.as_str())
        })
        .unwrap_or("unknown")
        .to_string()
}

fn is_within_days(row: &OsEvidenceEntry, days: u64, now: u64) -> bool {
    row.generated_at > 0
        && row.generated_at <= now
        && now.saturating_sub(row.generated_at) <= days * 24 * 60 * 60
}

fn governance_summary(rows: &[&OsEvidenceEntry]) -> OsEvidenceGovernanceSummary {
    let mut tool_quality = BTreeMap::<String, usize>::new();
    let mut quality_issues = BTreeMap::<String, usize>::new();
    let mut failure_classes = BTreeMap::<String, usize>::new();
    let mut recovery_strategies = BTreeMap::<String, usize>::new();
    let mut memory_types = BTreeMap::<String, usize>::new();
    let mut quality_reports = 0usize;
    let mut blocked_reports = 0usize;
    let mut recovery_candidates = 0usize;
    let mut failure_learning_records = 0usize;

    for row in rows {
        for report in row.tool_quality_reports.iter() {
            let payload = payload_or_self(report);
            quality_reports += 1;
            if payload
                .get("blocks_execution")
                .and_then(|v| v.as_bool())
                .unwrap_or(false)
            {
                blocked_reports += 1;
            }
            bump(
                &mut tool_quality,
                payload
                    .get("tool")
                    .and_then(|v| v.as_str())
                    .unwrap_or("unknown_tool"),
            );
            for issue in string_array(payload.get("issues")) {
                bump(&mut quality_issues, issue);
            }
        }

        for scorecard in row.recovery_scorecards.iter() {
            let payload = payload_or_self(scorecard);
            if let Some(strategy) = payload
                .get("candidate_strategy")
                .and_then(|v| v.as_str())
                .filter(|s| !s.trim().is_empty())
            {
                recovery_candidates += 1;
                bump(&mut recovery_strategies, strategy);
            }
        }

        for record in row.failure_learning_records.iter() {
            let payload = payload_or_self(record);
            failure_learning_records += 1;
            bump(
                &mut failure_classes,
                payload
                    .get("failure_class")
                    .and_then(|v| v.as_str())
                    .unwrap_or("unknown"),
            );
            let memory_type = payload
                .get("memory_write")
                .and_then(|v| v.get("memory_type"))
                .and_then(|v| v.as_str())
                .unwrap_or("unknown_memory");
            bump(&mut memory_types, memory_type);
        }
    }

    OsEvidenceGovernanceSummary {
        evidence_count: rows.len(),
        quality_reports,
        blocked_reports,
        block_rate: if quality_reports == 0 {
            0.0
        } else {
            blocked_reports as f64 / quality_reports as f64
        },
        recovery_candidates,
        failure_learning_records,
        tool_quality_top: top_counts(tool_quality, 8),
        quality_issue_top: top_counts(quality_issues, 8),
        failure_class_top: top_counts(failure_classes, 8),
        recovery_strategy_top: top_counts(recovery_strategies, 8),
        memory_type_top: top_counts(memory_types, 8),
    }
}

fn governance_suggestion(
    severity: &str,
    category: &str,
    message: impl Into<String>,
    action: impl Into<String>,
) -> OsEvidenceGovernanceSuggestion {
    OsEvidenceGovernanceSuggestion {
        severity: severity.to_string(),
        category: category.to_string(),
        message: message.into(),
        action: action.into(),
    }
}

fn capability_health(
    capability: &str,
    days: u64,
    summary: &OsEvidenceGovernanceSummary,
) -> OsEvidenceCapabilityHealth {
    let recovery_density = if summary.evidence_count == 0 {
        0.0
    } else {
        summary.recovery_candidates as f64 / summary.evidence_count as f64
    };
    let learning_density = if summary.evidence_count == 0 {
        0.0
    } else {
        summary.failure_learning_records as f64 / summary.evidence_count as f64
    };
    let top_issue = summary
        .quality_issue_top
        .first()
        .map(|(name, count)| format!("{name} ({count})"))
        .unwrap_or_default();

    let mut score = 100_i64;
    let mut suggestions = Vec::new();

    if summary.evidence_count == 0 {
        return OsEvidenceCapabilityHealth {
            capability: capability.to_string(),
            days,
            score: 0,
            level: "no_data".to_string(),
            evidence_count: 0,
            block_rate: 0.0,
            recovery_density: 0.0,
            learning_density: 0.0,
            top_issue,
            suggestions: vec![governance_suggestion(
                "info",
                "evidence",
                "这个时间窗口还没有可评估的 Evidence。",
                "继续运行真实任务并确保 TurnClosure、VerificationReport 和 FailureLearningRecord 落盘。",
            )],
        };
    }

    if summary.block_rate >= 0.50 {
        score -= 40;
        suggestions.push(governance_suggestion(
            "critical",
            "quality",
            format!(
                "质量阻断率达到 {}%。",
                (summary.block_rate * 100.0).round() as u64
            ),
            "优先查看低质量工具和质量问题 Top，为高频问题补 verification_contract 或替换执行路径。",
        ));
    } else if summary.block_rate >= 0.25 {
        score -= 25;
        suggestions.push(governance_suggestion(
            "warning",
            "quality",
            format!(
                "质量阻断率达到 {}%。",
                (summary.block_rate * 100.0).round() as u64
            ),
            "检查阻断报告是否来自同一工具或同一失败类别，并把规则沉淀到 Capability Manifest。",
        ));
    } else if summary.block_rate > 0.0 {
        score -= 10;
        suggestions.push(governance_suggestion(
            "info",
            "quality",
            format!("存在 {} 条阻断型质量报告。", summary.blocked_reports),
            "继续观察是否形成重复失败，必要时为该能力补充失败学习和恢复路径。",
        ));
    }

    if summary.blocked_reports > 0 && summary.recovery_candidates == 0 {
        score -= 15;
        suggestions.push(governance_suggestion(
            "warning",
            "recovery",
            "有阻断失败，但没有可用恢复候选。",
            "为对应 Skill/MCP 增加 recovery_playbook，让 RecoveryPlanner 能按失败原因选择下一条路径。",
        ));
    }

    if summary.blocked_reports > 0 && summary.failure_learning_records == 0 {
        score -= 15;
        suggestions.push(governance_suggestion(
            "warning",
            "learning",
            "有阻断失败，但没有写入失败学习记忆。",
            "检查 VerificationReport 到 FailureLearningLoop 的链路，确保失败原因、上下文和恢复结果进入统一记忆。",
        ));
    }

    if let Some((issue, count)) = summary.quality_issue_top.first() {
        if *count >= 3 {
            score -= 10;
            suggestions.push(governance_suggestion(
                "warning",
                "quality",
                format!("高频质量问题：{issue}，出现 {count} 次。"),
                "把这个问题升级为专项门禁：执行前预检、执行后校验、失败后自动换路。",
            ));
        }
    }

    if summary.evidence_count < 3 {
        score -= 5;
        suggestions.push(governance_suggestion(
            "info",
            "confidence",
            "样本量较少，健康分可信度有限。",
            "继续积累真实任务 Evidence，至少覆盖成功、失败、恢复三类样本。",
        ));
    }

    if suggestions.is_empty() {
        suggestions.push(governance_suggestion(
            "info",
            "quality",
            "当前能力健康，没有发现明显治理风险。",
            "保持自动监控，继续沉淀成功样本和恢复样本。",
        ));
    }

    let score = score.clamp(0, 100) as u64;
    let level = if score >= 85 {
        "healthy"
    } else if score >= 70 {
        "watch"
    } else if score >= 50 {
        "degraded"
    } else {
        "critical"
    };

    OsEvidenceCapabilityHealth {
        capability: capability.to_string(),
        days,
        score,
        level: level.to_string(),
        evidence_count: summary.evidence_count,
        block_rate: summary.block_rate,
        recovery_density,
        learning_density,
        top_issue,
        suggestions,
    }
}

#[tauri::command]
pub fn os_evidence_governance_index(
    limit: Option<usize>,
) -> Result<OsEvidenceGovernanceIndex, String> {
    let source_limit = limit.unwrap_or(300).clamp(1, 300);
    let rows = os_evidence_list(Some(source_limit))?;
    let now = now_secs();
    let mut capability_counts = BTreeMap::<String, usize>::new();
    for row in rows.iter() {
        bump(&mut capability_counts, evidence_capability_label(row));
    }
    let capability_options = top_counts(capability_counts, 48);
    let mut capability_names = vec!["all".to_string()];
    capability_names.extend(capability_options.iter().map(|(name, _)| name.clone()));

    let mut windows = Vec::new();
    for days in [7_u64, 14, 30] {
        let rows_in_window = rows
            .iter()
            .filter(|row| is_within_days(row, days, now))
            .collect::<Vec<_>>();
        windows.push(OsEvidenceGovernanceWindow {
            days,
            capability: "all".to_string(),
            summary: governance_summary(&rows_in_window),
        });
        for capability in capability_names
            .iter()
            .filter(|name| name.as_str() != "all")
        {
            let filtered = rows_in_window
                .iter()
                .copied()
                .filter(|row| evidence_capability_label(row) == capability.as_str())
                .collect::<Vec<_>>();
            if filtered.is_empty() {
                continue;
            }
            windows.push(OsEvidenceGovernanceWindow {
                days,
                capability: capability.clone(),
                summary: governance_summary(&filtered),
            });
        }
    }

    let mut health = windows
        .iter()
        .map(|window| capability_health(&window.capability, window.days, &window.summary))
        .collect::<Vec<_>>();
    health.sort_by(|a, b| {
        a.score
            .cmp(&b.score)
            .then_with(|| b.evidence_count.cmp(&a.evidence_count))
            .then_with(|| a.days.cmp(&b.days))
            .then_with(|| a.capability.cmp(&b.capability))
    });

    let index_path = project_output_dir()?.join("os_evidence_governance_index.json");
    let index = OsEvidenceGovernanceIndex {
        generated_at: now,
        source_limit,
        total_evidence: rows.len(),
        index_path: index_path.to_string_lossy().into_owned(),
        capability_options,
        windows,
        health,
    };
    let value = serde_json::to_value(&index)
        .map_err(|e| format!("serialize governance index failed: {e}"))?;
    write_json_file(&index_path, &value)?;
    Ok(index)
}

#[tauri::command]
pub fn os_evidence_stats(limit: Option<usize>) -> Result<OsEvidenceStats, String> {
    let rows = os_evidence_list(limit.or(Some(300)))?;
    let total = rows.len();
    let passed = rows.iter().filter(|row| row.ok).count();
    let failed = total.saturating_sub(passed);
    let mut duration_sum: u128 = 0;
    let mut duration_count: u128 = 0;
    let mut attempt_sum: u128 = 0;
    let mut attempt_count: u128 = 0;
    let mut failures = std::collections::BTreeMap::<String, usize>::new();
    let mut workflow_counts = std::collections::BTreeMap::<String, usize>::new();
    let mut workflow_pass = std::collections::BTreeMap::<String, (usize, usize)>::new();

    for row in rows.iter() {
        let metrics = row.metrics.as_ref();
        if let Some(ms) = metrics
            .and_then(|v| v.get("duration_ms"))
            .and_then(|v| v.as_u64())
        {
            duration_sum += ms as u128;
            duration_count += 1;
        }
        let attempts = metrics
            .and_then(|v| v.get("attempt_count"))
            .and_then(|v| v.as_u64())
            .unwrap_or(row.attempts.len() as u64);
        if attempts > 0 {
            attempt_sum += attempts as u128;
            attempt_count += 1;
        }
        let failure = metrics
            .and_then(|v| v.get("failure_class"))
            .and_then(|v| v.as_str())
            .unwrap_or(if row.ok { "none" } else { "unknown" })
            .to_string();
        if !row.ok || failure != "none" {
            *failures.entry(failure).or_insert(0) += 1;
        }
        let workflow = metrics
            .and_then(|v| v.get("workflow_id"))
            .and_then(|v| v.as_str())
            .or_else(|| {
                row.route
                    .as_ref()
                    .and_then(|v| v.get("workflow_id"))
                    .and_then(|v| v.as_str())
            })
            .unwrap_or(&row.task)
            .to_string();
        *workflow_counts.entry(workflow.clone()).or_insert(0) += 1;
        let entry = workflow_pass.entry(workflow).or_insert((0, 0));
        entry.0 += 1;
        if row.ok {
            entry.1 += 1;
        }
    }

    let mut failure_top = failures.into_iter().collect::<Vec<_>>();
    failure_top.sort_by(|a, b| b.1.cmp(&a.1));
    failure_top.truncate(8);
    let mut workflow_top = workflow_counts.into_iter().collect::<Vec<_>>();
    workflow_top.sort_by(|a, b| b.1.cmp(&a.1));
    workflow_top.truncate(8);
    let mut workflow_pass_rate = workflow_pass
        .into_iter()
        .map(|(name, (count, pass))| {
            let rate = if count == 0 {
                0.0
            } else {
                pass as f64 / count as f64
            };
            (name, count, pass, rate)
        })
        .collect::<Vec<_>>();
    workflow_pass_rate.sort_by(|a, b| b.1.cmp(&a.1));
    workflow_pass_rate.truncate(8);

    Ok(OsEvidenceStats {
        total,
        passed,
        failed,
        success_rate: if total == 0 {
            0.0
        } else {
            passed as f64 / total as f64
        },
        avg_duration_ms: if duration_count == 0 {
            0
        } else {
            (duration_sum / duration_count) as u64
        },
        avg_attempts: if attempt_count == 0 {
            0.0
        } else {
            attempt_sum as f64 / attempt_count as f64
        },
        failure_top,
        workflow_top,
        workflow_pass_rate,
    })
}

#[tauri::command]
pub fn os_evidence_open_path(path: String) -> Result<String, String> {
    let target = PathBuf::from(path.trim());
    if !target.exists() {
        return Err("path not found".to_string());
    }
    #[cfg(windows)]
    {
        std::process::Command::new("cmd")
            .arg("/C")
            .arg("start")
            .arg("")
            .arg(&target)
            .spawn()
            .map_err(|e| format!("open path failed: {e}"))?;
    }
    #[cfg(target_os = "macos")]
    {
        std::process::Command::new("open")
            .arg(&target)
            .spawn()
            .map_err(|e| format!("open path failed: {e}"))?;
    }
    #[cfg(all(not(windows), not(target_os = "macos")))]
    {
        std::process::Command::new("xdg-open")
            .arg(&target)
            .spawn()
            .map_err(|e| format!("open path failed: {e}"))?;
    }
    Ok(target.to_string_lossy().into_owned())
}

fn output_subdir(name: &str) -> Result<PathBuf, String> {
    let root = project_output_dir()?;
    Ok(root.join("os_evidence_console_runs").join(name))
}

fn latest_evidence_file(root: &Path) -> Option<PathBuf> {
    let mut paths = Vec::new();
    visit_evidence_files(root, &mut paths, 200);
    paths.sort_by(|a, b| modified_secs(b).cmp(&modified_secs(a)));
    paths.into_iter().next()
}

fn append_stop_timeline(out_dir: &str, pid: u32, ok: bool) -> Result<String, String> {
    let dir = PathBuf::from(out_dir);
    fs::create_dir_all(&dir).map_err(|e| format!("create stop dir failed: {e}"))?;
    let path = latest_evidence_file(&dir).unwrap_or_else(|| dir.join("user_stopped.evidence.json"));
    let mut value = if path.exists() {
        fs::read_to_string(&path)
            .ok()
            .and_then(|text| serde_json::from_str::<Value>(&text).ok())
            .unwrap_or_else(|| serde_json::json!({}))
    } else {
        serde_json::json!({
            "task": "os_evidence_console_task",
            "ok": false,
            "detail": "user_stopped",
            "evidence_path": path.to_string_lossy(),
        })
    };
    if !value.is_object() {
        value = serde_json::json!({});
    }
    let event = serde_json::json!({
        "ts": chrono_like_now(),
        "stage": "user_stopped",
        "status": if ok { "done" } else { "failed" },
        "detail": format!("user requested stop for pid={pid}"),
        "evidence": {
            "pid": pid,
            "stop_ok": ok,
            "out_dir": out_dir,
        }
    });
    if let Some(obj) = value.as_object_mut() {
        obj.insert("ok".to_string(), Value::Bool(false));
        obj.insert(
            "detail".to_string(),
            Value::String("user_stopped".to_string()),
        );
        let timeline = obj
            .entry("timeline".to_string())
            .or_insert_with(|| Value::Array(Vec::new()));
        if let Value::Array(rows) = timeline {
            rows.push(event);
        }
    }
    write_json_file(&path, &value)?;
    Ok(path.to_string_lossy().into_owned())
}

fn chrono_like_now() -> String {
    use std::time::{SystemTime, UNIX_EPOCH};
    let secs = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    format!("{secs}")
}

fn spawn_runner(
    mode: &str,
    input: OsEvidenceLaunchInput,
) -> Result<OsEvidenceLaunchResult, String> {
    let root = crate::l3_spawn::project_root().ok_or("project root not found")?;
    let project_name = input
        .project_name
        .clone()
        .unwrap_or_else(|| "Jachin".to_string());
    let project_path = input.project_path.clone().unwrap_or_default();
    let recipients = input
        .recipients
        .unwrap_or_else(|| vec!["Vivian".to_string()]);
    let recipients_json = serde_json::to_string(&recipients)
        .map_err(|e| format!("serialize recipients failed: {e}"))?;
    let wait_seconds = input.wait_seconds.unwrap_or(120).clamp(10, 600).to_string();
    let out_dir = output_subdir(mode)?;
    fs::create_dir_all(&out_dir).map_err(|e| format!("create output dir failed: {e}"))?;
    let script = root.join("scripts").join("os_evidence_task_runner.py");
    if !script.exists() {
        return Err(format!("runner script not found: {}", script.display()));
    }

    let mut cmd =
        Command::new(std::env::var("JACHIN_PYTHON").unwrap_or_else(|_| "python".to_string()));
    cmd.current_dir(&root)
        .arg(&script)
        .arg("--mode")
        .arg(mode)
        .arg("--project-name")
        .arg(&project_name)
        .arg("--project-path")
        .arg(&project_path)
        .arg("--recipients-json")
        .arg(&recipients_json)
        .arg("--wait-seconds")
        .arg(&wait_seconds)
        .arg("--out-dir")
        .arg(&out_dir)
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    if let Some(template_id) = input.template_id {
        if !template_id.trim().is_empty() {
            cmd.arg("--template-id").arg(template_id);
        }
    }
    if input.dry_run.unwrap_or(false) {
        cmd.arg("--dry-run");
    }
    #[cfg(windows)]
    {
        cmd.creation_flags(0x08000000);
    }
    let child = cmd
        .spawn()
        .map_err(|e| format!("spawn evidence runner failed: {e}"))?;
    let pid = child.id();
    let state = serde_json::json!({
        "mode": mode,
        "pid": pid,
        "status": "running",
        "out_dir": out_dir.to_string_lossy(),
        "project_name": project_name,
        "project_path": project_path,
        "recipients": recipients,
        "started_at": modified_secs(&out_dir),
    });
    let _ = write_json_file(&out_dir.join("run_state.json"), &state);
    Ok(OsEvidenceLaunchResult {
        ok: true,
        mode: mode.to_string(),
        out_dir: out_dir.to_string_lossy().into_owned(),
        pid: Some(pid),
        message: "task runner started".to_string(),
    })
}

fn runner_command(
    mode: &str,
    input: &OsEvidenceLaunchInput,
    out_dir: &Path,
    root: &Path,
) -> Result<Command, String> {
    let project_name = input
        .project_name
        .clone()
        .unwrap_or_else(|| "Jachin".to_string());
    let project_path = input.project_path.clone().unwrap_or_default();
    let recipients = input
        .recipients
        .clone()
        .unwrap_or_else(|| vec!["Vivian".to_string()]);
    let recipients_json = serde_json::to_string(&recipients)
        .map_err(|e| format!("serialize recipients failed: {e}"))?;
    let wait_seconds = input.wait_seconds.unwrap_or(120).clamp(10, 600).to_string();
    let script = root.join("scripts").join("os_evidence_task_runner.py");
    if !script.exists() {
        return Err(format!("runner script not found: {}", script.display()));
    }
    let mut cmd =
        Command::new(std::env::var("JACHIN_PYTHON").unwrap_or_else(|_| "python".to_string()));
    cmd.current_dir(root)
        .arg(&script)
        .arg("--mode")
        .arg(mode)
        .arg("--project-name")
        .arg(&project_name)
        .arg("--project-path")
        .arg(&project_path)
        .arg("--recipients-json")
        .arg(&recipients_json)
        .arg("--wait-seconds")
        .arg(wait_seconds)
        .arg("--out-dir")
        .arg(out_dir);
    if let Some(template_id) = input.template_id.as_ref() {
        if !template_id.trim().is_empty() {
            cmd.arg("--template-id").arg(template_id);
        }
    }
    if input.dry_run.unwrap_or(false) {
        cmd.arg("--dry-run");
    }
    Ok(cmd)
}

#[tauri::command]
pub fn os_evidence_start_standard_demo(
    input: OsEvidenceLaunchInput,
) -> Result<OsEvidenceLaunchResult, String> {
    spawn_runner("standard_demo", input)
}

#[tauri::command]
pub fn os_evidence_start_smoke_matrix(
    input: OsEvidenceLaunchInput,
) -> Result<OsEvidenceLaunchResult, String> {
    spawn_runner("smoke_matrix", input)
}

#[tauri::command]
pub fn os_evidence_start_template(
    input: OsEvidenceLaunchInput,
) -> Result<OsEvidenceLaunchResult, String> {
    spawn_runner("template", input)
}

#[tauri::command]
pub fn os_evidence_preflight(
    input: OsEvidenceLaunchInput,
) -> Result<OsEvidencePreflightResult, String> {
    let root = crate::l3_spawn::project_root().ok_or("project root not found")?;
    let out_dir = output_subdir("preflight")?;
    fs::create_dir_all(&out_dir).map_err(|e| format!("create preflight dir failed: {e}"))?;
    let output = runner_command("preflight", &input, &out_dir, &root)?
        .output()
        .map_err(|e| format!("preflight failed: {e}"))?;
    let stdout = String::from_utf8_lossy(&output.stdout).to_string();
    let stderr = String::from_utf8_lossy(&output.stderr).to_string();
    if !output.status.success() {
        return Err(format!("preflight process failed: {stderr}"));
    }
    let json_text = stdout
        .find('{')
        .and_then(|start| stdout.rfind('}').map(|end| stdout[start..=end].to_string()))
        .unwrap_or(stdout);
    let raw: Value = serde_json::from_str(&json_text)
        .map_err(|e| format!("preflight json invalid: {e}; stdout={json_text}"))?;
    Ok(OsEvidencePreflightResult {
        ok: raw.get("ok").and_then(|v| v.as_bool()).unwrap_or(false),
        checks: raw
            .get("checks")
            .cloned()
            .unwrap_or_else(|| Value::Array(Vec::new())),
        raw,
    })
}

#[tauri::command]
pub fn os_evidence_stop_task(
    pid: Option<u32>,
    out_dir: Option<String>,
) -> Result<OsEvidenceStopResult, String> {
    let Some(pid) = pid else {
        return Ok(OsEvidenceStopResult {
            ok: false,
            pid: None,
            out_dir,
            message: "pid_missing".to_string(),
        });
    };

    #[cfg(windows)]
    let output = Command::new("taskkill")
        .arg("/PID")
        .arg(pid.to_string())
        .arg("/T")
        .arg("/F")
        .output()
        .map_err(|e| format!("taskkill failed: {e}"))?;

    #[cfg(not(windows))]
    let output = Command::new("kill")
        .arg("-TERM")
        .arg(pid.to_string())
        .output()
        .map_err(|e| format!("kill failed: {e}"))?;

    let ok = output.status.success();
    if let Some(dir) = out_dir.as_ref() {
        let _ = append_stop_timeline(dir, pid, ok);
        let path = PathBuf::from(dir).join("run_state.json");
        let state = serde_json::json!({
            "pid": pid,
            "status": if ok { "stopped" } else { "stop_failed" },
            "out_dir": dir,
        });
        let _ = write_json_file(&path, &state);
    }
    Ok(OsEvidenceStopResult {
        ok,
        pid: Some(pid),
        out_dir,
        message: if ok {
            "stopped".to_string()
        } else {
            "stop_failed".to_string()
        },
    })
}

#[tauri::command]
pub fn os_evidence_config_get() -> Result<OsEvidenceConfig, String> {
    let path = config_path()?;
    if !path.exists() {
        return Ok(default_config());
    }
    let text = fs::read_to_string(&path).map_err(|e| format!("read config failed: {e}"))?;
    let mut cfg: OsEvidenceConfig =
        serde_json::from_str(&text).unwrap_or_else(|_| default_config());
    cfg.project_name = cfg.project_name.trim().to_string();
    cfg.project_path = cfg.project_path.trim().to_string();
    cfg.recipients = cfg
        .recipients
        .into_iter()
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
        .collect();
    if cfg.project_name.is_empty() {
        cfg.project_name = "Jachin".to_string();
    }
    if cfg.recipients.is_empty() {
        cfg.recipients = vec!["Vivian".to_string()];
    }
    cfg.wait_seconds = cfg.wait_seconds.clamp(10, 600);
    Ok(cfg)
}

#[tauri::command]
pub fn os_evidence_config_set(config: OsEvidenceConfig) -> Result<OsEvidenceConfig, String> {
    let cfg = OsEvidenceConfig {
        project_name: if config.project_name.trim().is_empty() {
            "Jachin".to_string()
        } else {
            config.project_name.trim().to_string()
        },
        project_path: config.project_path.trim().to_string(),
        recipients: config
            .recipients
            .into_iter()
            .map(|s| s.trim().to_string())
            .filter(|s| !s.is_empty())
            .collect::<Vec<_>>(),
        wait_seconds: config.wait_seconds.clamp(10, 600),
        dry_run: config.dry_run,
    };
    let cfg = if cfg.recipients.is_empty() {
        OsEvidenceConfig {
            recipients: vec!["Vivian".to_string()],
            ..cfg
        }
    } else {
        cfg
    };
    let path = config_path()?;
    let value = serde_json::to_value(&cfg).map_err(|e| format!("serialize config failed: {e}"))?;
    write_json_file(&path, &value)?;
    Ok(cfg)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn collects_failure_learning_records_from_nested_ledger_events() {
        let value = json!({
            "events": [
                {
                    "event_type": "failure_learning_recorded",
                    "payload": {
                        "failure_class": "tool_quality_failed",
                        "next_strategy": "regenerate_clean_summary",
                        "memory_write": {
                            "memory_type": "failure_hint",
                            "content": "summary failed quality gate"
                        }
                    }
                }
            ]
        });
        let mut rows = Vec::new();
        collect_failure_learning_records(&value, &mut rows);
        assert_eq!(rows.len(), 1);
        assert_eq!(
            rows[0].get("event_type").and_then(|v| v.as_str()),
            Some("failure_learning_recorded")
        );
        assert_eq!(
            rows[0]
                .get("payload")
                .and_then(|v| v.get("failure_class"))
                .and_then(|v| v.as_str()),
            Some("tool_quality_failed")
        );
    }

    #[test]
    fn collects_failure_learning_records_from_plain_payloads() {
        let value = json!({
            "failure_class": "target_not_found",
            "next_strategy": "resolve_target_from_memory_or_ask_user",
            "memory_write": { "memory_type": "failure_hint" }
        });
        let mut rows = Vec::new();
        collect_failure_learning_records(&value, &mut rows);
        assert_eq!(rows.len(), 1);
        assert_eq!(
            rows[0].get("failure_class").and_then(|v| v.as_str()),
            Some("target_not_found")
        );
    }
}
