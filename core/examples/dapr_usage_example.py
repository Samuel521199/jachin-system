"""
Dapr 使用示例

演示如何使用 Dapr 进行服务调用、状态管理和发布订阅。
"""

import asyncio
from core.dapr import (
    service_invocation,
    state_store,
    pubsub,
    dapr_client,
)


async def example_1_service_invocation():
    """示例 1: 服务调用"""
    print("=" * 50)
    print("示例 1: 服务调用（Service Invocation）")
    print("=" * 50)
    
    try:
        # 调用其他服务（通过 app-id）
        # 注意：这需要目标服务已经启动并注册了对应的 app-id
        result = await service_invocation.invoke(
            app_id="backend",  # 目标服务的 app-id
            method_name="/api/health",  # API 路径
            http_verb="GET",
        )
        print(f"服务调用结果: {result}")
    
    except Exception as e:
        print(f"服务调用失败（这是正常的，如果目标服务未启动）: {e}")


async def example_2_state_store():
    """示例 2: 状态管理"""
    print("\n" + "=" * 50)
    print("示例 2: 状态管理（State Store）")
    print("=" * 50)
    
    try:
        # 保存状态
        user_data = {
            "name": "Alice",
            "age": 30,
            "preferences": {
                "theme": "dark",
                "language": "zh-CN",
            },
        }
        
        success = await state_store.save("user:123", user_data)
        print(f"保存状态: {'成功' if success else '失败'}")
        
        # 获取状态
        retrieved = await state_store.get("user:123", {})
        print(f"获取状态: {retrieved}")
        
        # 批量操作
        states = [
            {"key": "session:abc", "value": {"user_id": "123", "expires_at": "2026-02-02"}},
            {"key": "session:def", "value": {"user_id": "456", "expires_at": "2026-02-03"}},
        ]
        await state_store.save_bulk(states)
        print("批量保存状态: 成功")
        
        # 批量获取
        bulk_data = await state_store.get_bulk(["session:abc", "session:def"])
        print(f"批量获取状态: {bulk_data}")
        
        # 删除状态
        await state_store.delete("session:abc")
        print("删除状态: 成功")
    
    except Exception as e:
        print(f"状态操作失败: {e}")


async def example_3_pubsub():
    """示例 3: 发布订阅"""
    print("\n" + "=" * 50)
    print("示例 3: 发布订阅（Pub/Sub）")
    print("=" * 50)
    
    try:
        # 发布消息到主题
        device_event = {
            "device_id": "raspberry-pi-001",
            "event_type": "motion_detected",
            "timestamp": "2026-02-01T10:00:00Z",
            "data": {
                "sensor": "PIR",
                "value": 1,
            },
        }
        
        success = await pubsub.publish(
            topic="device-events",
            data=device_event,
        )
        print(f"发布消息到 'device-events': {'成功' if success else '失败'}")
        
        # 发布用户事件
        user_event = {
            "user_id": "user123",
            "action": "login",
            "timestamp": "2026-02-01T10:00:00Z",
        }
        
        await pubsub.publish(
            topic="user-events",
            data=user_event,
        )
        print("发布消息到 'user-events': 成功")
    
    except Exception as e:
        print(f"发布消息失败: {e}")


async def example_4_health_check():
    """示例 4: 健康检查"""
    print("\n" + "=" * 50)
    print("示例 4: Dapr 健康检查")
    print("=" * 50)
    
    if dapr_client:
        is_healthy = dapr_client.health_check()
        print(f"Dapr Sidecar 状态: {'✓ 健康' if is_healthy else '✗ 异常'}")
        
        if is_healthy:
            print(f"  HTTP 端口: {dapr_client.dapr_http_port}")
            print(f"  gRPC 端口: {dapr_client.dapr_grpc_port}")
    else:
        print("Dapr 客户端不可用（请检查 dapr 包是否已安装）")


async def example_5_integration():
    """示例 5: 综合使用场景"""
    print("\n" + "=" * 50)
    print("示例 5: 综合使用场景")
    print("=" * 50)
    
    try:
        # 场景：用户登录流程
        
        # 1. 保存用户会话状态
        session_data = {
            "user_id": "user123",
            "login_time": "2026-02-01T10:00:00Z",
            "ip_address": "192.168.1.100",
        }
        await state_store.save("session:abc123", session_data)
        print("1. 保存会话状态: 成功")
        
        # 2. 发布登录事件
        await pubsub.publish(
            topic="user-events",
            data={
                "event": "user_login",
                "user_id": "user123",
                "session_id": "abc123",
            },
        )
        print("2. 发布登录事件: 成功")
        
        # 3. 调用其他服务（例如通知服务）
        try:
            await service_invocation.invoke(
                app_id="notification-service",
                method_name="/api/send-welcome",
                data={"user_id": "user123"},
            )
            print("3. 调用通知服务: 成功")
        except Exception as e:
            print(f"3. 调用通知服务: 失败（{e}）")
        
        # 4. 获取会话状态
        session = await state_store.get("session:abc123", {})
        print(f"4. 获取会话状态: {session}")
    
    except Exception as e:
        print(f"综合场景执行失败: {e}")


async def main():
    """主函数"""
    print("\n" + "=" * 50)
    print("Jachin-System Dapr 使用示例")
    print("=" * 50 + "\n")
    
    # 检查 Dapr 客户端
    if not dapr_client:
        print("⚠️  警告: Dapr 客户端不可用")
        print("   请确保:")
        print("   1. 已安装 dapr 包: pip install dapr")
        print("   2. Dapr sidecar 已启动")
        print("   3. 环境变量 DAPR_HTTP_PORT 和 DAPR_GRPC_PORT 已设置\n")
        return
    
    # 运行示例
    await example_4_health_check()
    await example_2_state_store()
    await example_3_pubsub()
    await example_1_service_invocation()
    await example_5_integration()
    
    print("\n" + "=" * 50)
    print("所有示例运行完成")
    print("=" * 50)


if __name__ == "__main__":
    asyncio.run(main())
