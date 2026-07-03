//! 主进程：写入热更新任务 JSON 并启动 `jachin-updater-helper`（与主 exe 同目录，或打包资源目录）。

use crate::updater_common::{
    embedded_updater_pubkey_b64, normalize_signature_for_hot_update_job,
    signature_wire_debug_summary, HotUpdateJob, HotUpdatePrepareResult,
};
use crate::updater_debug_log;
use serde::Deserialize;
use serde_json::json;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::{Duration, Instant};
use tauri::path::BaseDirectory;
use tauri::Emitter;
use tauri::Manager;

#[cfg(windows)]
use std::os::windows::process::CommandExt;

#[cfg(windows)]
const DETACHED_PROCESS: u32 = 0x0000_0008;
#[cfg(windows)]
const CREATE_NEW_PROCESS_GROUP: u32 = 0x0000_0200;

fn emit_hot_update_prepare_result<R: serde::Serialize>(app: &tauri::AppHandle, payload: &R) {
    let mut main_err: Option<String> = None;
    if let Some(w) = app.get_webview_window("main") {
        match w.emit("hot-update-prepare-result", payload) {
            Ok(()) => {
                updater_debug_log::append_line(
                    "hot_update_event",
                    "emit hot-update-prepare-result ok (main webview)",
                );
                return;
            }
            Err(e) => main_err = Some(e.to_string()),
        }
    }
    match app.emit("hot-update-prepare-result", payload) {
        Ok(()) => updater_debug_log::append_line(
            "hot_update_event",
            "emit hot-update-prepare-result ok (app broadcast)",
        ),
        Err(e) => updater_debug_log::append_line(
            "hot_update_event",
            &format!(
                "emit hot-update-prepare-result FAILED: app_err={e} main_err={}",
                main_err.unwrap_or_else(|| "(no main window)".into())
            ),
        ),
    }
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SpawnHotUpdatePayload {
    pub download_url: String,
    pub signature: String,
    pub new_version: String,
}

#[cfg(windows)]
const HELPER_EXE_NAME: &str = "jachin-updater-helper.exe";
#[cfg(not(windows))]
const HELPER_EXE_NAME: &str = "jachin-updater-helper";

/// 解析热更新助手路径：环境变量 → 与主程序同目录 → Tauri 打包资源目录。
pub fn resolve_helper_executable(app: &tauri::AppHandle) -> Result<PathBuf, String> {
    if let Ok(p) = std::env::var("JACHIN_UPDATER_HELPER_EXE") {
        let p = PathBuf::from(p.trim());
        if p.is_file() {
            return Ok(p);
        }
    }

    let exe_dir = std::env::current_exe()
        .ok()
        .and_then(|p| p.parent().map(Path::to_path_buf));
    if let Some(ref dir) = exe_dir {
        let beside = dir.join(HELPER_EXE_NAME);
        if beside.is_file() {
            return Ok(beside);
        }
    }

    if let Ok(resolved) = app.path().resolve(HELPER_EXE_NAME, BaseDirectory::Resource) {
        if resolved.is_file() {
            return Ok(resolved);
        }
    }

    let dir_hint = exe_dir
        .as_ref()
        .map(|d| d.display().to_string())
        .unwrap_or_else(|| "(无法解析主程序目录)".into());

    Err(format!(
        "未找到热更新助手 {HELPER_EXE_NAME}。\n\
         已尝试：① 环境变量 JACHIN_UPDATER_HELPER_EXE ② 与主程序同目录（当前: {dir_hint}）③ 应用资源目录。\n\
         【开发】在 clients/desktop 执行: npm run ensure-updater-helper，或使用 npm run tauri:dev:with-updater 一次起编好助手再 dev（日常可用 npm run tauri:dev 跳过以加快启动）。\n\
         【手动测试】勿只拷贝 jachin-desktop.exe：请把 target/release 下的 {HELPER_EXE_NAME} 与主程序放在同一文件夹（例如与 Downloads 里的 exe 同目录）。"
    ))
}

/// 写入任务文件并启动助手（已分离进程组，主进程退出后助手仍可运行）。
pub fn spawn_hot_update_job(
    app: &tauri::AppHandle,
    payload: SpawnHotUpdatePayload,
) -> Result<(), String> {
    let target_exe = std::env::current_exe().map_err(|e| e.to_string())?;
    let parent_pid = std::process::id();
    let job = HotUpdateJob {
        parent_pid,
        target_exe: target_exe.clone(),
        download_url: payload.download_url,
        signature: normalize_signature_for_hot_update_job(&payload.signature),
        new_version: payload.new_version,
        prepare_only: false,
        prepare_result_path: None,
        apply_only: false,
        staged_new_exe: None,
        updater_pubkey_wire: Some(embedded_updater_pubkey_b64().to_string()),
    };
    let new_version_log = job.new_version.clone();

    let job_path = std::env::temp_dir().join(format!(
        "jachin-hot-update-{}-{}.json",
        parent_pid,
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_millis())
            .unwrap_or(0)
    ));

    let f = std::fs::File::create(&job_path).map_err(|e| format!("创建任务文件失败: {e}"))?;
    serde_json::to_writer_pretty(f, &job).map_err(|e| format!("写入任务 JSON: {e}"))?;

    let helper = resolve_helper_executable(app)?;

    let mut cmd = Command::new(&helper);
    cmd.arg("--job").arg(&job_path);
    #[cfg(windows)]
    cmd.creation_flags(DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP);

    cmd.spawn()
        .map_err(|e| format!("启动 jachin-updater-helper 失败: {e}"))?;

    updater_debug_log::append_line(
        "rust_spawn",
        &format!(
            "spawn_hot_update helper={} job={} target={} new_version={}",
            helper.display(),
            job_path.display(),
            target_exe.display(),
            new_version_log
        ),
    );
    Ok(())
}

