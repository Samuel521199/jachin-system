//! 热更新助手与主程序共享：pubkey、minisign 校验、用户数据目录检查、任务 JSON。

use base64::Engine;
use minisign_verify::{PublicKey, Signature};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::io::{Read, Seek, SeekFrom};
use std::path::{Path, PathBuf};
use std::sync::OnceLock;

/// 与 `tauri.conf.json` 中 `identifier` 一致，用于定位 `%LOCALAPPDATA%\com.jachin.desktop`。
pub const TAURI_APP_IDENTIFIER_DIR: &str = "com.jachin.desktop";

/// 热更新调试日志目录：环境变量 `JACHIN_HOT_UPDATE_DEBUG_DIR`（非空）优先，否则默认 `D:\zzz\jachin`（与历史约定一致）。
pub fn hot_update_debug_log_dir() -> PathBuf {
    std::env::var("JACHIN_HOT_UPDATE_DEBUG_DIR")
        .ok()
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from(r"D:\zzz\jachin"))
}

/// 下载物 SHA256（十六进制），用于与发布端核对是否下错包。
pub fn hot_update_payload_sha256_hex(data: &[u8]) -> String {
    let digest = Sha256::digest(data);
    digest.iter().map(|b| format!("{b:02x}")).collect()
}

/// 读取文件首尾各一段，判断内容是否像 NSIS 安装包（与文件名无关）。
///
/// 用于避免热更新把安装包字节复制为 `jachin-desktop.exe` 后，桌面快捷方式仍指向该路径却启动安装向导。
pub fn sniff_file_looks_like_windows_nsis_installer_package(path: &Path) -> std::io::Result<bool> {
    const CHUNK: u64 = 768 * 1024;
    let mut f = std::fs::File::open(path)?;
    let len = f.metadata()?.len();
    if len == 0 {
        return Ok(false);
    }
    let head_n = std::cmp::min(CHUNK, len) as usize;
    let mut buf = vec![0u8; head_n];
    f.read_exact(&mut buf)?;
    let mut hay = String::from_utf8_lossy(&buf).into_owned();
    if len > CHUNK {
        let start = len.saturating_sub(CHUNK);
        f.seek(SeekFrom::Start(start))?;
        let tail_n = (len - start) as usize;
        let mut tail = vec![0u8; tail_n];
        f.read_exact(&mut tail)?;
        hay.push_str(&String::from_utf8_lossy(&tail));
    }
    Ok(hay.contains("Nullsoft Install System") || hay.contains("NSIS Error"))
}

/// 不落盘完整 `signature`，仅输出长度、换行统计、是否像明文 `.sig`、Base64 可提取长度与头尾片段，便于对照 API/任务 JSON。
pub fn signature_wire_debug_summary(wire: &str) -> String {
    let nl = wire.chars().filter(|&c| c == '\n').count();
    let cr = wire.chars().filter(|&c| c == '\r').count();
    let starts_untrusted = wire.trim_start().starts_with("untrusted comment:");
    let compact: String = wire
        .chars()
        .filter(|c| matches!(c, 'A'..='Z' | 'a'..='z' | '0'..='9' | '+' | '/' | '='))
        .collect();
    let head: String = compact.chars().take(32).collect();
    let tail: String = compact
        .chars()
        .rev()
        .take(16)
        .collect::<String>()
        .chars()
        .rev()
        .collect();
    format!(
        "byte_len={} nl={} cr={} plaintext_untrusted={} b64ish_compact_len={} head32={} tail16={}",
        wire.len(),
        nl,
        cr,
        starts_untrusted,
        compact.len(),
        head,
        tail
    )
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct HotUpdateJob {
    pub parent_pid: u32,
    pub target_exe: PathBuf,
    /// `apply_only` 时可为空。
    #[serde(default)]
    pub download_url: String,
    /// 与 Nexus / Tauri 约定：标准 Base64（整份 .sig 或经 API 规范化后的串）；`apply_only` 时可为空。
    #[serde(default)]
    pub signature: String,
    pub new_version: String,
    /// 仅下载并校验，将结果写入 `prepare_result_path` 后退出，不等待主进程、不替换 exe。
    #[serde(default)]
    pub prepare_only: bool,
    #[serde(default)]
    pub prepare_result_path: Option<PathBuf>,
    /// 仅等待主进程退出后，用 `staged_new_exe` 覆盖 `target_exe` 并启动新版本。
    #[serde(default)]
    pub apply_only: bool,
    #[serde(default)]
    pub staged_new_exe: Option<PathBuf>,
    /// 主程序构建时嵌入的 `plugins.updater.pubkey`（与主 exe 一致）。缺省时助手回退用自己编译时嵌入，易与旧版助手混用导致验签失败。
    #[serde(default)]
    pub updater_pubkey_wire: Option<String>,
}

/// 助手 `prepare_only` 阶段写入的结果文件（与前端事件 `hot-update-prepare-result` 字段一致，camelCase）。
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct HotUpdatePrepareResult {
    pub ok: bool,
    pub staged_new_exe: Option<String>,
    pub new_version: String,
    pub error: Option<String>,
}

/// 编译时从 tauri.conf.json 读取 updater pubkey，与主程序一致。
pub fn embedded_updater_pubkey_b64() -> &'static str {
    static PUB: OnceLock<String> = OnceLock::new();
    PUB.get_or_init(|| {
        let conf = include_str!(concat!(env!("CARGO_MANIFEST_DIR"), "/tauri.conf.json"));
        let v: serde_json::Value =
            serde_json::from_str(conf).expect("parse tauri.conf.json for updater pubkey");
        v.pointer("/plugins/updater/pubkey")
            .and_then(|x| x.as_str())
            .expect("plugins.updater.pubkey missing")
            .to_string()
    })
    .as_str()
}

