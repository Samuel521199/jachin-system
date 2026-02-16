/// Dapr Client - 与 Dapr Sidecar 通信
use reqwest::Client;
use serde_json::Value;

const DAPR_HTTP_PORT: u16 = 3500;

pub struct DaprClient {
    client: Client,
    port: u16,
}

impl DaprClient {
    pub fn new() -> Self {
        Self {
            client: Client::new(),
            port: DAPR_HTTP_PORT,
        }
    }

    /// 通过 Dapr 调用服务
    pub async fn invoke(
        &self,
        app_id: &str,
        method: &str,
        data: Option<Value>,
        http_verb: &str,
    ) -> Result<Value, Box<dyn std::error::Error>> {
        let url = format!(
            "http://localhost:{}/v1.0/invoke/{}/method/{}",
            self.port,
            app_id,
            method.trim_start_matches('/')
        );

        let mut request = match http_verb.to_uppercase().as_str() {
            "GET" => self.client.get(&url),
            "POST" => self.client.post(&url),
            "PUT" => self.client.put(&url),
            "DELETE" => self.client.delete(&url),
            _ => return Err("Unsupported HTTP verb".into()),
        };

        if let Some(body) = data {
            request = request.json(&body);
        }

        let response = request.send().await?;

        if !response.status().is_success() {
            return Err(format!("HTTP {}: {}", response.status(), response.text().await?).into());
        }

        let json: Value = response.json().await?;
        Ok(json)
    }

    /// 健康检查
    #[allow(dead_code)]
    pub async fn health_check(&self) -> Result<bool, Box<dyn std::error::Error>> {
        let url = format!("http://localhost:{}/v1.0/healthz", self.port);
        let response = self.client.get(&url).send().await?;
        Ok(response.status().is_success())
    }
}

impl Default for DaprClient {
    fn default() -> Self {
        Self::new()
    }
}
