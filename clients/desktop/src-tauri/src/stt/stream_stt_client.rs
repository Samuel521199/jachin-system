#![cfg(feature = "ambient")]

use serde::Deserialize;
use serde_json::json;
use std::io::{Read, Write};
use std::net::TcpStream;
use std::sync::{Mutex, OnceLock};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};
use tungstenite::stream::MaybeTlsStream;
use tungstenite::{connect, Message, WebSocket};

const TCP_START: u8 = 0x01;
const TCP_CHUNK: u8 = 0x02;
const TCP_FINALIZE: u8 = 0x03;
const TCP_ACK: u8 = 0x68;
const TCP_PARTIAL: u8 = 0x66;
const TCP_FINAL: u8 = 0x67;
const TCP_ERROR: u8 = 0x7F;

#[derive(Debug, Clone, Deserialize)]
struct StreamServerMessage {
    #[serde(default)]
    r#type: String,
    #[serde(default)]
    text: String,
    #[serde(default)]
    message: String,
}

enum StreamTransport {
    RawTcp(RawTcpClient),
    WebSocket(WebSocket<MaybeTlsStream<TcpStream>>),
}

struct RawTcpClient {
    stream: TcpStream,
    recv_buf: Vec<u8>,
}

pub struct SttStreamClient {
    transport: StreamTransport,
    latest_text: Option<String>,
    session_id: String,
    partial_count: usize,
    final_count: usize,
    audio_frames_sent: usize,
}

struct PrewarmedSttStream {
    base_url: String,
    session_id: String,
    created_at: Instant,
    client: SttStreamClient,
}

static PREWARMED_STT_STREAM: OnceLock<Mutex<Option<PrewarmedSttStream>>> = OnceLock::new();

#[derive(Debug, Clone)]
pub struct SttStreamFinalResult {
    pub text: Option<String>,
    pub finalized: bool,
    pub partial_count: usize,
    pub final_count: usize,
    pub audio_frames_sent: usize,
}

fn prewarmed_slot() -> &'static Mutex<Option<PrewarmedSttStream>> {
    PREWARMED_STT_STREAM.get_or_init(|| Mutex::new(None))
}

fn prewarm_session_id() -> String {
    let millis = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis())
        .unwrap_or(0);
    format!("ptt-prewarm-{millis}")
}

pub fn prewarm(base_url: &str) -> Result<String, String> {
    let mut guard = prewarmed_slot().lock().map_err(|e| e.to_string())?;
    if let Some(existing) = guard.as_ref() {
        if existing.base_url == base_url && existing.created_at.elapsed() < Duration::from_secs(45) {
            crate::l3_spawn::write_voice_chat_trace(
                "ptt",
                "stream_prewarm_reuse",
                &existing.session_id,
                &format!("age_ms={}", existing.created_at.elapsed().as_millis()),
            );
            return Ok(existing.session_id.clone());
        }
    }
    let session_id = prewarm_session_id();
    let client = SttStreamClient::connect(base_url, &session_id)?;
    crate::l3_spawn::write_voice_chat_trace(
        "ptt",
        "stream_prewarm_ready",
        &session_id,
        base_url,
    );
    *guard = Some(PrewarmedSttStream {
        base_url: base_url.to_string(),
        session_id: session_id.clone(),
        created_at: Instant::now(),
        client,
    });
    Ok(session_id)
}

pub fn take_prewarmed(base_url: &str, max_age: Duration) -> Option<SttStreamClient> {
    let mut guard = prewarmed_slot().lock().ok()?;
    let existing = guard.take()?;
    let age = existing.created_at.elapsed();
    if existing.base_url == base_url && age <= max_age {
        crate::l3_spawn::write_voice_chat_trace(
            "ptt",
            "stream_prewarm_take",
            &existing.session_id,
            &format!("age_ms={}", age.as_millis()),
        );
        return Some(existing.client);
    }
    crate::l3_spawn::write_voice_chat_trace(
        "ptt",
        "stream_prewarm_stale",
        &existing.session_id,
        &format!("age_ms={} base_match={}", age.as_millis(), existing.base_url == base_url),
    );
    None
}

impl SttStreamClient {
    pub fn session_id(&self) -> &str {
        &self.session_id
    }

