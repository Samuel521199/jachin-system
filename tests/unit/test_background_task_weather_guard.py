"""后台任务：禁止仅投递 util:get_weather_lite（须前台同步）。"""

from l3_node.primitives.agent_tasks.background_task_service import _only_util_get_weather_lite_skills


def test_only_util_get_weather_lite_skills_true() -> None:
    assert _only_util_get_weather_lite_skills(["util:get_weather_lite"]) is True
    assert _only_util_get_weather_lite_skills(["util_get_weather_lite"]) is True


def test_only_util_get_weather_lite_skills_false() -> None:
    assert _only_util_get_weather_lite_skills([]) is False
    assert _only_util_get_weather_lite_skills(["util:get_weather_lite", "core:fs_read"]) is False
