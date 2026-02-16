# 测试指南

## 概述

本文档提供了 Jachin-System 的完整测试指南，包括如何运行测试、理解测试结果和编写新测试。

## 测试结构

```
tests/
├── unit/              # 单元测试
│   ├── test_manifest_parser.py
│   └── test_skill_loader.py
├── integration/      # 集成测试
│   ├── test_plugin_system.py
│   ├── test_skill_execution.py
│   ├── test_intent_planning.py          # ⭐ 新增
│   └── test_e2e_natural_language.py    # ⭐ 新增
├── e2e/              # 端到端测试
│   ├── test_api_endpoints.py
│   └── test_plugin_gateway_e2e.py
├── performance/      # 性能测试 ⭐ 新增
│   └── test_plugin_execution_performance.py
├── mocks/            # Mock 对象 ⭐ 新增
│   └── mock_llm.py
├── conftest.py       # pytest配置和fixtures
└── README.md
```

## 快速开始

### 运行所有测试

```powershell
# 使用测试脚本（推荐）
.\scripts\run_tests.ps1

# 或直接使用 pytest
pytest tests/ -v
```

### 运行特定类型的测试

```powershell
# 单元测试
.\scripts\run_tests.ps1 -TestType unit

# 集成测试
.\scripts\run_tests.ps1 -TestType integration

# 端到端测试
.\scripts\run_tests.ps1 -TestType e2e

# 性能测试
.\scripts\run_tests.ps1 -TestType performance
```

### 生成覆盖率报告

```powershell
.\scripts\run_tests.ps1 -Coverage

# 查看报告
# 打开 htmlcov/index.html
```

## 测试类型详解

### 1. 单元测试

**位置**: `tests/unit/`

**目的**: 测试单个函数或类的功能

**示例**:
```python
def test_manifest_parser():
    """测试 manifest 解析"""
    manifest_data = {"id": "com.test.plugin", "version": "1.0.0"}
    manifest = parse_manifest(manifest_data)
    assert manifest.id == "com.test.plugin"
```

**运行**:
```bash
pytest tests/unit/ -v
```

### 2. 集成测试

**位置**: `tests/integration/`

**目的**: 测试多个组件协同工作

**新增测试**:
- `test_intent_planning.py`: IntentPlanner 集成测试
- `test_e2e_natural_language.py`: 端到端自然语言测试

**示例**:
```python
@pytest.mark.asyncio
async def test_natural_language_to_plugin_execution():
    """测试自然语言到插件执行的完整流程"""
    plan = await intent_planner.plan("查看系统状态")
    assert plan.plugin_id == "com.jachin.sys-monitor"
    
    result = await plugin_executor.invoke_plugin(...)
    assert result["status_code"] == 200
```

**运行**:
```bash
pytest tests/integration/ -v
```

### 3. 端到端测试

**位置**: `tests/e2e/`

**目的**: 测试完整的用户流程

**示例**:
```python
@pytest.mark.asyncio
async def test_plugin_gateway_e2e():
    """端到端测试：Gateway RPC 调用"""
    request = PluginRequest(...)
    response = await gateway.InvokePlugin(request, context)
    assert response.status_code == 200
```

**运行**:
```bash
pytest tests/e2e/ -v
```

### 4. 性能测试

**位置**: `tests/performance/`

**目的**: 测试系统性能指标

**新增测试**:
- `test_plugin_execution_performance.py`: 插件执行性能测试

**测试内容**:
- 插件执行延迟
- 并发执行性能
- Actor 创建和销毁性能
- 内存使用情况
- 大 payload 性能

**运行**:
```bash
pytest tests/performance/ -v
```

**性能基准**:
- 插件执行延迟: < 2s
- 并发吞吐量: > 10 req/s
- Actor 创建时间: < 3s
- 内存增长: < 100MB（10 次调用）
- 端到端延迟: < 5s

## Mock 对象

### Mock LLM Provider

**位置**: `tests/mocks/mock_llm.py`

**用途**: 模拟 LLM 响应，避免实际 API 调用

**使用示例**:
```python
from tests.mocks import create_intent_planning_mock

mock_llm = create_intent_planning_mock()
# 使用 mock_llm 进行测试
```

## 测试 Fixtures

### 常用 Fixtures

**`temp_plugin_dirs`**: 创建临时插件目录
```python
def test_with_temp_dirs(temp_plugin_dirs):
    plugins_dir, skills_repo_dir = temp_plugin_dirs
    # 使用临时目录
```

