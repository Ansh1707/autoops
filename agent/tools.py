"""Backward-compatible DevOps tool imports.

New code should import these from agent.tool_domains.devops.
"""

from agent.tool_domains.devops import get_metrics, search_logs


__all__ = ["search_logs", "get_metrics"]
