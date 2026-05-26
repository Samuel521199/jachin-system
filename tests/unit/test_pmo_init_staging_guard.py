"""PMO INIT staging write→import 交替守卫。"""
from __future__ import annotations

import json
import unittest

from l3_node.agent_core import (
    _pmo_init_blocked_stacked_staging_write,
    _pmo_init_track_staging_io,
)
from l3_node.engine.hooks_pipeline import PipelineContext


class TestPmoInitStagingGuard(unittest.TestCase):
    def _init_ctx(self) -> PipelineContext:
        ctx = PipelineContext(
            intent="INIT",
            metadata={
                "_implicit_channel": "pmo_copilot_cli",
                "pmo_init_mode": True,
            },
        )
        return ctx

    def test_blocks_second_staging_write_before_import(self) -> None:
        ctx = self._init_ctx()
        inp1 = json.dumps({"file_path": "pmo_staging/vewpI8lyYw_part1.ndjson", "content": "{}"})
        _pmo_init_track_staging_io(ctx, "core:fs_write", inp1, "ok")
        inp2 = json.dumps({"file_path": "pmo_staging/vewpI8lyYw_part2.ndjson", "content": "{}"})
        blocked = _pmo_init_blocked_stacked_staging_write("core:fs_write", inp2, ctx)
        self.assertIsNotNone(blocked)
        self.assertIn("pmo_init_stacked_staging_write_blocked", blocked or "")

    def test_allows_next_write_after_import(self) -> None:
        ctx = self._init_ctx()
        inp1 = json.dumps({"file_path": "pmo_staging/vewpI8lyYw_part1.ndjson", "content": "{}"})
        _pmo_init_track_staging_io(ctx, "core:fs_write", inp1, "ok")
        _pmo_init_track_staging_io(
            ctx,
            "core:pmo_import_json",
            json.dumps({"file_path": "pmo_staging/vewpI8lyYw_part1.ndjson"}),
            json.dumps({"status": "ok"}),
        )
        inp2 = json.dumps({"file_path": "pmo_staging/vewpI8lyYw_part2.ndjson", "content": "{}"})
        blocked = _pmo_init_blocked_stacked_staging_write("core:fs_write", inp2, ctx)
        self.assertIsNone(blocked)


if __name__ == "__main__":
    unittest.main()
