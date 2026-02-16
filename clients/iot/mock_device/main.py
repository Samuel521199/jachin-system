"""
Mock IoT Device - 模拟树莓派节点

测试"能力发现"流程：
1. 模拟一个位于 'living_room' 的树莓派节点
2. 向大脑广播它的能力（camera.capture 和 light.control）
3. 使用 Dapr Pub/Sub 发送到 system/announce 主题
"""

import sys
import os
import logging
from pathlib import Path

# Windows 控制台编码修复
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# 添加项目根目录到 Python 路径，以便导入 backend.core.protocol
project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(project_root / "backend"))

try:
    # 尝试导入 dapr-ext-grpc (推荐)
    try:
        from dapr.clients import DaprClient
    except ImportError:
        # 回退到 dapr SDK
        from dapr import DaprClient
    
    from backend.core.protocol import DeviceAnnounce, DeviceCapability
except ImportError as e:
    print(f"[ERROR] Import error: {e}")
    print("Please install Dapr Python SDK:")
    print("  pip install dapr-ext-grpc")
    print("  or")
    print("  pip install dapr")
    sys.exit(1)

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def announce_capabilities():
    """
    向大脑广播设备能力
    
    构建 DeviceAnnounce 对象，包含：
    - device_id: "raspi-living-room-001"
    - location: "living_room"
    - capabilities: camera.capture, light.control
    
    注意：DaprClient.publish_event 是同步方法，不需要 async
    """
    # 构建设备能力列表
    capabilities = [
        DeviceCapability(
            name="camera.capture",
            description="使用摄像头拍摄照片",
            parameters={
                "type": "object",
                "properties": {
                    "resolution": {
                        "type": "string",
                        "enum": ["720p", "1080p"],
                        "description": "照片分辨率"
                    },
                    "format": {
                        "type": "string",
                        "enum": ["jpg", "png"],
                        "description": "照片格式"
                    }
                }
            }
        ),
        DeviceCapability(
            name="light.control",
            description="控制灯光开关",
            parameters={
                "type": "object",
                "properties": {
                    "state": {
                        "type": "string",
                        "enum": ["on", "off"],
                        "description": "灯光状态"
                    },
                    "brightness": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": 100,
                        "description": "亮度（0-100）"
                    }
                },
                "required": ["state"]
            }
        )
    ]
    
    # 构建设备广播包
    announce = DeviceAnnounce(
        device_id="raspi-living-room-001",
        device_type="iot-node",
        location="living_room",
        capabilities=capabilities,
        metadata={
            "hostname": "raspi-living-room",
            "model": "Raspberry Pi 4",
            "os": "Raspberry Pi OS",
            "version": "1.0.0"
        }
    )
    
    # 使用 Dapr Client 发布到 system/announce 主题
    try:
        # 将 DeviceAnnounce 转换为字典（Pydantic V2 兼容）
        try:
            # Pydantic V2 使用 model_dump()
            announce_data = announce.model_dump()
        except AttributeError:
            # Pydantic V1 使用 dict()
            announce_data = announce.dict()
        
        # 发布到 system/announce 主题
        print("[INFO] Broadcasting device capabilities to system/announce...")
        print(f"   Device ID: {announce.device_id}")
        print(f"   Location: {announce.location}")
        print(f"   Capabilities: {[cap.name for cap in announce.capabilities]}")
        
        logger.info(f"📢 Broadcasting device capabilities to system/announce...")
        logger.info(f"   Device ID: {announce.device_id}")
        logger.info(f"   Location: {announce.location}")
        logger.info(f"   Capabilities: {[cap.name for cap in announce.capabilities]}")
        
        # 使用 DaprClient 发布消息
        # 注意：dapr-ext-grpc 的 publish_event 需要 bytes 数据
        import json
        data_bytes = json.dumps(announce_data, ensure_ascii=False).encode('utf-8')
        
        with DaprClient() as dapr_client:
            # 发布事件
            dapr_client.publish_event(
                pubsub_name="pubsub",
                topic_name="system/announce",
                data=data_bytes,
                data_content_type="application/json"
            )
        
        print("[SUCCESS] Device announcement sent successfully!")
        print(f"   Topic: system/announce")
        print(f"   PubSub: pubsub")
        print(f"   Data size: {len(data_bytes)} bytes")
        
        logger.info("✅ Device announcement sent successfully!")
        logger.info(f"   Topic: system/announce")
        logger.info(f"   PubSub: pubsub")
        logger.info(f"   Data size: {len(data_bytes)} bytes")
        
        return True
    
    except Exception as e:
        print(f"[ERROR] Failed to send device announcement: {e}")
        logger.error(f"Failed to send device announcement: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """主函数"""
    # 使用 print 确保输出可见（Dapr 可能会干扰日志）
    print("=" * 60)
    print("Mock IoT Device - Capability Discovery Test")
    print("=" * 60)
    print("")
    print("Device: Raspberry Pi (living_room)")
    print("Capabilities: camera.capture, light.control")
    print("")
    
    logger.info("=" * 60)
    logger.info("Mock IoT Device - Capability Discovery Test")
    logger.info("=" * 60)
    logger.info("")
    logger.info("Device: Raspberry Pi (living_room)")
    logger.info("Capabilities: camera.capture, light.control")
    logger.info("")
    
    # 广播设备能力（同步调用）
    success = announce_capabilities()
    
    if success:
        print("")
        print("=" * 60)
        print("[SUCCESS] Capability discovery test completed successfully!")
        print("=" * 60)
        print("")
        print("Next steps:")
        print("1. Check backend logs to see if device was registered")
        print("2. Query DeviceRegistry to verify device registration")
        print("3. Test get_tools() to see if capabilities are available")
        
        logger.info("")
        logger.info("=" * 60)
        logger.info("✅ Capability discovery test completed successfully!")
        logger.info("=" * 60)
    else:
        print("")
        print("=" * 60)
        print("[ERROR] Capability discovery test failed!")
        print("=" * 60)
        
        logger.error("")
        logger.error("=" * 60)
        logger.error("❌ Capability discovery test failed!")
        logger.error("=" * 60)
        sys.exit(1)


if __name__ == "__main__":
    try:
        # 直接调用 main（不再是异步）
        main()
    except KeyboardInterrupt:
        print("\n[WARN] Interrupted by user")
        logger.info("\nInterrupted by user")
        sys.exit(0)
    except Exception as e:
        print(f"\n[ERROR] Unexpected error: {e}")
        logger.error(f"\nUnexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