/// 热更新验签用 pubkey：优先任务 JSON（与当前主程序同次构建），缺省或非空才回退到助手内嵌（兼容旧任务文件）。
pub fn resolve_updater_pubkey_for_job(job: &HotUpdateJob) -> &str {
    job.updater_pubkey_wire
        .as_deref()
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .unwrap_or_else(|| embedded_updater_pubkey_b64())
}

/// 解码前强制去掉所有换行/回车（MIME 折行、JSON 字面 `\\n`、误粘贴等），避免 `base64` 在 offset≈76 报 Invalid symbol 10。
#[inline]
fn strip_crlf(s: &str) -> String {
    s.replace('\n', "").replace('\r', "")
}

/// 标准 minisign `.sig` 明文为 4 行：`untrusted comment` / **签名行** / `trusted comment` / **签名行**。
/// 对第 2、4 行再剥一次换行并只保留 Base64 字母表，避免这两行被误折行或夹带 `\\r`。
fn normalize_minisign_plaintext_sig(s: &str) -> String {
    let lines: Vec<String> = s
        .lines()
        .map(|l| l.trim_end_matches('\r').trim())
        .filter(|l| !l.is_empty())
        .map(str::to_string)
        .collect();

    if lines.len() >= 4
        && lines[0].starts_with("untrusted comment:")
        && lines[2].starts_with("trusted comment:")
    {
        let clean1: String = strip_crlf(&lines[1])
            .chars()
            .filter(|c| matches!(c, 'A'..='Z' | 'a'..='z' | '0'..='9' | '+' | '/' | '='))
            .collect();
        let clean2: String = strip_crlf(&lines[3])
            .chars()
            .filter(|c| matches!(c, 'A'..='Z' | 'a'..='z' | '0'..='9' | '+' | '/' | '='))
            .collect();
        return format!("{}\n{}\n{}\n{}", lines[0], clean1, lines[2], clean2);
    }

    lines.join("\n")
}

/// 去掉尾部 `=` 后按长度补全标准 Base64 填充，缓解手工粘贴少写 `=` 导致的 Invalid padding。
fn normalize_base64_padding(compact: &str) -> Result<String, String> {
    let body = compact.trim_end_matches('=');
    if body.is_empty() {
        return Err("base64 去填充后无有效字符".into());
    }
    let rem = body.len() % 4;
    if rem == 1 {
        return Err("base64 长度非法（有效体 mod 4 == 1，无法仅靠填充修复）".into());
    }
    let pad = (4 - rem) % 4;
    let mut out = body.to_string();
    out.extend(std::iter::repeat('=').take(pad));
    Ok(out)
}

