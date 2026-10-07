"""Prometheus metrics for the LinkedIn MCP server.

Exposes a tool-call counter (``linkedin_mcp_tool_calls_total``) plus the
default ``prometheus_client`` process/GC collectors, served as plain text
at ``/metrics`` via a FastMCP custom route (see ``server.py``).
"""
from __future__ import annotations

import functools
import logging
from typing import Awaitable, Callable, TypeVar

from prometheus_client import Counter

logger = logging.getLogger(__name__)

TOOL_CALLS_TOTAL = Counter(
    "linkedin_mcp_tool_calls_total",
    "MCP tool calls by tool and outcome",
    ["tool", "outcome"],
)

SESSION_SEEDS_TOTAL = Counter(
    "linkedin_mcp_session_seeds_total",
    "Session LinkedIn injectée depuis le fichier dans le profil du navigateur "
    "(profil sans li_at valide) — chaque hausse = LinkedIn a supprimé la session "
    "ou le profil a été remis à zéro",
)


def record_session_seed(seeded: bool) -> None:
    """Compte un amorçage de session ; une session conservée ne compte pas."""
    if seeded:
        SESSION_SEEDS_TOTAL.inc()

F = TypeVar("F", bound=Callable[..., Awaitable[object]])


def track_tool_calls(func: F) -> F:
    """Increment ``linkedin_mcp_tool_calls_total`` around an async MCP tool.

    Preserves the wrapped function's name/signature (via ``functools.wraps``)
    so FastMCP's introspection of ``@mcp.tool()`` functions keeps working.
    Re-raises whatever the tool raised — this only observes outcomes, it
    never changes error handling.
    """

    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        try:
            result = await func(*args, **kwargs)
        except Exception:
            TOOL_CALLS_TOTAL.labels(tool=func.__name__, outcome="error").inc()
            raise
        else:
            TOOL_CALLS_TOTAL.labels(tool=func.__name__, outcome="ok").inc()
            return result

    return wrapper  # type: ignore[return-value]