/// 准备阶段：助手下载并校验，主进程不退出；结果通过事件 `hot-update-prepare-result` 推送。
pub fn spawn_hot_update_prepare_job(
    app: &tauri::AppHandle,
    payload: SpawnHotUpdatePayload,
) -> Result<(), String> {
    let target_exe = std::env::current_exe().map_err(|e| e.to_string())?;
    let parent_pid = std::process::id();
    let ts = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_millis())
        .unwrap_or(0);

    let result_path = std::env::temp_dir().join(format!(
        "jachin-hot-prepare-result-{}-{}.json",
        parent_pid, ts
    ));

    let job = HotUpdateJob {
        parent_pid,
        target_exe: target_exe.clone(),
        download_url: payload.download_url.clone(),
        signature: normalize_signature_for_hot_update_job(&payload.signature),
        new_version: payload.new_version.clone(),
        prepare_only: true,
        prepare_result_path: Some(result_path.clone()),
        apply_only: false,
        staged_new_exe: None,
        updater_pubkey_wire: Some(embedded_updater_pubkey_b64().to_string()),
    };

    let job_path =
        std::env::temp_dir().join(format!("jachin-hot-prepare-{}-{}.json", parent_pid, ts));

    let f = std::fs::File::create(&job_path).map_err(|e| format!("创建任务文件失败: {e}"))?;
    serde_json::to_writer_pretty(f, &job).map_err(|e| format!("写入任务 JSON: {e}"))?;

    let helper = resolve_helper_executable(app)?;

    let mut cmd = Command::new(&helper);
    cmd.arg("--job").arg(&job_path);
    #[cfg(windows)]
    cmd.creation_flags(DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP);

    cmd.spawn()
        .map_err(|e| format!("启动 jachin-updater-helper 失败: {e}"))?;

    updater_debug_log::append_line(
        "rust_spawn",
        &format!(
            "spawn_hot_update_prepare helper={} job={} result_watch={} new_version={} parent_pid={}",
            helper.display(),
            job_path.display(),
            result_path.display(),
            payload.new_version,
            parent_pid
        ),
    );
    updater_debug_log::append_line(
        "rust_spawn",
        &format!(
            "sig_from_frontend {}",
            signature_wire_debug_summary(&payload.signature)
        ),
    );
    updater_debug_log::append_line(
        "rust_spawn",
        &format!(
            "sig_after_normalize {}",
            signature_wire_debug_summary(&normalize_signature_for_hot_update_job(
                &payload.signature
            ))
        ),
    );

    let app_handle = app.clone();
    let watch_path = result_path.clone();
    let ver_for_timeout = payload.new_version.clone();
    std::thread::spawn(move || {
        let deadline = Instant::now() + Duration::from_secs(920);
        let mut polls: u32 = 0;
        loop {
            if watch_path.is_file() {
                match std::fs::read_to_string(&watch_path) {
                    Ok(txt) => match serde_json::from_str::<HotUpdatePrepareResult>(&txt) {
                        Ok(r) => {
                            updater_debug_log::append_line(
                                "prepare_poll",
                                &format!(
                                    "result_file ok={} staged_len={} err={:?} path={}",
                                    r.ok,
                                    r.staged_new_exe.as_ref().map(|s| s.len()).unwrap_or(0),
                                    r.error,
                                    watch_path.display()
                                ),
                            );
                            emit_hot_update_prepare_result(&app_handle, &r);
                        }
                        Err(e) => {
                            emit_hot_update_prepare_result(
                                &app_handle,
                                &json!({
                                    "ok": false,
                                    "stagedNewExe": serde_json::Value::Null,
                                    "newVersion": ver_for_timeout,
                                    "error": format!("解析准备结果失败: {e}"),
                                }),
                            );
                        }
                    },
                    Err(e) => {
                        emit_hot_update_prepare_result(
                            &app_handle,
                            &json!({
                                "ok": false,
                                "stagedNewExe": serde_json::Value::Null,
                                "newVersion": ver_for_timeout,
                                "error": format!("读取准备结果失败: {e}"),
                            }),
                        );
                    }
                }
                break;
            }
            polls = polls.wrapping_add(1);
            if polls > 0 && polls % 75 == 0 {
                updater_debug_log::append_line(
                    "prepare_poll",
                    &format!(
                        "still_waiting_for_result_file polls={} elapsed_s≈{} path={}",
                        polls,
                        polls.saturating_mul(400) / 1000,
                        watch_path.display()
                    ),
                );
            }
            if Instant::now() > deadline {
                updater_debug_log::append_line(
                    "prepare_poll",
                    &format!(
                        "TIMEOUT no_result_file after polls={} path={}",
                        polls,
                        watch_path.display()
                    ),
                );
                emit_hot_update_prepare_result(
                    &app_handle,
                    &json!({
                        "ok": false,
                        "stagedNewExe": serde_json::Value::Null,
                        "newVersion": ver_for_timeout,
                        "error": "等待更新准备超时（约 15 分钟），请检查网络后重试。",
                    }),
                );
                break;
            }
            std::thread::sleep(Duration::from_millis(400));
        }
    });

    Ok(())
}

