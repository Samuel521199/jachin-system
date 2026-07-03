/// Device Registry - 设备注册和能力发现
///
/// 按照 Jachin-System v2.0 架构实现：
/// - 设备启动时广播能力到 system/announce
/// - 定期发送心跳到 system/heartbeat
/// - 监听设备指令 device/{device_id}/command
/// - 发送执行结果到 device/{device_id}/response
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::time::{SystemTime, UNIX_EPOCH};
use tokio::time::{interval, Duration};

const HEARTBEAT_INTERVAL_SECS: u64 = 10; // 心跳间隔：10秒

/// 设备能力定义
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DeviceCapability {
    pub name: String,
    pub description: String,
    pub parameters: Value,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub return_type: Option<String>,
}

/// 设备广播包
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DeviceAnnounce {
    pub device_id: String,
    pub device_type: String,
    pub location: String,
    pub capabilities: Vec<DeviceCapability>,
    pub metadata: Value,
    #[serde(default = "current_timestamp")]
    pub timestamp: f64,
}

/// 设备心跳包
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DeviceHeartbeat {
    pub device_id: String,
    #[serde(default = "current_timestamp")]
    pub timestamp: f64,
}

/// 设备指令包
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DeviceCommand {
    pub command_id: String,
    pub target_device_id: String,
    pub capability_name: String,
    pub params: Value,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub timeout: Option<u32>,
    #[serde(default = "current_timestamp")]
    pub timestamp: f64,
}

/// 设备响应包
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DeviceResponse {
    pub command_id: String,
    pub device_id: String,
    pub status: String, // "success" | "error" | "timeout"
    #[serde(skip_serializing_if = "Option::is_none")]
    pub result: Option<Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
    #[serde(default = "current_timestamp")]
    pub timestamp: f64,
}

pub fn current_timestamp() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_secs_f64()
}

/// 设备注册管理器
pub struct DeviceRegistry {
    device_id: String,
    dapr_http_port: u16,
}

impl DeviceRegistry {
    /// 创建新的设备注册管理器
    pub fn new(device_id: String) -> Self {
        // 桌面客户端使用不同的 Dapr 端口（如果运行自己的 sidecar）
        // 如果没有运行 sidecar，则直接连接到后端的 Dapr sidecar (3500)
        let dapr_port = std::env::var("DAPR_HTTP_PORT")
            .ok()
            .and_then(|p| p.parse().ok())
            .unwrap_or(3500);

        Self {
            device_id,
            dapr_http_port: dapr_port,
        }
    }

    /// 获取设备ID
    #[allow(dead_code)]
    pub fn device_id(&self) -> &str {
        &self.device_id
    }

    /// 定义桌面客户端的能力
    fn get_desktop_capabilities() -> Vec<DeviceCapability> {
        vec![
            DeviceCapability {
                name: "notification.show".to_string(),
                description: "在桌面显示系统通知".to_string(),
                parameters: json!({
                    "type": "object",
                    "properties": {
                        "title": {
                            "type": "string",
                            "description": "通知标题"
                        },
                        "message": {
                            "type": "string",
                            "description": "通知内容"
                        },
                        "duration": {
                            "type": "integer",
                            "description": "显示时长（秒）",
                            "default": 5
                        }
                    },
                    "required": ["title", "message"]
                }),
                return_type: Some("void".to_string()),
            },
            DeviceCapability {
                name: "window.show".to_string(),
                description: "显示指定窗口".to_string(),
                parameters: json!({
                    "type": "object",
                    "properties": {
                        "window_name": {
                            "type": "string",
                            "enum": ["sprite", "chat"],
                            "description": "窗口名称"
                        }
                    },
                    "required": ["window_name"]
                }),
                return_type: Some("void".to_string()),
            },
            DeviceCapability {
                name: "window.hide".to_string(),
                description: "隐藏指定窗口".to_string(),
                parameters: json!({
                    "type": "object",
                    "properties": {
                        "window_name": {
                            "type": "string",
                            "enum": ["sprite", "chat"],
                            "description": "窗口名称"
                        }
                    },
                    "required": ["window_name"]
                }),
                return_type: Some("void".to_string()),
            },
            DeviceCapability {
                name: "sprite.set_state".to_string(),
                description: "设置精灵动画状态".to_string(),
                parameters: json!({
                    "type": "object",
                    "properties": {
                        "state": {
                            "type": "string",
                            "enum": ["idle", "listening", "thinking", "speaking"],
                            "description": "动画状态"
                        }
                    },
                    "required": ["state"]
                }),
                return_type: Some("void".to_string()),
            },
        ]
    }

