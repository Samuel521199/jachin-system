"""
LLM Provider 使用示例

演示如何使用 MAL 层进行模型调用。
"""

import asyncio
from core.config import settings
from core.brain.llm import (
    LLMProviderFactory,
    ModelRouter,
    TaskComplexity,
    QwenAdapter,
    LocalAdapter,
)


async def example_1_factory_usage():
    """示例 1: 使用 Factory 创建 Provider"""
    print("=" * 50)
    print("示例 1: 使用 Factory 创建 Provider")
    print("=" * 50)

    # 创建 Qwen Provider
    try:
        qwen = LLMProviderFactory.create_provider(
            "qwen",
            api_key=settings.QWEN_API_KEY,
            model="qwen-turbo"
        )
        
        messages = [{"role": "user", "content": "你好，请介绍一下你自己"}]
        response = await qwen.chat(messages)
        print(f"Qwen 响应: {response}\n")
    except Exception as e:
        print(f"Qwen Provider 创建失败: {e}\n")


async def example_2_router_usage():
    """示例 2: 使用 Router 智能路由"""
    print("=" * 50)
    print("示例 2: 使用 Router 智能路由")
    print("=" * 50)
    
    # 创建 Router（自动从环境变量读取配置）
    router = LLMProviderFactory.create_router()
    
    # 简单任务（应该路由到本地模型）
    simple_input = "开灯"
    print(f"用户输入（简单）: {simple_input}")
    try:
        response = await router.process_request(simple_input)
        print(f"响应: {response}\n")
    except Exception as e:
        print(f"处理失败: {e}\n")
    
    # 复杂任务（应该路由到 Qwen）
    complex_input = "帮我写一个 Python 函数来计算斐波那契数列"
    print(f"用户输入（复杂）: {complex_input}")
    try:
        response = await router.process_request(complex_input)
        print(f"响应: {response[:100]}...\n")  # 只显示前100个字符
    except Exception as e:
        print(f"处理失败: {e}\n")


async def example_3_stream_chat():
    """示例 3: 流式响应"""
    print("=" * 50)
    print("示例 3: 流式响应")
    print("=" * 50)
    
    try:
        qwen = LLMProviderFactory.create_provider("qwen")
        messages = [{"role": "user", "content": "请用一句话介绍 Python"}]
        
        print("流式响应: ", end="", flush=True)
        async for chunk in qwen.stream_chat(messages):
            print(chunk, end="", flush=True)
        print("\n")
    except Exception as e:
        print(f"流式响应失败: {e}\n")


async def example_4_manual_routing():
    """示例 4: 手动路由"""
    print("=" * 50)
    print("示例 4: 手动路由")
    print("=" * 50)
    
    # 创建 Adapters
    qwen = None
    local = None
    
    try:
        qwen = LLMProviderFactory.create_provider("qwen")
    except Exception as e:
        print(f"Qwen Adapter 创建失败: {e}")
    
    try:
        local = LLMProviderFactory.create_provider("local")
    except Exception as e:
        print(f"Local Adapter 创建失败: {e}")
    
    # 创建 Router
    router = ModelRouter(
        qwen_adapter=qwen,
        local_adapter=local,
        prefer_local_for_simple=True
    )
    
    # 分析任务复杂度
    user_input = "查询当前温度"
    complexity = router.analyze_complexity(user_input)
    print(f"用户输入: {user_input}")
    print(f"分析复杂度: {complexity.value}")
    
    # 路由到合适的 Provider
    try:
        provider = router.route(complexity)
        print(f"选择的 Provider: {provider.get_model_info()['provider']}")
        
        messages = [{"role": "user", "content": user_input}]
        response = await provider.chat(messages)
        print(f"响应: {response}\n")
    except Exception as e:
        print(f"路由失败: {e}\n")


async def example_5_embedding():
    """示例 5: 文本嵌入"""
    print("=" * 50)
    print("示例 5: 文本嵌入")
    print("=" * 50)
    
    try:
        qwen = LLMProviderFactory.create_provider("qwen")
        texts = ["这是第一段文本", "这是第二段文本"]
        
        embeddings = await qwen.embed(texts)
        print(f"文本数量: {len(texts)}")
        print(f"嵌入向量数量: {len(embeddings)}")
        print(f"向量维度: {len(embeddings[0]) if embeddings else 0}\n")
    except Exception as e:
        print(f"嵌入失败: {e}\n")


async def example_6_health_check():
    """示例 6: 健康检查"""
    print("=" * 50)
    print("示例 6: 健康检查")
    print("=" * 50)
    
    providers = []
    
    # 尝试创建 Qwen Provider
    try:
        qwen = LLMProviderFactory.create_provider("qwen")
        providers.append(("Qwen", qwen))
    except Exception as e:
        print(f"Qwen Provider 不可用: {e}")
    
    # 尝试创建 Local Provider
    try:
        local = LLMProviderFactory.create_provider("local")
        providers.append(("Local", local))
    except Exception as e:
        print(f"Local Provider 不可用: {e}")
    
    # 检查每个 Provider 的健康状态
    for name, provider in providers:
        is_healthy = await provider.health_check()
        status = "健康" if is_healthy else "不健康"
        print(f"{name} Provider: {status}")
        print(f"  模型信息: {provider.get_model_info()}\n")


async def main():
    """主函数"""
    print("\n" + "=" * 50)
    print("Jachin-System LLM Provider 使用示例")
    print("=" * 50 + "\n")
    
    # 运行所有示例
    await example_1_factory_usage()
    await example_2_router_usage()
    await example_3_stream_chat()
    await example_4_manual_routing()
    await example_5_embedding()
    await example_6_health_check()
    
    print("=" * 50)
    print("所有示例运行完成")
    print("=" * 50)


if __name__ == "__main__":
    # 设置环境变量（示例，实际应从 .env 文件读取）
    # os.environ["QWEN_API_KEY"] = "your-api-key-here"
    
    asyncio.run(main())
