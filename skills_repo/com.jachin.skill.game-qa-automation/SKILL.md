---
id: com.jachin.skill.game-qa-automation
name: game_qa_automation_platform
version: "1.0.0"
description: "游戏 QA / 自动化测试平台：面向游戏和复杂 UI 的视觉测试、冒烟、回放和规则执行。"
---

# Game QA Automation Platform

This is the business-level entry for game testing and complex UI automation.

It should coordinate the GameQA local runtime, Playwright/browser automation,
screen observation, replay scripts, smoke tests, and rule-based validation. The
user should see one product skill instead of multiple test scripts or MCP atoms.

Core scenarios:

1. Run smoke tests for game or complex UI modules.
2. Start a visual automation session and collect evidence.
3. Replay a recorded path and compare expected behavior.
4. Explain failure points with logs, screenshots, and structured evidence.
5. Keep low-level MCP and test scripts behind this single business skill.
