# Memory Module - 记忆管理模块

## 概述

Memory 模块负责管理 Jachin-System 的长期记忆，使用 Qdrant 向量数据库存储和检索记忆。

## 核心组件

### VectorStore

基于 Qdrant 的向量存储单例类，提供以下功能：

- **upsert**: 添加或更新记忆（向量）
- **search**: 搜索相似记忆
- **delete**: 删除指定记忆
- **get_by_id**: 根据 ID 获取记忆

## 使用示例

### 1. 基本使用

```python
from core.memory import vector_store
from core.llm import LLMProviderFactory

# 获取 LLM Provider（用于生成 embedding）
llm = LLMProviderFactory.create_provider("qwen")

# 生成文本的 embedding
text = "用户喜欢在晚上工作"
embedding = await llm.embed([text])[0]

# 存储记忆
memory_id = await vector_store.upsert(
    text=text,
    embedding=embedding,
    metadata={
        "user_id": "user123",
        "category": "preference",
        "timestamp": "2026-02-01T10:00:00Z",
    }
)

# 搜索相似记忆
query_text = "用户的工作时间"
query_embedding = await llm.embed([query_text])[0]

results = await vector_store.search(
    query_embedding=query_embedding,
    limit=5,
    score_threshold=0.7,
    filter_conditions={"user_id": "user123"},
)

for result in results:
    print(f"相似度: {result['score']:.2f}")
    print(f"文本: {result['text']}")
    print(f"元数据: {result['metadata']}")
```

### 2. 使用自定义集合

```python
# 创建用户专属的记忆集合
collection_name = f"user_{user_id}_memories"

# 存储到自定义集合
memory_id = await vector_store.upsert(
    text=text,
    embedding=embedding,
    metadata={"user_id": user_id},
    collection_name=collection_name,
)

# 从自定义集合搜索
results = await vector_store.search(
    query_embedding=query_embedding,
    collection_name=collection_name,
    limit=10,
)
```

### 3. 删除记忆

```python
# 删除指定记忆
success = await vector_store.delete(
    point_id=memory_id,
    collection_name="user_memories",
)

# 根据 ID 获取记忆
memory = await vector_store.get_by_id(
    point_id=memory_id,
    collection_name="user_memories",
)
```

### 4. 健康检查和集合信息

```python
# 检查 Qdrant 服务状态
is_healthy = vector_store.health_check()
print(f"Qdrant 服务状态: {'健康' if is_healthy else '异常'}")

# 获取集合信息
info = vector_store.get_collection_info("jachin_memories")
print(f"集合名称: {info['name']}")
print(f"记忆数量: {info['points_count']}")
print(f"向量维度: {info['config']['vector_size']}")
```

## 配置

向量存储的配置通过 `config/settings.py` 管理：

```python
# Qdrant 配置
QDRANT_URL = "http://localhost:6333"
QDRANT_GRPC_URL = "http://localhost:6334"  # 可选，用于 gRPC 连接
```

## 单例模式

`VectorStore` 使用单例模式，确保整个应用只有一个 Qdrant 客户端实例：

```python
from core.memory import VectorStore, vector_store

# 方式 1: 使用全局实例（推荐）
results = await vector_store.search(...)

# 方式 2: 创建新实例（实际上会返回同一个实例）
store = VectorStore()
results = await store.search(...)
```

## 最佳实践

1. **元数据设计**: 在 `metadata` 中包含足够的上下文信息（user_id, timestamp, category 等），便于后续过滤和检索

2. **集合管理**: 
   - 使用默认集合存储通用记忆
   - 为不同用户或场景创建专属集合

3. **相似度阈值**: 根据应用场景设置合适的 `score_threshold`，过滤低质量结果

4. **批量操作**: 对于大量记忆，考虑批量 upsert 以提高性能

5. **错误处理**: 始终处理可能的异常，确保服务的健壮性

## 与 LLM 集成

记忆模块通常与 LLM Provider 配合使用：

```python
from core.memory import vector_store
from core.llm import LLMProviderFactory

async def remember_and_recall(user_id: str, query: str):
    """记忆和回忆流程"""
    
    # 1. 获取 LLM Provider
    llm = LLMProviderFactory.create_provider("qwen")
    
    # 2. 生成查询向量
    query_embedding = await llm.embed([query])[0]
    
    # 3. 搜索相关记忆
    memories = await vector_store.search(
        query_embedding=query_embedding,
        filter_conditions={"user_id": user_id},
        limit=5,
        score_threshold=0.7,
    )
    
    # 4. 构建上下文
    context = "\n".join([m["text"] for m in memories])
    
    # 5. 使用上下文进行对话
    messages = [
        {"role": "system", "content": f"相关记忆：\n{context}"},
        {"role": "user", "content": query},
    ]
    response = await llm.chat(messages)
    
    return response
```

## 注意事项

1. **向量维度**: 确保 embedding 的维度与集合配置一致（默认 1536，对应 Qwen embedding）

2. **异步操作**: 所有方法都是异步的，需要使用 `await` 调用

3. **线程安全**: VectorStore 是线程安全的单例，可以在多线程环境中使用

4. **连接管理**: Qdrant 客户端会自动管理连接，无需手动关闭
