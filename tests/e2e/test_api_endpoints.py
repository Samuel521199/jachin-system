"""
API端点端到端测试
API Endpoints End-to-End Tests
"""

import pytest
from fastapi.testclient import TestClient
from core.main import app


@pytest.fixture
def client():
    """创建测试客户端"""
    return TestClient(app)


def test_health_check(client):
    """测试健康检查端点"""
    response = client.get("/health")
    
    assert response.status_code == 200
    data = response.json()
    assert "status" in data
    assert data["status"] == "healthy"


def test_api_docs(client):
    """测试API文档端点"""
    response = client.get("/docs")
    
    # Swagger UI应该返回200
    assert response.status_code == 200


def test_list_skills_endpoint(client):
    """测试列出技能端点"""
    response = client.get("/api/v3/skills")
    
    # 应该返回200（即使没有技能）
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)


def test_cluster_stats_endpoint(client):
    """测试集群统计端点"""
    response = client.get("/api/v3/cluster/stats")
    
    # 应该返回200
    assert response.status_code == 200
    data = response.json()
    assert "nodes" in data
    assert "tasks" in data
