"""
heavy_process 示例插件 - 使用 PluginServerBase
用于 P0-3 SandboxEngine 集成测试

依赖：HeavyProcessRunner 启动时已设置 PYTHONPATH 含项目根
"""
from core.plugin.plugin_server_base import PluginServerBase


class HeavyDemoPlugin(PluginServerBase):
    def execute(self, capability: str, payload: dict) -> dict:
        if capability == "ping":
            return {"status_code": 200, "payload": {"message": "pong"}}
        if capability == "echo":
            return {"status_code": 200, "payload": {"echo": payload.get("text", "")}}
        return {"status_code": 404, "error_message": f"Unknown capability: {capability}"}


if __name__ == "__main__":
    HeavyDemoPlugin.run()