/// 仅对「标准 Base64 字母表」字符做 STANDARD 解码；**先** `strip_crlf`，再过滤非法字符，再 **规范化填充**。勿把明文多行 `.sig` 整段送入此函数。
fn base64_decode_to_string(b64: &str) -> Result<String, String> {
    let no_breaks = strip_crlf(b64);
    let compact: String = no_breaks
        .chars()
        .filter(|c| matches!(c, 'A'..='Z' | 'a'..='z' | '0'..='9' | '+' | '/' | '='))
        .collect();
    if compact.is_empty() {
        return Err("base64 为空（去掉非法字符后无内容）".into());
    }
    let padded = normalize_base64_padding(&compact)?;
    let decoded = base64::engine::general_purpose::STANDARD
        .decode(padded.as_bytes())
        .map_err(|e| format!("base64 decode: {e}"))?;
    String::from_utf8(decoded).map_err(|e| format!("utf8: {e}"))
}

/// Python / MIME 风格 Base64（约每 76 字符插入 `\\n`）的外层 `signature` 解码，与热更新验签里 **第一层** Base64 完全一致。
///
/// 实现：去 `\\r`/`\\n` → 仅保留 Base64 字母表 → `STANDARD.decode`（**不会 panic**，错误为 `Err`）。
#[allow(dead_code)] // 供 `jachin-updater-helper` 单元测试与文档对齐；主程序与 helper 运行路径不引用
pub fn decode_mime_wrapped_signature_outer_base64(wire: &str) -> Result<String, String> {
    base64_decode_to_string(wire.trim())
}

fn is_minisign_sig_plaintext(s: &str) -> bool {
    s.trim_start().starts_with("untrusted comment:")
}

/// 写入热更新任务 JSON 之前对 `signature` 规范化：
/// - 若为明文 `.sig`（`untrusted comment:` 开头）原样保留；
/// - 否则只保留标准 Base64 字符，**去掉 MIME/JSON 带来的中间换行**（避免旧版助手仅 `trim()` 时在 offset≈76 报 Invalid symbol 10）。
#[allow(dead_code)] // 主程序 `updater_spawn` 使用；`jachin-updater-helper` 通过 `#[path]` 同文件编译时未引用
pub fn normalize_signature_for_hot_update_job(signature: &str) -> String {
    let t = signature.trim();
    if is_minisign_sig_plaintext(t) {
        return t.to_string();
    }
    t.chars()
        .filter(|c| matches!(c, 'A'..='Z' | 'a'..='z' | '0'..='9' | '+' | '/' | '='))
        .collect()
}

fn is_minisign_pubkey_plaintext(s: &str) -> bool {
    s.trim_start().starts_with("untrusted comment:")
}

fn decode_pubkey_plaintext_minisign_pub(t: &str) -> Result<PublicKey, String> {
    let normalized: String = t
        .lines()
        .map(|l| l.trim_end_matches('\r').trim())
        .filter(|l| !l.is_empty())
        .collect::<Vec<_>>()
        .join("\n");
    decode_minisign_pubkey_from_pub_file_text(&normalized)
}

/// 解析 `.pub` 第二行 `RW…`：去非法字符后按 [`normalize_base64_padding`] 再 [`PublicKey::from_base64`]。
fn public_key_from_minisign_rw_line_b64(line: &str) -> Result<PublicKey, String> {
    let compact: String = line
        .trim()
        .chars()
        .filter(|c| matches!(c, 'A'..='Z' | 'a'..='z' | '0'..='9' | '+' | '/' | '='))
        .collect();
    let padded = normalize_base64_padding(&compact)?;
    PublicKey::from_base64(&padded).map_err(|e| format!("minisign from_base64(RW 行): {e}"))
}

/// 优先 [`PublicKey::decode`] 整份 `.pub`；失败则对 `RW…` 行截断至 56 字符并补 padding 后再 [`PublicKey::from_base64`]。
fn decode_minisign_pubkey_from_pub_file_text(pub_file: &str) -> Result<PublicKey, String> {
    let t = pub_file.trim_end();
    match PublicKey::decode(t) {
        Ok(pk) => Ok(pk),
        Err(e_decode) => {
            for line in t.lines() {
                let line = line.trim();
                if line.starts_with("RW")
                    && line
                        .chars()
                        .all(|c| matches!(c, 'A'..='Z' | 'a'..='z' | '0'..='9' | '+' | '/' | '='))
                {
                    return public_key_from_minisign_rw_line_b64(line).map_err(|e| {
                        format!("pubkey: {e}（decode 曾失败: {e_decode}）")
                    });
                }
            }
            Err(format!("pubkey: minisign 解析失败: {e_decode}"))
        }
    }
}