    pub fn connect(base_url: &str, session_id: &str) -> Result<Self, String> {
        crate::l3_spawn::write_voice_chat_trace(
            "ptt",
            "stream_connect_start",
            session_id,
            base_url,
        );
        if let Ok(raw) = RawTcpClient::connect(base_url, session_id) {
            crate::l3_spawn::write_voice_chat_trace(
                "ptt",
                "stream_connect_ok",
                session_id,
                "transport=raw_tcp",
            );
            crate::l3_spawn::write_voice_chat_trace(
                "ptt",
                "stream_start_sent",
                session_id,
                "sample_rate=16000 format=pcm16le",
            );
            return Ok(Self {
                transport: StreamTransport::RawTcp(raw),
                latest_text: None,
                session_id: session_id.to_string(),
                partial_count: 0,
                final_count: 0,
                audio_frames_sent: 0,
            });
        }
        match Self::connect_ws(base_url, session_id) {
            Ok(client) => Ok(client),
            Err(e) => {
                crate::l3_spawn::write_voice_chat_trace(
                    "ptt",
                    "stream_connect_failed",
                    session_id,
                    &e,
                );
                Err(e)
            }
        }
    }

    fn connect_ws(base_url: &str, session_id: &str) -> Result<Self, String> {
        let base = base_url.trim();
        if base.is_empty() {
            return Err("empty jvs base url".to_string());
        }
        let ws_base = if let Some(rest) = base.strip_prefix("http://") {
            format!("ws://{}", rest)
        } else if let Some(rest) = base.strip_prefix("https://") {
            format!("wss://{}", rest)
        } else if base.starts_with("ws://") || base.starts_with("wss://") {
            base.to_string()
        } else {
            format!("ws://{}", base)
        };
        let sid = session_id
            .chars()
            .map(|c| {
                if c.is_ascii_alphanumeric() || c == '-' || c == '_' {
                    c.to_string()
                } else {
                    "_".to_string()
                }
            })
            .collect::<String>();
        let ws_url = format!(
            "{}/v1/stt/stream?session_id={}",
            ws_base.trim_end_matches('/'),
            sid
        );
        let (mut ws, _) =
            connect(&ws_url).map_err(|e| format!("stt stream connect failed: {e}"))?;
        if let MaybeTlsStream::Plain(stream) = ws.get_mut() {
            let _ = stream.set_nonblocking(true);
        }
        let start = json!({
            "type": "start",
            "sample_rate": 16000,
            "format": "pcm16le",
            "channels": 1,
        });
        ws.write(Message::Text(start.to_string()))
            .map_err(|e| format!("stt stream start failed: {e}"))?;
        crate::l3_spawn::write_voice_chat_trace(
            "ptt",
            "stream_connect_ok",
            session_id,
            "transport=websocket",
        );
        crate::l3_spawn::write_voice_chat_trace(
            "ptt",
            "stream_start_sent",
            session_id,
            "sample_rate=16000 format=pcm16le",
        );
        Ok(Self {
            transport: StreamTransport::WebSocket(ws),
            latest_text: None,
            session_id: session_id.to_string(),
            partial_count: 0,
            final_count: 0,
            audio_frames_sent: 0,
        })
    }

    pub fn push_chunk(&mut self, chunk_f32: &[f32]) -> Result<(), String> {
        if chunk_f32.is_empty() {
            return Ok(());
        }
        let mut pcm = Vec::<u8>::with_capacity(chunk_f32.len() * 2);
        for &s in chunk_f32 {
            let clamped = s.clamp(-1.0, 1.0);
            let v = if clamped < 0.0 {
                (clamped * 32768.0) as i16
            } else {
                (clamped * 32767.0) as i16
            };
            pcm.extend_from_slice(&v.to_le_bytes());
        }
        match &mut self.transport {
            StreamTransport::RawTcp(raw) => raw.send_frame(TCP_CHUNK, &pcm)?,
            StreamTransport::WebSocket(ws) => ws
                .write(Message::Binary(pcm))
                .map_err(|e| format!("stt stream write chunk failed: {e}"))?,
        }
        self.audio_frames_sent += 1;
        if self.audio_frames_sent == 1 {
            crate::l3_spawn::write_voice_chat_trace(
                "ptt",
                "stream_first_audio_sent",
                &self.session_id,
                &format!("bytes={}", chunk_f32.len() * 2),
            );
        }
        self.drain_nonblocking();
        Ok(())
    }

