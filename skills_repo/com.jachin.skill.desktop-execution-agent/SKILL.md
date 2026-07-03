---
id: com.jachin.skill.desktop-execution-agent
name: enterprise_desktop_execution_agent
version: "1.0.0"
description: "企业桌面执行 Agent：跨 Windows、飞书、文件、浏览器和办公软件完成真实任务，并保留证据链。"
---

# Enterprise Desktop Execution Agent

This business skill is the product-level entry for OS assistant workflows.

It should route user requests to the installed OS mission router, Windows UIA
MCP, file operations, browser automation, Lark delivery tools, and evidence
recording workflow. It must not hard-code a single app workflow; it chooses
capabilities dynamically based on the task.

Core scenarios:

1. Open or switch Windows apps and verify the foreground window.
2. Find, read, copy, move, attach, or summarize local files with confirmation for dangerous operations.
3. Ask Codex to analyze a local project, copy the result, send it to Lark, and record evidence.
4. Operate browser or office software when the task requires a real UI.
5. Return a concise completion report with evidence paths, screenshots, OCR, or structured checks.

Safety:

- Destructive file actions require explicit confirmation unless the user has enabled a trusted policy.
- App actions must verify the active window before typing sensitive content.
- Every multi-app workflow should write evidence.