/// 应用阶段：助手等待本进程退出后替换 exe 并启动新版本（调用方须在 spawn 后退出主程序）。
pub fn spawn_hot_update_apply_job(
    app: &tauri::AppHandle,
    staged_new_exe: PathBuf,
    new_version: String,
) -> Result<(), String> {
    let target_exe = std::env::current_exe().map_err(|e| e.to_string())?;
    let parent_pid = std::process::id();
    let ts = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_millis())
        .unwrap_or(0);

    let job = HotUpdateJob {
        parent_pid,
        target_exe: target_exe.clone(),
        download_url: String::new(),
        signature: String::new(),
        new_version,
        prepare_only: false,
        prepare_result_path: None,
        apply_only: true,
        staged_new_exe: Some(staged_new_exe.clone()),
        updater_pubkey_wire: Some(embedded_updater_pubkey_b64().to_string()),
    };

    let job_path =
        std::env::temp_dir().join(format!("jachin-hot-apply-{}-{}.json", parent_pid, ts));

    let f = std::fs::File::create(&job_path).map_err(|e| format!("创建任务文件失败: {e}"))?;
    serde_json::to_writer_pretty(f, &job).map_err(|e| format!("写入任务 JSON: {e}"))?;

    let helper = resolve_helper_executable(app)?;

    let mut cmd = Command::new(&helper);
    cmd.arg("--job").arg(&job_path);
    #[cfg(windows)]
    cmd.creation_flags(DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP);

    cmd.spawn()
        .map_err(|e| format!("启动 jachin-updater-helper（应用阶段）失败: {e}"))?;

    updater_debug_log::append_line(
        "rust_spawn",
        &format!(
            "spawn_hot_update_apply helper={} job={} staged={} target={}",
            helper.display(),
            job_path.display(),
            staged_new_exe.display(),
            target_exe.display()
        ),
    );
    Ok(())
}
