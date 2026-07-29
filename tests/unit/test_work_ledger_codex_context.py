from __future__ import annotations


def test_codex_context_pack_ranks_redacts_and_stays_within_budget(
    tmp_path,
):
    from l3_node.work_ledger_codex_context import build_codex_context_pack

    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "outside.py"
    secret = "sk-this-is-a-real-looking-secret-value"
    patch = (
        "diff --git a/src/router.py b/src/router.py\n"
        "--- a/src/router.py\n"
        "+++ b/src/router.py\n"
        "@@ -1 +1 @@\n"
        f"-API_KEY={secret}\n"
        "+router = build_capability_router()\n"
        "diff --git a/docs/unrelated.md b/docs/unrelated.md\n"
        "--- a/docs/unrelated.md\n"
        "+++ b/docs/unrelated.md\n"
        "@@ -1 +1 @@\n"
        + ("-old\n+unrelated documentation text\n" * 300)
        + "diff --git a/.env b/.env\n"
        "--- a/.env\n"
        "+++ b/.env\n"
        "@@ -1 +1 @@\n"
        "-TOKEN=secret\n"
    )
    pack = build_codex_context_pack(
        project_name="Jachin",
        project_path=str(project),
        task_title="增强 capability router",
        user_goal="让任务自动选择正确 capability",
        purpose="分析 router 变更",
        changed_files=[
            {"path": "docs/unrelated.md", "status": "M"},
            {"path": "src/router.py", "status": "M"},
            {"path": ".env", "status": "M"},
            {"path": str(outside), "status": "M"},
        ],
        diff_patch=patch,
        file_snippets=[
            {
                "path": "src/router.py",
                "excerpt": (
                    "owner_email=owner@example.com\n"
                    "owner_phone=13800138000\n"
                    f"api_key={secret}\n"
                    "def build_capability_router(): ..."
                ),
            },
            {"path": ".env", "excerpt": "TOKEN=not-allowed"},
            {"path": str(outside), "excerpt": "outside project"},
        ],
        max_chars=5000,
    )

    serialized = pack["serialized"]
    stats = pack["stats"]
    assert len(serialized) <= 5000
    assert stats["within_budget"]
    assert secret not in serialized
    assert "owner@example.com" not in serialized
    assert "13800138000" not in serialized
    assert "[REDACTED" in serialized
    assert pack["context"]["changed_files"][0]["path"] == "src/router.py"
    assert any(row["path"] == ".env" for row in stats["blocked_paths"])
    assert any(
        row["reason"] == "outside_project_root" for row in stats["blocked_paths"]
    )
    assert stats["redaction_type_counts"]["api_key"] >= 1


def test_codex_context_pack_digest_changes_with_relevant_evidence(
    tmp_path,
):
    from l3_node.work_ledger_codex_context import build_codex_context_pack

    project = tmp_path / "project"
    project.mkdir()
    common = {
        "project_name": "Jachin",
        "project_path": str(project),
        "task_title": "工作计划",
        "user_goal": "总结今天的改动",
        "changed_files": [{"path": "src/main.py", "status": "M"}],
    }
    first = build_codex_context_pack(
        **common,
        diff_patch="+first implementation",
    )
    same = build_codex_context_pack(
        **common,
        diff_patch="+first implementation",
    )
    changed = build_codex_context_pack(
        **common,
        diff_patch="+second implementation",
    )

    assert first["digest"] == same["digest"]
    assert first["digest"] != changed["digest"]


def test_work_plan_prompt_exposes_context_pack_audit_metadata(
    tmp_path,
):
    from l3_node.work_ledger_codex import build_codex_work_plan_prompt

    project = tmp_path / "project"
    project.mkdir()
    prompt, meta = build_codex_work_plan_prompt(
        {
            "project_name": "Jachin",
            "project_path": str(project),
            "title": "优化 Codex 协作",
            "user_goal": "限制上下文并保护密钥",
            "gap_keys": ["accomplishment_meaning"],
            "changed_files": [
                {"path": "l3_node/work_ledger_codex.py", "status": "M"}
            ],
            "diff_stat": "1 file changed",
            "diff_patch": "+API_KEY=sk-sensitive-value-1234567890",
            "cached_diff_patch": "",
            "file_snippets": [],
        }
    )

    assert "sk-sensitive-value-1234567890" not in prompt
    assert meta["context_pack"]["stats"]["within_budget"]
    assert meta["context_pack"]["digest"]
