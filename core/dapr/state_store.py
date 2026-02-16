"""
StateStore - 状态存储封装

通过 Dapr 进行状态管理，支持 Redis、PostgreSQL 等后端。
"""

import json
import logging
import asyncio
from typing import Dict, Any, Optional, List
from datetime import datetime

from .client import dapr_client

# 导入 StateItem（如果可用）
try:
    from dapr.clients.grpc._state import StateItem
    STATE_ITEM_AVAILABLE = True
except ImportError:
    STATE_ITEM_AVAILABLE = False
    StateItem = None

logger = logging.getLogger(__name__)


class StateStore:
    """
    状态存储封装类
    
    使用 Dapr 的状态存储组件（默认使用 Redis）进行状态管理。
    支持事务操作和批量操作。
    """
    
    def __init__(self, store_name: str = "statestore"):
        """
        初始化状态存储
        
        Args:
            store_name: Dapr 状态存储组件名称（默认 "statestore"）
        """
        self.store_name = store_name
        self._use_dapr = dapr_client is not None
        if not self._use_dapr:
            logger.warning("Dapr client not available, StateStore will use in-memory storage")
            self._memory_store: Dict[str, Any] = {}
    
    async def save(
        self,
        key: str,
        value: Any,
        etag: Optional[str] = None,
        metadata: Optional[Dict[str, str]] = None,
    ) -> bool:
        """
        保存状态
        
        Args:
            key: 状态键
            value: 状态值（可以是字典、列表等可序列化对象）
            etag: 乐观锁版本号（可选）
            metadata: 元数据（可选）
        
        Returns:
            是否保存成功
        
        Example:
            ```python
            await state_store.save("user:123", {"name": "Alice", "age": 30})
            ```
        """
        try:
            if not self._use_dapr:
                # 使用内存存储
                self._memory_store[key] = value
                logger.debug(f"Saved state to memory: {key}")
                return True
            
            # 序列化值
            if isinstance(value, (str, bytes)):
                value_bytes = value if isinstance(value, bytes) else value.encode()
            else:
                value_bytes = json.dumps(value, ensure_ascii=False).encode('utf-8')
            
            # 使用 Dapr SDK 的 save_state 方法（同步调用包装在 asyncio.to_thread 中）
            await asyncio.to_thread(
                dapr_client.client.save_state,
                store_name=self.store_name,
                key=key,
                value=value_bytes,
                etag=etag,
                metadata=metadata or {},
            )
            
            logger.debug(f"Saved state: {key}")
            return True
        
        except Exception as e:
            logger.error(f"Failed to save state {key}: {e}")
            return False
    
    async def get(
        self,
        key: str,
        default: Any = None,
    ) -> Any:
        """
        获取状态
        
        Args:
            key: 状态键
            default: 默认值（如果键不存在）
        
        Returns:
            状态值，如果不存在则返回 default
        
        Example:
            ```python
            user = await state_store.get("user:123", {})
            ```
        """
        try:
            if not self._use_dapr:
                # 使用内存存储
                return self._memory_store.get(key, default)
            
            # 使用 Dapr SDK 的 get_state 方法（同步调用包装在 asyncio.to_thread 中）
            response = await asyncio.to_thread(
                dapr_client.client.get_state,
                store_name=self.store_name,
                key=key,
            )
            
            if not response.data:
                return default
            
            # 尝试解析 JSON
            try:
                return json.loads(response.data)
            except (json.JSONDecodeError, UnicodeDecodeError):
                return response.data.decode() if isinstance(response.data, bytes) else response.data
        
        except Exception as e:
            logger.error(f"Failed to get state {key}: {e}")
            return default
    
    async def delete(self, key: str, etag: Optional[str] = None) -> bool:
        """
        删除状态
        
        Args:
            key: 状态键
            etag: 乐观锁版本号（可选）
        
        Returns:
            是否删除成功
        """
        try:
            if not self._use_dapr:
                # 使用内存存储
                if key in self._memory_store:
                    del self._memory_store[key]
                    logger.debug(f"Deleted state from memory: {key}")
                return True
            
            # 使用 Dapr SDK 的 delete_state 方法（同步调用包装在 asyncio.to_thread 中）
            await asyncio.to_thread(
                dapr_client.client.delete_state,
                store_name=self.store_name,
                key=key,
                etag=etag,
            )
            logger.debug(f"Deleted state: {key}")
            return True
        
        except Exception as e:
            logger.error(f"Failed to delete state {key}: {e}")
            return False
    
    async def save_bulk(
        self,
        states: List[Dict[str, Any]],
    ) -> bool:
        """
        批量保存状态
        
        Args:
            states: 状态列表，每个元素包含 key, value, etag, metadata
        
        Returns:
            是否保存成功
        """
        try:
            if not self._use_dapr:
                # 使用内存存储
                for state in states:
                    key = state["key"]
                    value = state["value"]
                    self._memory_store[key] = value
                logger.debug(f"Saved {len(states)} states to memory in bulk")
                return True
            
            # 构建状态项列表
            state_items = []
            for state in states:
                key = state["key"]
                value = state["value"]
                
                # 序列化值
                if isinstance(value, (str, bytes)):
                    value_bytes = value if isinstance(value, bytes) else value.encode()
                else:
                    value_bytes = json.dumps(value, ensure_ascii=False).encode('utf-8')
                
                # 创建 StateItem（如果可用）
                if STATE_ITEM_AVAILABLE and StateItem:
                    state_item = StateItem(
                        key=key,
                        value=value_bytes,
                        etag=state.get("etag"),
                        metadata=state.get("metadata", {}),
                    )
                    state_items.append(state_item)
                else:
                    # 降级方案：使用字典格式
                    state_items.append({
                        "key": key,
                        "value": value_bytes,
                        "etag": state.get("etag"),
                        "metadata": state.get("metadata", {}),
                    })
            
            # 使用 Dapr SDK 的 save_bulk_state 方法（同步调用包装在 asyncio.to_thread 中）
            if STATE_ITEM_AVAILABLE:
                await asyncio.to_thread(
                    dapr_client.client.save_bulk_state,
                    store_name=self.store_name,
                    states=state_items,
                )
            else:
                # 降级方案：逐个保存（每个调用都包装在 asyncio.to_thread 中）
                for item in state_items:
                    await asyncio.to_thread(
                        dapr_client.client.save_state,
                        store_name=self.store_name,
                        key=item["key"],
                        value=item["value"],
                        etag=item.get("etag"),
                        metadata=item.get("metadata", {}),
                    )
            
            logger.debug(f"Saved {len(states)} states in bulk")
            return True
        
        except Exception as e:
            logger.error(f"Failed to save bulk states: {e}")
            return False
    
    async def get_bulk(
        self,
        keys: List[str],
    ) -> Dict[str, Any]:
        """
        批量获取状态
        
        Args:
            keys: 状态键列表
        
        Returns:
            状态字典，key -> value
        """
        try:
            if not self._use_dapr:
                # 使用内存存储
                result = {}
                for key in keys:
                    if key in self._memory_store:
                        result[key] = self._memory_store[key]
                return result
            
            # 使用 Dapr SDK 的 get_bulk_state 方法（同步调用包装在 asyncio.to_thread 中）
            response = await asyncio.to_thread(
                dapr_client.client.get_bulk_state,
                store_name=self.store_name,
                keys=keys,
            )
            
            result = {}
            for item in response.items:
                try:
                    result[item.key] = json.loads(item.data)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    result[item.key] = item.data.decode() if isinstance(item.data, bytes) else item.data
            
            return result
        
        except Exception as e:
            logger.error(f"Failed to get bulk states: {e}")
            return {}


# 全局实例
state_store = StateStore()