/// 常见误配置：第一行是「首行 ASCII 的 Base64」，第二行是 `.pub` 里原始的 `RW…` 密钥行（中间换行）。
/// 整段 **不是** 单一连续外层 Base64，直接 decode 会在约 offset 133 处失败（Invalid last symbol）。
fn try_reconstruct_pubkey_mixed_first_line_b64_second_rw_line(wire: &str) -> Option<String> {
    let lines: Vec<&str> = wire
        .lines()
        .map(|l| l.trim().trim_end_matches('\r'))
        .filter(|l| !l.is_empty())
        .collect();
    if lines.len() != 2 {
        return None;
    }
    let (first_b64, second_raw) = (lines[0], lines[1]);
    if !second_raw.starts_with("RW") {
        return None;
    }
    if !second_raw
        .chars()
        .all(|c| matches!(c, 'A'..='Z' | 'a'..='z' | '0'..='9' | '+' | '/' | '='))
    {
        return None;
    }
    let first_decoded = base64_decode_to_string(first_b64).ok()?;
    if !first_decoded.trim_start().starts_with("untrusted comment:") {
        return None;
    }
    let mut combined = if first_decoded.ends_with('\n') {
        format!("{first_decoded}{second_raw}")
    } else {
        format!("{first_decoded}\n{second_raw}")
    };
    if !combined.ends_with('\n') {
        combined.push('\n');
    }
    Some(combined)
}

/// `tauri.conf.json` 的 `plugins.updater.pubkey`：
/// - 明文整份 `.pub`；
/// - **或** 首行 Base64 + 次行原始 `RW…`（见上）；
/// - **或** 「整份 `.pub` 字节」的单一外层 Base64（推荐）。
fn decode_public_key_from_conf(pubkey_wire: &str) -> Result<PublicKey, String> {
    let t = pubkey_wire.trim().trim_start_matches('\u{feff}');
    if is_minisign_pubkey_plaintext(t) {
        return decode_pubkey_plaintext_minisign_pub(t);
    }
    if let Some(combined) = try_reconstruct_pubkey_mixed_first_line_b64_second_rw_line(t) {
        return decode_minisign_pubkey_from_pub_file_text(&combined);
    }
    let decoded = base64_decode_to_string(pubkey_wire)
        .map_err(|e| format!("pubkey: 外层 Base64 解码失败（若 pubkey 在 json 里折行，应已自动去掉换行；详情 {e}）"))?;
    decode_minisign_pubkey_from_pub_file_text(&decoded)
}

/// 从 Nexus/任务 JSON 的 `signature` 得到 minisign **多行明文** `.sig`，供 `Signature::decode` 使用。
///
/// 正确约定：字段值为 **标准 Base64（整份 .sig 文件字节）**，解码后才是带换行的 minisign 文本。
/// **禁止**把明文多行 `.sig` 直接交给 `base64::decode`（其中含 `:`、空格、`\n`，会在约 76 字节处触发 Invalid symbol 10）。
///
/// 兼容：① 误传了明文 `.sig`；② 双重 Base64；③ 外层带 MIME 折行。
fn resolve_minisign_sig_text_from_wire_impl<F: FnMut(&str)>(wire: &str, mut trace: F) -> Result<String, String> {
    let t = wire.trim();
    trace(&format!(
        "resolve_sig trim_char_len={} {}",
        t.chars().count(),
        signature_wire_debug_summary(wire)
    ));
    if is_minisign_sig_plaintext(t) {
        trace("resolve_sig branch=plaintext_minisign");
        return Ok(normalize_minisign_plaintext_sig(t));
    }

    trace("resolve_sig branch=outer_base64");
    let mut text = base64_decode_to_string(wire.trim()).map_err(|e| {
        trace(&format!("resolve_sig outer_b64_err={e}"));
        format!(
            "signature: 外层 Base64 解码失败（{e}）。若 signature 已是明文 .sig，应以「untrusted comment:」开头且勿再 Base64 包裹"
        )
    })?;
    trace(&format!(
        "resolve_sig outer_ok decoded_utf8_len={} lines={}",
        text.len(),
        text.lines().count()
    ));

    for round in 0..3u32 {
        if is_minisign_sig_plaintext(&text) {
            trace(&format!(
                "resolve_sig hit_plaintext_after_inner round={round}"
            ));
            return Ok(normalize_minisign_plaintext_sig(&text));
        }
        let compact_inner: String = strip_crlf(&text)
            .chars()
            .filter(|c| matches!(c, 'A'..='Z' | 'a'..='z' | '0'..='9' | '+' | '/' | '='))
            .collect();
        trace(&format!(
            "resolve_sig inner_strip round={round} compact_len={}",
            compact_inner.len()
        ));
        if compact_inner.len() < 32 {
            trace("resolve_sig inner_compact too_short abort");
            break;
        }
        text = base64_decode_to_string(&compact_inner).map_err(|e| {
            trace(&format!("resolve_sig inner_b64_err round={round} err={e}"));
            format!("signature: 内层 Base64 解码失败（{e}）；可能是双重包裹损坏或非 Base64 内容")
        })?;
        trace(&format!(
            "resolve_sig inner_ok round={round} utf8_len={} lines={}",
            text.len(),
            text.lines().count()
        ));
    }

    trace("resolve_sig exhausted not_plaintext");
    Err(
        "signature: 无法得到 minisign .sig 明文（期望解 Base64 后以「untrusted comment:」开头；或为历史双重 Base64，请重发版本）"
            .into(),
    )
}

