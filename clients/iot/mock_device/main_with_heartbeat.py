"""
Mock IoT Device with Heartbeat - 带心跳的模拟树莓派节点

功能：
1. 启动时广播设备能力到 system/announce
2. 每 10 秒发送一次心跳到 system/heartbeat
3. 持续运行，直到用户按 Ctrl+C 停止
"""

import sys
import os
import logging
import time
import signal
import threading
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler

# Windows 控制台编码修复
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# 添加项目根目录到 Python 路径
project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(project_root / "backend"))

try:
    # 尝试导入 dapr-ext-grpc (推荐)
    try:
        from dapr.clients import DaprClient
        DAPR_CLIENT_TYPE = "dapr.clients"
    except ImportError:
        try:
            # 尝试 dapr-ext-grpc 的另一种导入方式
            from dapr.ext.grpc import DaprClient
            DAPR_CLIENT_TYPE = "dapr.ext.grpc"
        except ImportError:
            try:
                # 回退到旧版 dapr SDK
                from dapr import DaprClient
                DAPR_CLIENT_TYPE = "dapr"
            except ImportError:
                raise ImportError("No Dapr SDK found. Please install: pip install dapr-ext-grpc")
    
    from backend.core.protocol import DeviceAnnounce, DeviceCapability
except ImportError as e:
    print(f"[ERROR] Import error: {e}")
    print("")
    print("Please install Dapr Python SDK:")
    print("  pip install dapr-ext-grpc")
    print("")
    print("Or run the installation script:")
    print("  .\\install_dependencies.bat")
    print("")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# 全局变量
running = True
device_id = "raspi-living-room-001"


class HealthHandler(BaseHTTPRequestHandler):
    """简单的健康检查处理器"""
    def do_GET(self):
        if self.path == '/dapr/subscribe':
            # Dapr 订阅端点 - 返回空数组（模拟设备不需要订阅）
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(b'[]')
        else:
            # 健康检查端点
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')
    
    def log_message(self, format, *args):
        # 禁用默认日志输出
        pass


def start_http_server(port=8001):
    """启动简单的 HTTP 服务器（Dapr 要求）"""
    server = HTTPServer(('0.0.0.0', port), HealthHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    logger.info(f"HTTP server started on port {port}")
    return server


def signal_handler(sig, frame):
    """处理 Ctrl+C 信号"""
    global running
    print("\n[INFO] Received interrupt signal, shutting down...")
    logger.info("Received interrupt signal, shutting down...")
    running = False


def announce_capabilities(dapr_client):
    """向大脑广播设备能力"""
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
    
    announce = DeviceAnnounce(
        device_id=device_id,
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
    
    try:
        try:
            announce_data = announce.model_dump()
        except AttributeError:
            announce_data = announce.dict()
        
        import json
        data_bytes = json.dumps(announce_data, ensure_ascii=False).encode('utf-8')
        
        # 根据不同的 Dapr SDK 版本使用不同的方法
        try:
            # dapr-ext-grpc 方式
            dapr_client.publish_event(
                pubsub_name="pubsub",
                topic_name="system/announce",
                data=data_bytes,
                data_content_type="application/json"
            )
        except TypeError:
            # 如果参数不对，尝试另一种方式
            dapr_client.publish_event(
                pubsub_name="pubsub",
                topic_name="system/announce",
                data=data_bytes
            )
        
        print(f"[SUCCESS] Device announcement sent: {device_id}")
        logger.info(f"✅ Device announcement sent: {device_id}")
        return True
    
    except Exception as e:
        print(f"[ERROR] Failed to send announcement: {e}")
        logger.error(f"Failed to send announcement: {e}")
        return False


def send_heartbeat(dapr_client):
    """发送心跳"""
    try:
        import json
        heartbeat_data = {
            "device_id": device_id,
            "timestamp": time.time()
        }
        data_bytes = json.dumps(heartbeat_data, ensure_ascii=False).encode('utf-8')
        
        # 根据不同的 Dapr SDK 版本使用不同的方法
        try:
            # dapr-ext-grpc 方式
            dapr_client.publish_event(
                pubsub_name="pubsub",
                topic_name="system/heartbeat",
                data=data_bytes,
                data_content_type="application/json"
            )
        except TypeError:
            # 如果参数不对，尝试另一种方式
            dapr_client.publish_event(
                pubsub_name="pubsub",
                topic_name="system/heartbeat",
                data=data_bytes
            )
        
        print(f"[HEARTBEAT] Sent heartbeat: {device_id}")
        logger.debug(f"💓 Sent heartbeat: {device_id}")
        return True
    
    except Exception as e:
        print(f"[ERROR] Failed to send heartbeat: {e}")
        logger.error(f"Failed to send heartbeat: {e}")
        return False


def main():
    """主函数"""
    global running
    
    # 注册信号处理器
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    print("=" * 60)
    print("Mock IoT Device - With Heartbeat")
    print("=" * 60)
    print("")
    print(f"Device ID: {device_id}")
    print("Location: living_room")
    print("Capabilities: camera.capture, light.control")
    print("")
    print("Press Ctrl+C to stop")
    print("")
    
    logger.info("=" * 60)
    logger.info("Mock IoT Device - With Heartbeat")
    logger.info("=" * 60)
    logger.info(f"Device ID: {device_id}")
    
    # 启动 HTTP 服务器（Dapr 要求应用监听端口）
    print("[INFO] Starting HTTP server on port 8001...")
    http_server = start_http_server(8001)
    time.sleep(0.5)  # 等待服务器启动
    
    # 创建 Dapr 客户端（保持连接）
    dapr_client = DaprClient()
    
    # 发送初始广播
    print("[INFO] Sending initial device announcement...")
    if not announce_capabilities(dapr_client):
        print("[ERROR] Failed to send initial announcement")
        sys.exit(1)
    
    print("[INFO] Device registered. Starting heartbeat loop...")
    print("[INFO] Heartbeat interval: 10 seconds")
    print("")
    
    # 心跳循环
    heartbeat_interval = 10  # 秒
    last_heartbeat = time.time()
    
    try:
        while running:
            current_time = time.time()
            
            # 每 10 秒发送一次心跳
            if current_time - last_heartbeat >= heartbeat_interval:
                send_heartbeat(dapr_client)
                last_heartbeat = current_time
            
            # 短暂休眠，避免 CPU 占用过高
            time.sleep(1)
    
    except KeyboardInterrupt:
        print("\n[INFO] Interrupted by user")
        logger.info("Interrupted by user")
    except Exception as e:
        print(f"\n[ERROR] Unexpected error: {e}")
        logger.error(f"Unexpected error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        print("\n[INFO] Shutting down...")
        logger.info("Shutting down...")
        try:
            dapr_client.close()
        except:
            pass
        try:
            http_server.shutdown()
        except:
            pass


if __name__ == "__main__":
    main()
