"""PMO 人类可读调试日志格式（v7 按轮次摘要）。"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from l3_node import pmo_copilot_debug_file as dbg


class TestPmoCopilotDebugFile(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        dbg._pending_action.clear()
        dbg._session.clear()
        self._old_env = os.environ.pop("JACHIN_PMO_COPILOT_DEBUG_LOG", None)

    def tearDown(self) -> None:
        dbg._pending_action.clear()
        dbg._session.clear()
        if self._old_env is not None:
            os.environ["JACHIN_PMO_COPILOT_DEBUG_LOG"] = self._old_env
        else:
            os.environ.pop("JACHIN_PMO_COPILOT_DEBUG_LOG", None)
        self._tmpdir.cleanup()

    def test_header_contains_v7_architecture(self) -> None:
        log = Path(self._tmpdir.name) / "pmo.txt"
        dbg.init_pmo_debug_session(
            log_path=log,
            user_message="跑宏观看板",
            correlation_id="abc",
            max_iterations=28,
        )
        text = log.read_text(encoding="utf-8")
        self.assertIn("PMO-Copilot 运行日志（人类可读 · 按轮次摘要）", text)
        self.assertIn("【架构速览 · v7】", text)
        self.assertIn("pmo_mirror_import", text)
        self.assertIn("ReAct 上限: 28 轮", text)

    def test_format_bi_round_lists_tables_and_output_dir(self) -> None:
        log = Path(self._tmpdir.name) / "pmo.txt"
        dbg.init_pmo_debug_session(
            log_path=log,
            user_message="跑宏观看板",
            correlation_id="abc",
            max_iterations=26,
        )
        obs = json.dumps(
            {
                "status": "success",
                "output_dir": "C:/Users/x/.jachin/data/pmo_tables/run1",
                "files": [
                    "需求进度_vewpI8lyYw.md",
                    "MANIFEST.json",
                    "人员矩阵_vewCz1FFJi.md",
                ],
                "nodes": [
                    {"view_id_hint": "vewpI8lyYw", "title": "需求进度"},
                    {"view_id_hint": "vewCz1FFJi", "title": "人员矩阵"},
                ],
            },
            ensure_ascii=False,
        )
        dbg.append_pmo_debug_action(
            tool="mcp:atom_bi_project_context",
            inp='{"wiki_urls":["https://example.com/wiki"]}',
            iteration=0,
            run_id="run1",
            thought="我需要先拉取飞书全部视图。",
        )
        dbg.append_pmo_debug_observation(
            tool="mcp:atom_bi_project_context",
            observation_full=obs,
            iteration=0,
            run_id="run1",
        )
        text = log.read_text(encoding="utf-8")
        self.assertIn("【第 1 / 26 轮】INIT · 拉表", text)
        self.assertIn("📌 这一步在做什么", text)
        self.assertIn("💭 Agent 想法", text)
        self.assertIn("🔧 调用了: mcp:atom_bi_project_context", text)
        self.assertIn("落盘目录: C:/Users/x/.jachin/data/pmo_tables/run1", text)
        self.assertIn("vewpI8lyYw", text)
        self.assertIn("✅ 本步无系统错误", text)

    def test_format_db_query_views_meta(self) -> None:
        log = Path(self._tmpdir.name) / "pmo.txt"
        dbg.init_pmo_debug_session(log_path=log, user_message="分析", max_iterations=10)
        obs = json.dumps(
            {
                "status": "ok",
                "row_count": 2,
                "rows": [
                    {"view_id": "vewpI8lyYw", "view_name": "K11 项目进度", "record_count": 2000},
                    {"view_id": "vewCz1FFJi", "view_name": "人工看板", "record_count": 22},
                ],
            },
            ensure_ascii=False,
        )
        sql = "SELECT view_id, view_name, record_count FROM pmo_views_meta;"
        dbg.append_pmo_debug_action(
            tool="core:db_query",
            inp=json.dumps({"sql": sql}, ensure_ascii=False),
            iteration=0,
            run_id="r",
            thought="先查看数据地图，了解各视图规模。",
        )
        dbg.append_pmo_debug_observation(
            tool="core:db_query",
            observation_full=obs,
            iteration=0,
            run_id="r",
        )
        text = log.read_text(encoding="utf-8")
        self.assertIn("Probe · 数据地图", text)
        self.assertIn("✅ 数据地图", text)
        self.assertIn("2,000 条记录", text)

    def test_format_mirror_import(self) -> None:
        log = Path(self._tmpdir.name) / "pmo.txt"
        dbg.init_pmo_debug_session(log_path=log, user_message="init", max_iterations=5)
        obs = json.dumps(
            {
                "status": "ok",
                "total_records": 100,
                "views": [
                    {"view_id": "vewpI8lyYw", "view_name": "开发计划", "record_count": 80},
                    {"view_id": "vewCz1FFJi", "record_count": 20},
                ],
            },
            ensure_ascii=False,
        )
        dbg.append_pmo_debug_action(
            tool="core:pmo_mirror_import",
            inp="{}",
            iteration=0,
            run_id="r",
        )
        dbg.append_pmo_debug_observation(
            tool="core:pmo_mirror_import",
            observation_full=obs,
            iteration=0,
            run_id="r",
        )
        text = log.read_text(encoding="utf-8")
        self.assertIn("INIT · 入库", text)
        self.assertIn("镜像入库成功", text)

    def test_finalize_appends_task_end(self) -> None:
        log = Path(self._tmpdir.name) / "pmo.txt"
        dbg.init_pmo_debug_session(log_path=log, user_message="done", max_iterations=5)
        dbg.finalize_pmo_debug_log("战报已生成")
        text = log.read_text(encoding="utf-8")
        self.assertIn("【任务结束】", text)
        self.assertIn("战报已生成", text)

    def test_notifier_block_human_readable(self) -> None:
        log = Path(self._tmpdir.name) / "pmo.txt"
        dbg.init_pmo_debug_session(log_path=log, user_message="推送", max_iterations=10)
        obs = json.dumps(
            {
                "status": "error",
                "error": "pmo_premature_notifier_blocked",
                "reason": "markdown_incomplete",
                "missing_sections": ["📊 需求进度全览（须有 GFM 表格 |）", "👥 人员任务矩阵（须有 GFM 表格 |）"],
                "msg": "【宿主拦截】markdown_content 缺少三表",
            },
            ensure_ascii=False,
        )
        inp = json.dumps(
            {
                "title": "PMO",
                "markdown_content": "只有摘要文字，没有表格",
                "chat_id": "oc_test",
            },
            ensure_ascii=False,
        )
        dbg.append_pmo_debug_action(
            tool="mcp:atom_lark_notifier",
            inp=inp,
            iteration=0,
            run_id="r",
        )
        dbg.append_pmo_debug_observation(
            tool="mcp:atom_lark_notifier",
            observation_full=obs,
            iteration=0,
            run_id="r",
        )
        text = log.read_text(encoding="utf-8")
        self.assertIn("飞书未发送", text)
        self.assertIn("三张 GFM 表格", text)
        self.assertIn("❌ 问题说明", text)

    def test_db_zero_rows_epic_on_personnel_view(self) -> None:
        log = Path(self._tmpdir.name) / "pmo.txt"
        dbg.init_pmo_debug_session(log_path=log, user_message="分析", max_iterations=10)
        sql = (
            "SELECT json_extract(fields, '$.Requirement') AS requirement "
            "FROM pmo_raw_records WHERE source_view = 'vewCz1FFJi' "
            "AND json_extract(fields, '$.$\"父记录\"[0].text') IS NULL"
        )
        obs = json.dumps(
            {
                "status": "ok",
                "row_count": 0,
                "rows": [],
                "hints": ["Epic 筛选不能在 vewCz1FFJi"],
            },
            ensure_ascii=False,
        )
        dbg.append_pmo_debug_action(
            tool="core:db_query",
            inp=json.dumps({"sql": sql}, ensure_ascii=False),
            iteration=0,
            run_id="r",
        )
        dbg.append_pmo_debug_observation(
            tool="core:db_query",
            observation_full=obs,
            iteration=0,
            run_id="r",
        )
        text = log.read_text(encoding="utf-8")
        self.assertIn("0 条", text)
        self.assertIn("人话解释", text)
        self.assertIn("人员看板", text)


    def test_gfm_table_thought_renders_human_readable(self) -> None:
        """Thought 含 GFM 表草稿时，目的/想法不得刷屏管道符与 JSON 列名。"""
        log = Path(self._tmpdir.name) / "pmo.txt"
        dbg.init_pmo_debug_session(log_path=log, user_message="分析", max_iterations=10)
        sql = (
            "SELECT source_view, raw_text, fields FROM pmo_raw_records "
            "WHERE source_view IN ('vewpI8lyYw', 'vewCz1FFJi') LIMIT 2"
        )
        cols = '["Requirement", "priority", "Sprint", "状态", "Person in charge/Participant"]'
        thought = (
            "Step1·地图完成。关键视图：vewpI8lyYw（2000条主表）、vewCz1FFJi（23条人员任务）。"
            "三表草稿初始化：\n"
            f"| vewpI8lyYw | 2000 | {cols} |\n"
            f"| vewCz1FFJi | 23 | {cols} |\n"
            "📦 Epic/顶层需求（Step4填）：\n"
            "（待填充）"
        )
        obs = json.dumps({"status": "ok", "row_count": 2, "rows": [{}, {}]}, ensure_ascii=False)
        dbg.append_pmo_debug_action(
            tool="core:db_query",
            inp=json.dumps({"sql": sql}, ensure_ascii=False),
            iteration=1,
            run_id="r",
            thought=thought,
        )
        dbg.append_pmo_debug_observation(
            tool="core:db_query",
            observation_full=obs,
            iteration=1,
            run_id="r",
        )
        text = log.read_text(encoding="utf-8")
        self.assertIn("Step2 样本", text)
        self.assertIn("📌 这一步在做什么", text)
        self.assertIn("Step1·地图完成", text)
        self.assertIn("表草稿：", text)
        self.assertIn("vewpI8lyYw（2000 条）", text)
        # 目的行不应以管道符开头
        purpose_block = text.split("📌 这一步在做什么", 1)[1].split("💭 Agent 想法", 1)[0]
        self.assertNotRegex(purpose_block, r"\|\s*vewpI8lyYw")
        # 想法区不应整段 dump JSON 列名数组
        idea_block = text.split("💭 Agent 想法", 1)[1].split("🔧 调用了", 1)[0]
        self.assertNotIn('"Requirement", "priority"', idea_block)


if __name__ == "__main__":
    unittest.main()