/// Tauri `tauri signer sign` 在 trusted comment 写入 `file:<被签名的本地文件名>`。
pub fn parse_minisign_trusted_file_field(release_signature_b64: &str) -> Result<Option<String>, String> {
    let sig_text = resolve_minisign_sig_text_from_wire_impl(release_signature_b64, |_| {})?;
    for line in sig_text.lines() {
        let line = line.trim();
        let Some(rest) = line.strip_prefix("trusted comment:") else {
            continue;
        };
        if let Some(pos) = rest.find("file:") {
            let tail = rest[pos + 5..].trim();
            let name = tail.lines().next().unwrap_or(tail).trim();
            if !name.is_empty() {
                return Ok(Some(name.to_string()));
            }
        }
    }
    Ok(None)
}

/// 从文件名类字符串中提取 `主.次.修订`（纯数字三段），用于对照发布版本。
fn extract_dotted_semvers(s: &str) -> Vec<String> {
    let mut out = Vec::new();
    for part in s.split(|c: char| !(c.is_ascii_digit() || c == '.')) {
        if part.is_empty() {
            continue;
        }
        let segs: Vec<&str> = part.split('.').collect();
        if segs.len() == 3
            && segs
                .iter()
                .all(|p| !p.is_empty() && p.chars().all(|c| c.is_ascii_digit()))
        {
            out.push(part.to_string());
        }
    }
    out
}

/// 校验签名里记载的「被签文件名」中的版本号与 Nexus 声明的 `new_version` 一致。
///
/// 防止 MinIO 路径登记为 0.8.75、实际对象仍是 `*_0.8.74_*-setup.exe` 且签名只对旧包有效时，客户端仍显示「已下载 0.8.75」。
pub fn assert_signed_artifact_version_matches_release(
    release_signature_b64: &str,
    expected_new_version: &str,
) -> Result<(), String> {
    let Some(signed_name) = parse_minisign_trusted_file_field(release_signature_b64)? else {
        return Ok(());
    };
    let exp = expected_new_version
        .trim()
        .trim_start_matches(['v', 'V'])
        .to_lowercase();
    let signed_lower = signed_name.to_lowercase();
    if signed_lower.contains(&exp) {
        return Ok(());
    }
    let found = extract_dotted_semvers(&signed_name);
    if found.is_empty() {
        return Ok(());
    }
    if found.iter().any(|v| v.to_lowercase() == exp) {
        return Ok(());
    }
    let vers = found.join(", ");
    Err(format!(
        "签名的 trusted comment 中 file 为「{signed_name}」，解析到版本号 {vers}，与本次热更新声明的「{expected_new_version}」不一致。\
         说明对象存储/Nexus 可能把旧安装包登记到了新版本号下（常见：只改了 VERSION 未重新 tauri build 就发布）。\
         请用正确版本的安装包重新签名并发布，或修正数据库中的版本记录。"
    ))
}

/// 与 tauri-plugin-updater 一致：`Signature::decode` 解析多行 `.sig`，**不要**对整段明文做 `base64::decode`。
#[allow(dead_code)] // 对外 API；`jachin-updater-helper` 二进制仅调用 `verify_minisign_payload_traced`
pub fn verify_minisign_payload(data: &[u8], release_signature_b64: &str, pubkey_conf_b64: &str) -> Result<(), String> {
    verify_minisign_payload_traced(data, release_signature_b64, pubkey_conf_b64, |_| {})
}