**`plugin_manager`**: 创建插件管理器
```python
def test_with_plugin_manager(plugin_manager):
    # 使用插件管理器
```

**`plugin_executor`**: 创建插件执行器
```python
def test_with_executor(plugin_executor):
    # 使用插件执行器
```

**`ray_init`**: 初始化 Ray（模块级别）
```python
@pytest.fixture(scope="module", autouse=True)
def ray_init():
    if not ray.is_initialized():
        ray.init(local_mode=True)
    yield
    ray.shutdown()
```

## 性能监控

### 查询性能指标

```powershell
# 查询性能统计
.\scripts\check_performance.ps1 -Endpoint stats

# 查询最近的指标
.\scripts\check_performance.ps1 -Endpoint metrics -Minutes 5

# 查询最近的错误
.\scripts\check_performance.ps1 -Endpoint errors -Minutes 5

# 查询告警
.\scripts\check_performance.ps1 -Endpoint alerts
```

### 使用 API

```bash
# 获取所有统计
curl http://localhost:18888/api/v3/monitoring/stats

# 获取特定指标统计
curl http://localhost:18888/api/v3/monitoring/stats?metric_name=plugin.execution

# 获取最近的指标
curl http://localhost:18888/api/v3/monitoring/metrics?minutes=5

# 获取告警
curl http://localhost:18888/api/v3/monitoring/alerts
```

## 编写新测试

### 单元测试模板

```python
def test_function_name():
    """测试描述"""
    # Arrange: 准备测试数据
    input_data = "test"
    
    # Act: 执行被测试的函数
    result = function_to_test(input_data)
    
    # Assert: 验证结果
    assert result == expected_value
```

### 异步测试模板

```python
@pytest.mark.asyncio
async def test_async_function():
    """测试异步函数"""
    result = await async_function_to_test()
    assert result is not None
```

### 集成测试模板

```python
@pytest.mark.asyncio
@pytest.mark.requires_ray
async def test_integration(plugin_executor, temp_plugin_dirs, ray_init):
    """集成测试"""
    # 1. 准备测试数据
    plugin_id = "com.test.plugin"
    
    # 2. 执行操作
    result = await plugin_executor.invoke_plugin(...)
    
    # 3. 验证结果
    assert result["status_code"] == 200
```

## 测试标记

### 常用标记

- `@pytest.mark.asyncio`: 异步测试
- `@pytest.mark.requires_ray`: 需要 Ray 集群
- `@pytest.mark.slow`: 慢速测试（可选）

### 运行特定标记的测试

```bash
# 只运行快速测试
pytest tests/ -v -m "not slow"

# 只运行需要 Ray 的测试
pytest tests/ -v -m "requires_ray"
```

## 测试覆盖率

### 生成覆盖率报告

```bash
# HTML 报告
pytest tests/ --cov=core --cov-report=html

# 终端报告
pytest tests/ --cov=core --cov-report=term

# 同时生成两种报告
pytest tests/ --cov=core --cov-report=html --cov-report=term
```

### 覆盖率目标

- **单元测试**: > 70%
- **集成测试**: > 50%
- **端到端测试**: > 30%
- **总体覆盖率**: > 60%

## 常见问题

### 1. Ray 未初始化

**错误**: `Ray is not initialized`

**解决**:
```python
import ray
ray.init(local_mode=True)
```

### 2. 测试数据库连接失败

**错误**: `无法连接到数据库`

**解决**: 确保 PostgreSQL 服务已启动

### 3. Mock LLM 不工作

**错误**: `Mock LLM 返回 None`

**解决**: 检查是否正确使用了 `@patch` 装饰器

### 4. 性能测试失败

**错误**: `性能测试超时`

**解决**: 
- 检查系统资源
- 增加超时时间
- 检查 Ray 集群状态

## 持续集成

### GitHub Actions 示例

```yaml
name: Tests

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v2
      - uses: actions/setup-python@v2
        with:
          python-version: '3.10'
      - run: pip install -r requirements.txt
      - run: pip install pytest pytest-asyncio pytest-cov
      - run: pytest tests/ --cov=core --cov-report=xml
```

## 相关文档

- `docs/ERROR_HANDLING.md`: 错误处理规范
- `docs/DEVELOPMENT_ORDER_ANALYSIS.md`: 开发顺序分析
- `tests/README.md`: 测试文档
