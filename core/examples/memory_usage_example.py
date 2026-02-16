"""
Memory Module 使用示例

演示如何使用 VectorStore 进行记忆的存储和检索。
"""

import asyncio
import os
from core.memory import vector_store
from core.brain.llm import LLMProviderFactory


async def example_1_basic_upsert_and_search():
    """示例 1: 基本的存储和搜索"""
    print("=" * 50)
    print("示例 1: 基本的存储和搜索")
    print("=" * 50)
    
    try:
        # 获取 LLM Provider（用于生成 embedding）
        llm = LLMProviderFactory.create_provider("qwen")
        
        # 准备记忆文本
        memories = [
            "用户喜欢在晚上工作，通常在 20:00 到 24:00",
            "用户最喜欢的编程语言是 Python",
            "用户经常使用 VSCode 作为代码编辑器",
            "用户喜欢喝咖啡，特别是拿铁",
        ]
        
        # 存储记忆
        print("\n存储记忆...")
        memory_ids = []
        for text in memories:
            # 生成 embedding
            embedding = await llm.embed([text])[0]
            
            # 存储到向量数据库
            memory_id = await vector_store.upsert(
                text=text,
                embedding=embedding,
                metadata={
                    "user_id": "user123",
                    "category": "preference",
                },
            )
            memory_ids.append(memory_id)
            print(f"  ✓ 存储: {text[:30]}... (ID: {memory_id[:8]})")
        
        # 搜索相似记忆
        print("\n搜索相似记忆...")
        query_text = "用户的工作习惯"
        query_embedding = await llm.embed([query_text])[0]
        
        results = await vector_store.search(
            query_embedding=query_embedding,
            limit=3,
            score_threshold=0.5,
            filter_conditions={"user_id": "user123"},
        )
        
        print(f"\n查询: {query_text}")
        print(f"找到 {len(results)} 条相关记忆:\n")
        for i, result in enumerate(results, 1):
            print(f"{i}. 相似度: {result['score']:.3f}")
            print(f"   文本: {result['text']}")
            print(f"   ID: {result['id'][:8]}...")
            print()
    
    except Exception as e:
        print(f"错误: {e}")


async def example_2_custom_collection():
    """示例 2: 使用自定义集合"""
    print("=" * 50)
    print("示例 2: 使用自定义集合")
    print("=" * 50)
    
    try:
        llm = LLMProviderFactory.create_provider("qwen")
        
        # 创建用户专属集合
        user_id = "user456"
        collection_name = f"user_{user_id}_memories"
        
        # 存储到自定义集合
        text = "用户计划在下周完成项目文档"
        embedding = await llm.embed([text])[0]
        
        memory_id = await vector_store.upsert(
            text=text,
            embedding=embedding,
            metadata={"user_id": user_id, "type": "plan"},
            collection_name=collection_name,
        )
        
        print(f"存储到集合: {collection_name}")
        print(f"记忆 ID: {memory_id}")
        
        # 从自定义集合搜索
        query_text = "用户的计划"
        query_embedding = await llm.embed([query_text])[0]
        
        results = await vector_store.search(
            query_embedding=query_embedding,
            collection_name=collection_name,
            limit=5,
        )
        
        print(f"\n从集合 {collection_name} 找到 {len(results)} 条结果")
        for result in results:
            print(f"  - {result['text']} (相似度: {result['score']:.3f})")
    
    except Exception as e:
        print(f"错误: {e}")


async def example_3_get_and_delete():
    """示例 3: 获取和删除记忆"""
    print("=" * 50)
    print("示例 3: 获取和删除记忆")
    print("=" * 50)
    
    try:
        llm = LLMProviderFactory.create_provider("qwen")
        
        # 存储一条记忆
        text = "这是一条测试记忆"
        embedding = await llm.embed([text])[0]
        
        memory_id = await vector_store.upsert(
            text=text,
            embedding=embedding,
            metadata={"test": True},
        )
        
        print(f"存储记忆 ID: {memory_id}")
        
        # 根据 ID 获取记忆
        memory = await vector_store.get_by_id(memory_id)
        if memory:
            print(f"\n获取记忆:")
            print(f"  ID: {memory['id']}")
            print(f"  文本: {memory['text']}")
            print(f"  元数据: {memory['metadata']}")
        
        # 删除记忆
        success = await vector_store.delete(memory_id)
        print(f"\n删除记忆: {'成功' if success else '失败'}")
        
        # 验证删除
        memory_after = await vector_store.get_by_id(memory_id)
        print(f"删除后获取: {'不存在' if memory_after is None else '仍存在'}")
    
    except Exception as e:
        print(f"错误: {e}")