/// 与 [`verify_minisign_payload`] 相同，但逐步回调 `trace`（写入调试日志等）。
pub fn verify_minisign_payload_traced<F: FnMut(&str)>(
    data: &[u8],
    release_signature_b64: &str,
    pubkey_conf_b64: &str,
    mut trace: F,
) -> Result<(), String> {
    trace(&format!(
        "minisign_trace start payload_len={} payload_sha256={}",
        data.len(),
        hot_update_payload_sha256_hex(data)
    ));
    let public_key = decode_public_key_from_conf(pubkey_conf_b64).map_err(|e| {
        trace(&format!("minisign_trace pubkey_ERR {e}"));
        e
    })?;
    trace("minisign_trace pubkey_OK");
    let sig_text =
        resolve_minisign_sig_text_from_wire_impl(release_signature_b64, |m| trace(m)).map_err(|e| {
            trace(&format!("minisign_trace resolve_ERR {e}"));
            e
        })?;
    trace(&format!(
        "minisign_trace sig_plaintext lines={} char_len={}",
        sig_text.lines().count(),
        sig_text.len()
    ));
    let signature = Signature::decode(sig_text.trim_end()).map_err(|e| {
        trace(&format!("minisign_trace Signature_decode_ERR {e}"));
        format!("signature: minisign 解析失败: {e}")
    })?;
    trace("minisign_trace Signature_decode_OK");
    public_key
        .verify(data, &signature, true)
        .map_err(|e| {
            trace(&format!("minisign_trace crypto_verify_ERR {e}"));
            format!("minisign verify: {e}")
        })?;
    trace("minisign_trace crypto_verify_OK");
    Ok(())
}

/// 检查用户数据是否就绪：Tauri 应用数据目录 + `~/.jachin`（与 nexus_config 路径一致）。
pub fn user_data_ready_for_hot_update() -> Result<(), String> {
    let local = std::env::var("LOCALAPPDATA").map_err(|_| "缺少 LOCALAPPDATA".to_string())?;
    let app_dir = PathBuf::from(&local).join(TAURI_APP_IDENTIFIER_DIR);
    if !app_dir.is_dir() {
        return Err(format!(
            "应用数据目录不存在（请先正常启动过一次桌面端）: {}",
            app_dir.display()
        ));
    }

    let home = std::env::var("USERPROFILE").map_err(|_| "缺少 USERPROFILE".to_string())?;
    let jachin = PathBuf::from(home).join(".jachin");
    if !jachin.is_dir() {
        return Err("用户目录下缺少 .jachin（配对/配置目录），请确认已使用过桌面端".to_string());
    }

    Ok(())
}

#[cfg(test)]
mod pubkey_decode_tests {
    use base64::Engine;
    use super::decode_public_key_from_conf;

    /// `minisign-verify` 文档示例公钥行（56 字符，解码前缀 `Ed`）。
    const DOC_RW_LINE: &str = "RWQf6LRCGA9i53mlYecO4IzT51TGPpvWucNSCh1CBM0QTaLn73Y7GFO3";

    fn sample_pub_plaintext() -> String {
        format!(
            "untrusted comment: minisign public key: FFFFFFFFFFFFFFFFFFFFFFFF\n{}\n",
            DOC_RW_LINE
        )
    }

    #[test]
    fn decode_public_key_plaintext_pub_file_ok() {
        decode_public_key_from_conf(&sample_pub_plaintext()).expect("plaintext .pub");
    }

    #[test]
    fn decode_public_key_outer_single_blob_ok() {
        let plain = sample_pub_plaintext();
        let outer = base64::engine::general_purpose::STANDARD.encode(plain.as_bytes());
        decode_public_key_from_conf(&outer).expect("outer base64 of full .pub");
    }

    /// 与历史 `tauri.conf.json` 一致：首行为「第一行 ASCII 的 Base64」，次行为 RW 原文（整段非单一外层 Base64）。
    #[test]
    fn decode_public_key_mixed_first_b64_second_raw_ok() {
        let first = "untrusted comment: minisign public key: FFFFFFFFFFFFFFFFFFFFFFFF\n";
        let first_b64 = base64::engine::general_purpose::STANDARD.encode(first.as_bytes());
        let mixed = format!("{}\n{}", first_b64, DOC_RW_LINE);
        decode_public_key_from_conf(&mixed).expect("mixed two-line (offset~133 场景)");
    }
}
