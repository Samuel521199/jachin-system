# 测试说明

## 测试结构

### 新增测试模块

- **`tests/integration/test_intent_planning.py`**: IntentPlanner 集成测试
- **`tests/integration/test_e2e_natural_language.py`**: 端到端自然语言测试
- **`tests/performance/test_plugin_execution_performance.py`**: 性能基准测试
- **`tests/mocks/mock_llm.py`**: Mock LLM Provider

## 测试结构

```
tests/
├── unit/              # 单元测试
│   ├── test_manifest_parser.py
│   └── test_skill_loader.py
├── integration/      # 集成测试
│   └── test_skill_execution.py
├── e2e/              # 端到端测试
│   └── test_api_endpoints.py
├── conftest.py       # pytest配置和fixtures
└── README.md         # 本文件
```

## 运行测试

### 运行所有测试

```bash
# 从项目根目录运行
pytest tests/

# 或使用详细输出
pytest tests/ -v
```

### 运行特定类型的测试

```bash
# 只运行单元测试
pytest tests/unit/

# 只运行集成测试
pytest tests/integration/

# 只运行端到端测试
pytest tests/e2e/
```

### 运行特定测试文件

```bash
pytest tests/unit/test_manifest_parser.py
```

### 运行特定测试函数

```bash
pytest tests/unit/test_manifest_parser.py::test_valid_manifest_yaml
```

## 测试覆盖率

```bash
# 生成覆盖率报告
pytest tests/ --cov=core --cov-report=html

# 查看覆盖率报告
# 打开 htmlcov/index.html
```

## 注意事项

1. **数据库测试**: 集成测试需要数据库连接，确保PostgreSQL服务已启动
2. **环境变量**: 某些测试可能需要环境变量，检查`.env`文件
3. **Docker**: Docker沙箱测试需要Docker服务运行
4. **Ray**: Ray相关测试需要Ray集群（Single模式即可）

## 编写新测试

### 单元测试示例

```python
def test_function_name():
    """测试描述"""
    # Arrange
    input_data = "test"
    
    # Act
    result = function_to_test(input_data)
    
    # Assert
    assert result == expected_value
```

### 异步测试示例

```python
@pytest.mark.asyncio
async def test_async_function():
    """测试异步函数"""
    result = await async_function_to_test()
    assert result is not None
```

### 使用Fixtures

```python
def test_with_db_session(db_session):
    """使用数据库会话的测试"""
    # db_session是conftest.py中定义的fixture
    assert db_session is not None
```
