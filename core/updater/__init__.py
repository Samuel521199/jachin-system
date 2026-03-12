"""
Updater Agent - Layer 1 ↔ Layer 2 端云握手
战役四：端云握手

职责：
- 轮询或长连接 Layer 1 获取部署指令
- 使用临时 Token 下载 .jmp/.jsp 包
- 解压、热加载到 PluginManager
- 通知 Layer 3：「主人，我已经学会新技能了」
"""

from core.updater.agent import UpdaterAgent

__all__ = ["UpdaterAgent"]
