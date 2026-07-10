# DashScope 视觉冒烟测试（qwen3.5-plus vs qwen-vl-max）

用于**脱离 L3 Agent**，单独验证：同一张本地 PNG 经 OpenAI 风格 `image_url`（data URL）传给 LiteLLM → DashScope 时，不同模型是否真正「看到」图片。

## 前置条件

- Python 环境已安装仓库依赖（含 `litellm`）。
- 已设置 `DASHSCOPE_API_KEY`（可在仓库根 `.env` 或 `dist_jachin_desktop/.env` 中配置；脚本会尝试 `load_dotenv` 这两处）。

## 默认测试图

仓库内固定文件：

`.playwright-mcp/page-2026-04-10T01-59-28-779Z.png`

## 运行

在仓库根目录执行：

```bash
python scripts/test_dashscope_vision_smoke.py
```

默认会**依次**请求：

1. `dashscope/qwen3.5-plus`
2. `dashscope/qwen-vl-max`

只测一个模型：

```bash
python scripts/test_dashscope_vision_smoke.py --model dashscope/qwen-vl-max
```

换图：

```bash
python scripts/test_dashscope_vision_smoke.py --image path/to/screenshot.png
```

## 如何解读结果

- **qwen-vl-max**：应能概括截图中的界面、按钮、文案等；若仍称「未收到图片」，则优先查 API Key、网络、LiteLLM/DashScope 路由。
- **qwen3.5-plus**：多为**文本模型**；可能对图片无视觉理解或表现不稳定。若 L3 在含图场景误用该模型作为「主答」模型，容易出现「否认有图」类回复；含图对话应使用 VL 系列（与 `core/llm_provider.py` 中多模态路由一致）。

## 与 L3 的关系

本脚本**不**经过 `agent_core` / WorkOrder；仅验证 **LiteLLM + DashScope + data URL 图片** 的最小链路。L3 侧若仍异常，请对比本脚本输出与 `JACHIN_DASHSCOPE_VL_KEEPS_TOOLS`、fallback 链等配置。
