"""Capability-owned policy hooks.

The memory-first kernel keeps transport compatibility in ``agent_core.py`` and
places skill/business/tool-domain guardrails here.  A capability may later move
these modules into its own Skill/MCP package; the core agent should only call the
generic hook surface.
"""
