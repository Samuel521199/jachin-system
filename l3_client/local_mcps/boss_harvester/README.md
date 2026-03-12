# Boss 直聘收网 - L3 本地伴生 MCP

高强度本地 RPA（依赖本机 IP、浏览器 Cookie），**仅运行于 L3 客户端本机**，通过 Stdio 与 L3 主进程通信。

## 数据卷

PDF 保存到 L3 本地专属目录：

```
~/.jachin/client_volumes/{target_volume}/
```

与 L2 云端 `~/.jachin/volumes/` 隔离。

## 唤醒方式（Stdio 模式）

L3 宿主通过命令行作为子进程唤醒，**绝不启动 HTTP 服务器**：

```bash
# 方式 1：模块方式
python -m l3_client.local_mcps.boss_harvester.server

# 方式 2：直接执行（需在项目根目录）
python l3_client/local_mcps/boss_harvester/server.py
```

L3 主进程通过 stdin/stdout 与子进程通信（MCP 标准 Stdio 传输）。

## 前置条件

1. Chrome 以 `--remote-debugging-port=9222` 启动（如 `skills_repo/plugin/scripts/launch_chrome_debug.ps1`）
2. 在 Chrome 中登录 Boss 直聘，停留在「沟通」页

## 依赖

```bash
pip install -r l3_client/local_mcps/boss_harvester/requirements.txt
playwright install chromium  # 若使用 CDP 连接已有 Chrome 可省略
```

## 工具：atom_inbox_harvester

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| job_name | str | ✓ | 岗位名称，需与 Boss 下拉完全一致 |
| max_count | int | | 最大处理数量，默认 20 |
| target_volume | str | | 数据卷名，默认 global_resume_pool |
| filter_tab | str | | 消息 Tab，默认 全部 |
| request_if_no_resume | bool | | 无简历时点击求简历，默认 true |
| cdp_url | str | | Chrome 调试地址，默认 http://127.0.0.1:9222 |

返回结构含 `pdf_paths`（绝对路径），供 L3 Wasm 沙箱直接读取。