    /// 发送设备广播
    pub async fn announce(&self) -> Result<(), Box<dyn std::error::Error>> {
        let announce = DeviceAnnounce {
            device_id: self.device_id.clone(),
            device_type: "desktop-client".to_string(),
            location: "desktop".to_string(),
            capabilities: Self::get_desktop_capabilities(),
            metadata: json!({
                "platform": std::env::consts::OS,
                "arch": std::env::consts::ARCH,
                "version": env!("CARGO_PKG_VERSION"),
            }),
            timestamp: current_timestamp(),
        };

        let data = serde_json::to_vec(&announce)?;

        // 通过 Dapr Pub/Sub 发布到 system/announce 主题
        self.publish_event("system/announce", data).await?;

        println!("[DeviceRegistry] Device announced: {}", self.device_id);
        Ok(())
    }

    /// 发送心跳
    #[allow(dead_code)]
    pub async fn send_heartbeat(&self) -> Result<(), Box<dyn std::error::Error>> {
        let heartbeat = DeviceHeartbeat {
            device_id: self.device_id.clone(),
            timestamp: current_timestamp(),
        };

        let data = serde_json::to_vec(&heartbeat)?;
        self.publish_event("system/heartbeat", data).await?;

        Ok(())
    }

    /// 发送设备响应
    pub async fn send_response(
        &self,
        response: DeviceResponse,
    ) -> Result<(), Box<dyn std::error::Error>> {
        let topic = format!("device/{}/response", self.device_id);
        let data = serde_json::to_vec(&response)?;
        self.publish_event(&topic, data).await?;
        Ok(())
    }

    /// 通过 Dapr Pub/Sub 发布事件
    async fn publish_event(
        &self,
        topic: &str,
        data: Vec<u8>,
    ) -> Result<(), Box<dyn std::error::Error>> {
        let url = format!(
            "http://localhost:{}/v1.0/publish/pubsub/{}",
            self.dapr_http_port, topic
        );

        let client = reqwest::Client::new();
        let response = client
            .post(&url)
            .header("Content-Type", "application/json")
            .body(data)
            .send()
            .await?;

        if !response.status().is_success() {
            return Err(format!("Failed to publish event: HTTP {}", response.status()).into());
        }

        Ok(())
    }

    /// 启动心跳循环（后台任务）
    pub fn start_heartbeat_loop(&self) -> tokio::task::JoinHandle<()> {
        let device_id = self.device_id.clone();
        let dapr_http_port = self.dapr_http_port;

        tokio::spawn(async move {
            let mut interval = interval(Duration::from_secs(HEARTBEAT_INTERVAL_SECS));

            loop {
                interval.tick().await;

                let heartbeat = DeviceHeartbeat {
                    device_id: device_id.clone(),
                    timestamp: current_timestamp(),
                };

                if let Ok(data) = serde_json::to_vec(&heartbeat) {
                    let url = format!(
                        "http://localhost:{}/v1.0/publish/pubsub/system/heartbeat",
                        dapr_http_port
                    );

                    let client = reqwest::Client::new();
                    let _ = client
                        .post(&url)
                        .header("Content-Type", "application/json")
                        .body(data)
                        .send()
                        .await;
                }
            }
        })
    }
}
