# -*- coding: utf-8 -*-
"""Verification Agent 角色与 VERDICT 解析。"""
from __future__ import annotations

from l3_node.primitives.multi_agent.verification_agent import (
    VERIFICATION_ROLE_ID,
    VERIFICATION_SYSTEM_PROMPT,
    build_verification_role,
    parse_verification_verdict,
    pmo_verification_audit_enabled,
)


def test_verification_role_id():
    role = build_verification_role()
    assert role["id"] == VERIFICATION_ROLE_ID


def test_parse_verification_verdict_pass():
    text = "## 发现\n无\n## VERDICT\nVERDICT: PASS"
    assert parse_verification_verdict(text) == "PASS"


def test_parse_verification_verdict_fail_case_insensitive():
    assert parse_verification_verdict("## VERDICT\nVerdict: FAIL") == "FAIL"


def test_parse_verification_verdict_partial():
    assert parse_verification_verdict("结论 VERDICT: PARTIAL 因数据不足") == "PARTIAL"


def test_parse_verification_verdict_unknown():
    assert parse_verification_verdict("看起来没问题") == "UNKNOWN"


def test_verification_prompt_requires_verdict():
    assert "VERDICT: PASS" in VERIFICATION_SYSTEM_PROMPT
    assert "禁止" in VERIFICATION_SYSTEM_PROMPT


def test_pmo_audit_disabled_by_default():
    import os

    old = os.environ.pop("PMO_ENABLE_VERIFICATION_AUDIT", None)
    try:
        assert pmo_verification_audit_enabled() is False
    finally:
        if old is not None:
            os.environ["PMO_ENABLE_VERIFICATION_AUDIT"] = old


def test_pmo_audit_enabled_env():
    import os

    os.environ["PMO_ENABLE_VERIFICATION_AUDIT"] = "1"
    try:
        assert pmo_verification_audit_enabled() is True
    finally:
        os.environ.pop("PMO_ENABLE_VERIFICATION_AUDIT", None)
