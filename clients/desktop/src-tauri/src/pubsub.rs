/// Dapr Pub/Sub 订阅服务器
/// 
/// 实现 Dapr 的订阅机制，接收来自大脑的设备指令

use axum::{
    extract::State,
    http::StatusCode,
    response::{Json, Response},
    routing::{get, post},
    Router,
    body::Body,
};
use serde_json::{json, Value};
use std::sync::Arc;
use tauri::{AppHandle, Emitter};
use crate::device_registry::DeviceCommand;

/// 应用状态（包含 Tauri AppHandle）
pub struct AppState {
    pub app_handle: Arc<AppHandle>,
    pub device_id: String,
}

/// Dapr 订阅端点 - 返回订阅列表
/// Dapr sidecar 会调用这个端点来发现订阅
async fn dapr_subscribe(State(state): State<Arc<AppState>>) -> Json<Value> {
    let subscriptions = json!([
        {
            "pubsubname": "pubsub",
            "topic": format!("device/{}/command", state.device_id),
            "route": format!("/dapr/subscribe/device/{}/command", state.device_id)
        }
    ]);
    
    println!("[PubSub] Dapr requested subscriptions, returning: {:?}", subscriptions);
    Json(subscriptions)
}

/// 处理设备指令 - Dapr 推送消息到这里
async fn handle_command(
    State(state): State<Arc<AppState>>,
    Json(payload): Json<Value>,
) -> Result<Response<Body>, StatusCode> {
    // Dapr 发送的是 CloudEvent 格式
    let command_data = if let Some(data) = payload.get("data") {
        // CloudEvent 格式：data 可能是字符串或对象
        if let Some(data_str) = data.as_str() {
            serde_json::from_str::<DeviceCommand>(data_str)
                .map_err(|_| StatusCode::BAD_REQUEST)?
        } else {
            serde_json::from_value::<DeviceCommand>(data.clone())
                .map_err(|_| StatusCode::BAD_REQUEST)?
        }
    } else {
        // 直接数据格式
        serde_json::from_value::<DeviceCommand>(payload)
            .map_err(|_| StatusCode::BAD_REQUEST)?
    };

    // 验证设备ID
    if command_data.target_device_id != state.device_id {
        return Err(StatusCode::BAD_REQUEST);
    }

    // 通过 Tauri 事件发送到前端
    // Tauri 会自动序列化 serde_json::Value
    let command_json = serde_json::to_value(&command_data).unwrap_or_default();
    let _ = state.app_handle.emit("device-command", command_json);
    
    println!("[PubSub] Received command for {}: {:?}", state.device_id, command_data);

    // Dapr 期望成功时返回 HTTP 200，空响应体
    Ok(Response::builder()
        .status(StatusCode::OK)
        .body(Body::empty())
        .unwrap())
}

/// 创建 Pub/Sub 路由
pub fn create_pubsub_router(state: Arc<AppState>) -> Router {
    let device_id = state.device_id.clone();
    Router::new()
        .route(
            "/dapr/subscribe",
            get(dapr_subscribe),
        )
        .route(
            &format!("/dapr/subscribe/device/{}/command", device_id),
            post(handle_command),
        )
        .with_state(state)
}

/// 启动 Pub/Sub HTTP 服务器
pub async fn start_pubsub_server(
    app_handle: AppHandle,
    device_id: String,
    port: u16,
) -> Result<tokio::task::JoinHandle<()>, Box<dyn std::error::Error>> {
    let state = Arc::new(AppState {
        app_handle: Arc::new(app_handle),
        device_id: device_id.clone(),
    });
    
    let router = create_pubsub_router(state);
    
    let listener = tokio::net::TcpListener::bind(format!("127.0.0.1:{}", port)).await?;
    
    let handle = tokio::spawn(async move {
        println!("[PubSub] Starting HTTP server on port {} for device {}", port, device_id);
        
        axum::serve(listener, router)
            .await
            .unwrap_or_else(|e| {
                eprintln!("[PubSub] Server error: {}", e);
            });
    });
    
    Ok(handle)
}
