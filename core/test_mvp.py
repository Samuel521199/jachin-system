"""
MVP 测试脚本 - 快速验证核心功能

运行方式：
    python backend/test_mvp.py
"""

import asyncio
import sys
from pathlib import Path

# Add backend to path
backend_dir = Path(__file__).parent
sys.path.insert(0, str(backend_dir))

async def test_llm_provider():
    """测试 LLM Provider 初始化"""
    print("=" * 50)
    print("测试 1: LLM Provider 初始化")
    print("=" * 50)
    
    try:
        from core.brain.llm.factory import LLMProviderFactory
        from core.config import settings

        print(f"Provider 类型: {settings.LLM_PROVIDER}")
        print(f"模型: {settings.LLM_MODEL}")

        # 检查 API Key
        api_key = (
            settings.QWEN_API_KEY
            or settings.DASHSCOPE_API_KEY
            or settings.QWEN_AI_API_KEY
        )
        
        if not api_key:
            print("❌ API Key 未设置")
            print("   请设置环境变量: QWEN_API_KEY, DASHSCOPE_API_KEY, 或 QWEN_AI_API_KEY")
            return False
        
        print(f"✅ API Key 已设置 (长度: {len(api_key)})")
        
        # 创建 Provider
        provider = LLMProviderFactory.create_provider(
            provider_type=settings.LLM_PROVIDER,
            model=settings.LLM_MODEL
        )
        
        print(f"✅ Provider 创建成功: {type(provider).__name__}")
        
        # 获取模型信息
        model_info = provider.get_model_info()
        print(f"✅ 模型信息: {model_info}")
        
        return True
        
    except Exception as e:
        print(f"❌ 错误: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_chat():
    """测试聊天功能"""
    print("\n" + "=" * 50)
    print("测试 2: 聊天功能")
    print("=" * 50)
    
    try:
        from core.brain.llm.factory import LLMProviderFactory
        from core.config import settings

        provider = LLMProviderFactory.create_provider(
            provider_type=settings.LLM_PROVIDER,
            model=settings.LLM_MODEL
        )
        
        messages = [
            {"role": "user", "content": "你好，请用一句话介绍你自己"}
        ]
        
        print("发送消息:", messages[0]["content"])
        print("等待响应...")
        
        response = await provider.chat(messages)
        
        print(f"✅ 收到响应: {response[:100]}...")
        return True
        
    except Exception as e:
        print(f"❌ 错误: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_health_check():
    """测试健康检查"""
    print("\n" + "=" * 50)
    print("测试 3: 健康检查")
    print("=" * 50)
    
    try:
        from core.brain.llm.factory import LLMProviderFactory
        from core.config import settings

        provider = LLMProviderFactory.create_provider(
            provider_type=settings.LLM_PROVIDER,
            model=settings.LLM_MODEL
        )
        
        health = await provider.health_check()
        
        if health:
            print("✅ 健康检查通过")
        else:
            print("⚠️  健康检查失败")
        
        return health
        
    except Exception as e:
        print(f"❌ 错误: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_config():
    """测试配置加载"""
    print("\n" + "=" * 50)
    print("测试 4: 配置加载")
    print("=" * 50)
    
    try:
        from core.config import settings

        print(f"✅ LLM Provider: {settings.LLM_PROVIDER}")
        print(f"✅ LLM Model: {settings.LLM_MODEL}")
        print(f"✅ Server Port: {settings.SERVER_PORT}")
        print(f"✅ Debug Mode: {settings.DEBUG}")
        print(f"✅ Qdrant URL: {settings.QDRANT_URL}")
        print(f"✅ Redis URL: {settings.REDIS_URL}")
        
        return True
        
    except Exception as e:
        print(f"❌ 错误: {e}")
        import traceback
        traceback.print_exc()
        return False


async def main():
    """运行所有测试"""
    print("\n" + "🚀 MVP 功能测试" + "\n")
    
    results = []
    
    # 测试配置
    results.append(await test_config())
    
    # 测试 Provider
    results.append(await test_llm_provider())
    
    # 测试健康检查
    results.append(await test_health_check())
    
    # 测试聊天（可选，需要 API Key）
    try:
        results.append(await test_chat())
    except Exception as e:
        print(f"\n⚠️  聊天测试跳过: {e}")
    
    # 总结
    print("\n" + "=" * 50)
    print("测试总结")
    print("=" * 50)
    
    passed = sum(results)
    total = len(results)
    
    print(f"通过: {passed}/{total}")
    
    if passed == total:
        print("✅ 所有测试通过！MVP 功能正常")
        return 0
    else:
        print("⚠️  部分测试失败，请检查配置")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