    pub fn finalize(&mut self, timeout: Duration) -> SttStreamFinalResult {
        crate::l3_spawn::write_voice_chat_trace(
            "ptt",
            "stream_finalize_sent",
            &self.session_id,
            &format!(
                "timeout_ms={} partial_count={} audio_frames_sent={}",
                timeout.as_millis(),
                self.partial_count,
                self.audio_frames_sent
            ),
        );
        match &mut self.transport {
            StreamTransport::RawTcp(raw) => {
                let _ = raw.send_frame(TCP_FINALIZE, &[]);
            }
            StreamTransport::WebSocket(ws) => {
                let _ = ws.write(Message::Text(json!({"type":"finalize"}).to_string()));
            }
        }
        let deadline = Instant::now() + timeout;
        while Instant::now() < deadline {
            let mut got_final = false;
            for (kind, text) in self.read_available_messages() {
                if !text.trim().is_empty() {
                    self.latest_text = Some(text.clone());
                }
                self.record_stream_event(&kind);
                if kind == "final" {
                    got_final = true;
                }
            }
            if got_final {
                crate::l3_spawn::write_voice_chat_trace(
                    "ptt",
                    "stream_final_received",
                    &self.session_id,
                    &format!(
                        "text_len={} partial_count={} final_count={}",
                        self.latest_text.as_ref().map(|s| s.len()).unwrap_or(0),
                        self.partial_count,
                        self.final_count
                    ),
                );
                return SttStreamFinalResult {
                    text: self.latest_text.clone(),
                    finalized: true,
                    partial_count: self.partial_count,
                    final_count: self.final_count,
                    audio_frames_sent: self.audio_frames_sent,
                };
            }
            std::thread::sleep(Duration::from_millis(15));
        }
        crate::l3_spawn::write_voice_chat_trace(
            "ptt",
            "stream_final_timeout",
            &self.session_id,
            &format!(
                "text_len={} partial_count={} final_count={} audio_frames_sent={}",
                self.latest_text.as_ref().map(|s| s.len()).unwrap_or(0),
                self.partial_count,
                self.final_count,
                self.audio_frames_sent
            ),
        );
        SttStreamFinalResult {
            text: self.latest_text.clone(),
            finalized: false,
            partial_count: self.partial_count,
            final_count: self.final_count,
            audio_frames_sent: self.audio_frames_sent,
        }
    }

    fn drain_nonblocking(&mut self) {
        for (kind, text) in self.read_available_messages() {
            if !text.trim().is_empty() {
                self.latest_text = Some(text);
            }
            self.record_stream_event(&kind);
        }
    }

    pub fn has_text(&self) -> bool {
        self.latest_text
            .as_ref()
            .map(|s| !s.trim().is_empty())
            .unwrap_or(false)
    }

    fn record_stream_event(&mut self, kind: &str) {
        if kind == "partial" {
            self.partial_count += 1;
            crate::l3_spawn::write_voice_chat_trace(
                "ptt",
                "stream_partial_received",
                &self.session_id,
                &format!(
                    "partial_count={} text_len={}",
                    self.partial_count,
                    self.latest_text.as_ref().map(|s| s.len()).unwrap_or(0)
                ),
            );
        } else if kind == "final" {
            self.final_count += 1;
        }
    }

    fn read_available_messages(&mut self) -> Vec<(String, String)> {
        match &mut self.transport {
            StreamTransport::RawTcp(raw) => raw.read_messages_nonblocking(),
            StreamTransport::WebSocket(ws) => {
                let mut out = Vec::new();
                loop {
                    match ws.read() {
                        Ok(msg) => {
                            if let Some(parsed) = Self::parse_ws_server_msg(msg) {
                                out.push(parsed);
                            }
                        }
                        Err(tungstenite::Error::Io(ref io_err))
                            if io_err.kind() == std::io::ErrorKind::WouldBlock =>
                        {
                            break;
                        }
                        Err(_) => break,
                    }
                }
                out
            }
        }
    }