async def example_4_collection_info():
    """示例 4: 获取集合信息"""
    print("=" * 50)
    print("示例 4: 获取集合信息")
    print("=" * 50)
    
    try:
        # 健康检查
        is_healthy = vector_store.health_check()
        print(f"Qdrant 服务状态: {'✓ 健康' if is_healthy else '✗ 异常'}")
        
        # 获取默认集合信息
        info = vector_store.get_collection_info()
        print(f"\n默认集合信息:")
        print(f"  名称: {info['name']}")
        print(f"  记忆数量: {info['points_count']}")
        print(f"  向量数量: {info['vectors_count']}")
        print(f"  向量维度: {info['config']['vector_size']}")
        print(f"  距离度量: {info['config']['distance']}")
    
    except Exception as e:
        print(f"错误: {e}")


async def example_5_memory_recall():
    """示例 5: 记忆回忆流程（与 LLM 集成）"""
    print("=" * 50)
    print("示例 5: 记忆回忆流程")
    print("=" * 50)
    
    try:
        llm = LLMProviderFactory.create_provider("qwen")
        user_id = "user789"
        
        # 1. 存储一些记忆
        memories = [
            "用户喜欢 Python 编程",
            "用户使用 FastAPI 开发后端",
            "用户喜欢喝咖啡",
        ]
        
        print("存储记忆...")
        for text in memories:
            embedding = await llm.embed([text])[0]
            await vector_store.upsert(
                text=text,
                embedding=embedding,
                metadata={"user_id": user_id},
            )
        
        # 2. 用户查询
        user_query = "用户使用什么技术栈？"
        print(f"\n用户查询: {user_query}")
        
        # 3. 生成查询向量并搜索相关记忆
        query_embedding = await llm.embed([user_query])[0]
        relevant_memories = await vector_store.search(
            query_embedding=query_embedding,
            filter_conditions={"user_id": user_id},
            limit=3,
            score_threshold=0.6,
        )
        
        # 4. 构建上下文
        context = "\n".join([f"- {m['text']}" for m in relevant_memories])
        print(f"\n相关记忆:\n{context}")
        
        # 5. 使用上下文进行对话
        messages = [
            {
                "role": "system",
                "content": f"以下是用户的相关记忆：\n{context}\n\n请基于这些记忆回答用户的问题。",
            },
            {"role": "user", "content": user_query},
        ]
        
        response = await llm.chat(messages)
        print(f"\nAI 回答:\n{response}")
    
    except Exception as e:
        print(f"错误: {e}")


async def main():
    """主函数"""
    print("\n" + "=" * 50)
    print("Jachin-System Memory Module 使用示例")
    print("=" * 50 + "\n")
    
    # 检查 Qdrant 连接
    if not vector_store.health_check():
        print("⚠️  警告: Qdrant 服务不可用，请确保服务已启动")
        print("   启动命令: docker-compose up -d qdrant\n")
        return
    
    # 运行示例
    await example_1_basic_upsert_and_search()
    print("\n")
    
    await example_2_custom_collection()
    print("\n")
    
    await example_3_get_and_delete()
    print("\n")
    
    await example_4_collection_info()
    print("\n")
    
    await example_5_memory_recall()
    
    print("\n" + "=" * 50)
    print("所有示例运行完成")
    print("=" * 50)


if __name__ == "__main__":
    # 设置环境变量（示例，实际应从 .env 文件读取）
    # os.environ["QWEN_API_KEY"] = "your-api-key-here"
    
    asyncio.run(main())
