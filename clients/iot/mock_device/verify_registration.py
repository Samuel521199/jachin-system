"""
验证设备注册 - 检查设备是否已成功注册到 DeviceRegistry
"""

import sys
import requests
from pathlib import Path

# 添加项目根目录到 Python 路径
project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(project_root / "backend"))

try:
    from backend.core.registry import DeviceRegistry
    from backend.core.protocol import DeviceAnnounce
except ImportError as e:
    print(f"[ERROR] Import error: {e}")
    print("Please ensure backend is accessible")
    sys.exit(1)


def verify_via_api():
    """通过 REST API 验证设备注册"""
    print("=" * 60)
    print("Verifying Device Registration via REST API")
    print("=" * 60)
    print()
    
    base_url = "http://localhost:8000"
    device_id = "raspi-living-room-001"
    
    try:
        # 1. 查询所有设备
        print(f"[1] Querying all devices...")
        response = requests.get(f"{base_url}/api/v2/devices", timeout=5)
        if response.status_code == 200:
            devices = response.json()
            print(f"    Found {len(devices.get('devices', []))} devices")
            for device in devices.get('devices', []):
                print(f"    - {device.get('device_id')} ({device.get('location')})")
        else:
            print(f"    [WARN] API endpoint not available (status: {response.status_code})")
            print(f"    This is OK if backend is not running")
        
        print()
        
        # 2. 查询特定设备
        print(f"[2] Querying device: {device_id}...")
        response = requests.get(f"{base_url}/api/v2/devices/{device_id}", timeout=5)
        if response.status_code == 200:
            device = response.json()
            print(f"    [SUCCESS] Device found!")
            print(f"    Device ID: {device.get('device_id')}")
            print(f"    Location: {device.get('location')}")
            print(f"    Capabilities: {len(device.get('capabilities', []))}")
            for cap in device.get('capabilities', []):
                print(f"      - {cap.get('name')}: {cap.get('description')}")
        elif response.status_code == 404:
            print(f"    [WARN] Device not found in registry")
            print(f"    Possible reasons:")
            print(f"    1. Backend is not running")
            print(f"    2. Backend is not listening to system/announce topic")
            print(f"    3. DeviceRegistry is not initialized")
        else:
            print(f"    [ERROR] API error (status: {response.status_code})")
            print(f"    Response: {response.text}")
        
        print()
        
        # 3. 查询设备能力
        print(f"[3] Querying device capabilities...")
        response = requests.get(f"{base_url}/api/v2/devices/{device_id}/capabilities", timeout=5)
        if response.status_code == 200:
            capabilities = response.json()
            print(f"    [SUCCESS] Found {len(capabilities.get('capabilities', []))} capabilities")
            for cap in capabilities.get('capabilities', []):
                print(f"      - {cap.get('name')}: {cap.get('description')}")
        else:
            print(f"    [WARN] Could not query capabilities (status: {response.status_code})")
        
    except requests.exceptions.ConnectionError:
        print("[WARN] Cannot connect to backend API (http://localhost:8000)")
        print("       Backend may not be running or not listening on port 8000")
        print()
        print("To start backend:")
        print("  dapr run --app-id jachin-brain --app-port 8000 --dapr-http-port 3500 \\")
        print("    --resources-path ./dapr/components --config ./dapr/config/config.yaml \\")
        print("    -- python backend/main.py")
    except Exception as e:
        print(f"[ERROR] Unexpected error: {e}")
        import traceback
        traceback.print_exc()


def verify_via_registry():
    """直接通过 DeviceRegistry 验证（需要后端运行）"""
    print("=" * 60)
    print("Verifying Device Registration via DeviceRegistry")
    print("=" * 60)
    print()
    
    try:
        import asyncio
        
        async def check():
            registry = DeviceRegistry()
            device_id = "raspi-living-room-001"
            
            # 查询设备
            print(f"[1] Querying device: {device_id}...")
            device = await registry.get_device(device_id)
            
            if device:
                print(f"    [SUCCESS] Device found!")
                print(f"    Device ID: {device.device_id}")
                print(f"    Location: {device.location}")
                print(f"    Device Type: {device.device_type}")
                print(f"    Capabilities: {len(device.capabilities)}")
                for cap in device.capabilities:
                    print(f"      - {cap.name}: {cap.description}")
                
                print()
                
                # 检查在线状态
                print(f"[2] Checking device online status...")
                is_online = registry.is_device_online(device_id)
                print(f"    Online: {is_online}")
                
                print()
                
                # 获取工具列表
                print(f"[3] Getting tools from registry...")
                tools = await registry.get_tools()
                print(f"    Found {len(tools)} tools")
                for tool in tools:
                    if device_id in tool.get('function', {}).get('name', ''):
                        print(f"      - {tool.get('function', {}).get('name')}")
            else:
                print(f"    [WARN] Device not found in registry")
                print(f"    The device announcement may not have been received by backend")
            
            print()
            
            # 列出所有设备
            print(f"[4] Listing all devices...")
            all_devices = await registry.get_all_devices()
            print(f"    Total devices: {len(all_devices)}")
            for dev in all_devices:
                online_status = "online" if registry.is_device_online(dev.device_id) else "offline"
                print(f"      - {dev.device_id} ({dev.location}) - {online_status}")
        
        asyncio.run(check())
    
    except Exception as e:
        print(f"[ERROR] Failed to verify via registry: {e}")
        print("         This requires backend to be running and Dapr to be accessible")
        import traceback
        traceback.print_exc()


def main():
    """主函数"""
    print()
    print("=" * 60)
    print("Device Registration Verification")
    print("=" * 60)
    print()
    
    # 方式 1: 通过 REST API 验证
    verify_via_api()
    
    print()
    print("-" * 60)
    print()
    
    # 方式 2: 直接通过 DeviceRegistry 验证
    verify_via_registry()
    
    print()
    print("=" * 60)
    print("Verification Complete")
    print("=" * 60)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[WARN] Interrupted by user")
        sys.exit(0)
    except Exception as e:
        print(f"\n[ERROR] Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