    fn parse_ws_server_msg(msg: Message) -> Option<(String, String)> {
        match msg {
            Message::Text(t) => {
                let parsed = serde_json::from_str::<StreamServerMessage>(&t).ok()?;
                if parsed.r#type == "error" && !parsed.message.is_empty() {
                    return Some((parsed.r#type, parsed.message));
                }
                Some((parsed.r#type, parsed.text))
            }
            _ => None,
        }
    }
}

impl RawTcpClient {
    fn connect(base_url: &str, session_id: &str) -> Result<Self, String> {
        let (host, http_port) =
            parse_host_port(base_url).ok_or_else(|| "invalid jvs base url".to_string())?;
        let tcp_port = http_port.saturating_add(1);
        let addr = format!("{host}:{tcp_port}");
        let mut stream = TcpStream::connect_timeout(
            &addr.parse().map_err(|e| format!("invalid tcp addr: {e}"))?,
            Duration::from_millis(300),
        )
        .map_err(|e| format!("stt raw tcp connect failed: {e}"))?;
        let _ = stream.set_nodelay(true);
        let _ = stream.set_nonblocking(true);
        let start = json!({
            "sample_rate": 16000,
            "channels": 1,
            "format": "pcm16le",
            "session_id": sanitize_session_id(session_id),
        })
        .to_string();
        send_frame(&mut stream, TCP_START, start.as_bytes())?;
        Ok(Self {
            stream,
            recv_buf: Vec::with_capacity(8192),
        })
    }

    fn send_frame(&mut self, msg_type: u8, payload: &[u8]) -> Result<(), String> {
        send_frame(&mut self.stream, msg_type, payload)
    }

    fn read_messages_nonblocking(&mut self) -> Vec<(String, String)> {
        let mut tmp = [0u8; 4096];
        loop {
            match self.stream.read(&mut tmp) {
                Ok(0) => break,
                Ok(n) => self.recv_buf.extend_from_slice(&tmp[..n]),
                Err(ref e) if e.kind() == std::io::ErrorKind::WouldBlock => break,
                Err(_) => break,
            }
        }
        let mut out = Vec::new();
        loop {
            if self.recv_buf.len() < 5 {
                break;
            }
            let msg_type = self.recv_buf[0];
            let len = u32::from_le_bytes([
                self.recv_buf[1],
                self.recv_buf[2],
                self.recv_buf[3],
                self.recv_buf[4],
            ]) as usize;
            if self.recv_buf.len() < 5 + len {
                break;
            }
            let payload = self.recv_buf[5..5 + len].to_vec();
            self.recv_buf.drain(0..5 + len);
            if let Some((kind, text)) = parse_tcp_payload(msg_type, &payload) {
                out.push((kind, text));
            }
        }
        out
    }
}

fn send_frame(stream: &mut TcpStream, msg_type: u8, payload: &[u8]) -> Result<(), String> {
    let mut frame = Vec::with_capacity(5 + payload.len());
    frame.push(msg_type);
    frame.extend_from_slice(&(payload.len() as u32).to_le_bytes());
    frame.extend_from_slice(payload);
    stream
        .write_all(&frame)
        .map_err(|e| format!("stt raw tcp send failed: {e}"))
}

fn parse_tcp_payload(msg_type: u8, payload: &[u8]) -> Option<(String, String)> {
    if !matches!(msg_type, TCP_ACK | TCP_PARTIAL | TCP_FINAL | TCP_ERROR) {
        return None;
    }
    let text = std::str::from_utf8(payload).ok()?;
    let parsed = serde_json::from_str::<StreamServerMessage>(text).ok()?;
    if parsed.r#type == "error" && !parsed.message.is_empty() {
        return Some((parsed.r#type, parsed.message));
    }
    Some((parsed.r#type, parsed.text))
}

fn parse_host_port(base_url: &str) -> Option<(String, u16)> {
    let mut s = base_url.trim();
    if s.is_empty() {
        return None;
    }
    if let Some(rest) = s.strip_prefix("http://") {
        s = rest;
    } else if let Some(rest) = s.strip_prefix("https://") {
        s = rest;
    }
    let host_port = s.split('/').next()?.trim();
    if host_port.is_empty() {
        return None;
    }
    if let Some((host, port_str)) = host_port.rsplit_once(':') {
        let port = port_str.parse::<u16>().ok()?;
        let h = host.trim().trim_matches('[').trim_matches(']').to_string();
        if h.is_empty() {
            return None;
        }
        return Some((h, port));
    }
    Some((host_port.to_string(), 18982))
}

fn sanitize_session_id(session_id: &str) -> String {
    session_id
        .chars()
        .map(|c| {
            if c.is_ascii_alphanumeric() || c == '-' || c == '_' {
                c
            } else {
                '_'
            }
        })
        .collect::<String>()
}
