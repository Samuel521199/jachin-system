"""
贾维斯式任务闭环测试：CRITICAL 提醒超时后自动升级到 VoIP 电话
"""

import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


class TestSentinelTask:
    """SentinelTask 数据模型测试"""

    def test_sentinel_task_model(self):
        from common.schemas.sentinel import SentinelTask, SentinelPriority

        task = SentinelTask(
            task_id="date_night_001",
            priority=SentinelPriority.CRITICAL,
            escalation_level=0,
            required_ack=True,
            context={"title": "约会提醒", "content": "和女朋友的约会，19:00"},
        )
        assert task.task_id == "date_night_001"
        assert task.priority == "critical"
        assert task.escalation_level == 0
        assert task.required_ack is True
        assert "约会" in task.context["content"]


class TestSentinelEscalation:
    """哨兵升级逻辑测试"""

    @pytest.fixture(autouse=True)
    def sentinel_test_mode(self):
        """启用 Sentinel 测试模式（mock 技能调用）"""
        os.environ["SENTINEL_TEST_MODE"] = "1"
        yield
        os.environ.pop("SENTINEL_TEST_MODE", None)

    @pytest.fixture(autouse=True)
    def init_ray(self):
        """确保 Ray 已初始化"""
        try:
            import ray
            if not ray.is_initialized():
                ray.init(ignore_reinit_error=True, include_dashboard=False)
            yield
        except Exception as e:
            pytest.skip(f"Ray not available: {e}")

    def test_sentinel_add_and_ack_task(self):
        """测试添加任务与确认"""
        import ray
        from core.brain.ray_actors.sentinel import SentinelActor

        actor = SentinelActor.remote()
        task_id = ray.get(actor.add_task.remote({
            "task_id": "test_001",
            "priority": "critical",
            "context": {"title": "测试", "content": "测试内容"},
        }))
        assert task_id == "test_001"

        pending = ray.get(actor.get_pending_tasks.remote())
        assert len(pending) == 1
        assert pending[0]["task_id"] == "test_001"

        acked = ray.get(actor.ack_task.remote("test_001"))
        assert acked is True

        pending = ray.get(actor.get_pending_tasks.remote())
        assert len(pending) == 0

    def test_sentinel_escalates_to_voip_when_critical_timeout(self):
        """
        模拟场景：CRITICAL 级别「约会提醒」，用户 5 分钟未点击弹窗，
        验证 Sentinel 自动升级并调用 VoIP 技能。
        """
        import ray
        from core.brain.ray_actors.sentinel import SentinelActor

        actor = SentinelActor.remote()
        ray.get(actor.clear_test_invokes.remote())

        # 添加 CRITICAL 任务：模拟 6 分钟前已发过桌面弹窗（level 0），
        # 10 分钟前已发过手机推送（level 1），已超时，应升级到 level 2 (voip_call)
        six_min_ago = (datetime.now() - timedelta(minutes=16)).isoformat()
        task = {
            "task_id": "date_night_001",
            "priority": "critical",
            "escalation_level": 1,
            "last_notified_at": six_min_ago,
            "required_ack": True,
            "context": {"title": "约会提醒", "content": "和女朋友的约会，19:00"},
        }
        ray.get(actor.add_task.remote(task))

        ray.get(actor.run_scan_once.remote())

        invokes = ray.get(actor.get_test_invokes.remote())
        voip_calls = [i for i in invokes if i.get("capability_name") == "voip_call"]

        assert len(voip_calls) >= 1, (
            f"Expected at least one voip_call, got invokes: {invokes}"
        )
        assert voip_calls[0]["skill_id"] == "voip"
        assert "约会" in voip_calls[0]["input_data"].get("message", "")

    def test_escalation_chain_constants(self):
        """验证升级链常量定义"""
        from core.brain.ray_actors.sentinel import REACH_ESCALATION_CHAIN, ESCALATION_TIMEOUTS

        assert "desktop_notify" in REACH_ESCALATION_CHAIN
        assert "mobile_push" in REACH_ESCALATION_CHAIN
        assert "voip_call" in REACH_ESCALATION_CHAIN
        assert REACH_ESCALATION_CHAIN.index("voip_call") > REACH_ESCALATION_CHAIN.index("desktop_notify")
        assert ESCALATION_TIMEOUTS[0] == 5
        assert ESCALATION_TIMEOUTS[2] == 0
