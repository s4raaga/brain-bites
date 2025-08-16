"""Data acquisition & Blackboard MCP package.

Exposes:
 - agent (Anthropic<->MCP orchestration)
 - bb_mcp (Playwright-backed MCP server tools)
"""

# Convenience re-exports (optional)
from . import agent  # noqa: F401
from . import bb_mcp  # noqa: F401
