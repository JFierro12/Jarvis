from dataclasses import dataclass
from typing import List, Optional

from app.tools.registry import TOOL_DEFINITIONS, ToolDefinition


@dataclass
class PolicyResult:
    decision: str  # "allow" | "require_confirmation" | "deny"
    reason: Optional[str] = None
    confirmation_summary: Optional[str] = None
    risk_level: Optional[str] = None


def evaluate_tool_call(
    tool_name: str,
    target: str,
    granted_permissions: List[str],
) -> PolicyResult:
    """The single deterministic authority over whether a tool call may run.

    Mirrors ios/JarvisKit/Sources/JarvisKit/Services/PolicyEngine.swift —
    a language model may propose a tool call, but only this function (or its
    iOS counterpart) decides whether it is permitted. Never let a model's
    own output grant itself additional tool permissions.
    """
    definition: Optional[ToolDefinition] = TOOL_DEFINITIONS.get(tool_name)
    if definition is None:
        return PolicyResult(decision="deny", reason=f"Unknown tool: {tool_name}")

    granted = set(granted_permissions)
    missing = [p for p in definition.required_permissions if p not in granted]
    if missing:
        return PolicyResult(decision="deny", reason=f"Missing permission(s): {', '.join(sorted(missing))}")

    if definition.requires_confirmation:
        summary = definition.description if not target else f"{definition.description} ({target})"
        return PolicyResult(
            decision="require_confirmation",
            confirmation_summary=summary,
            risk_level=definition.risk_level,
        )

    return PolicyResult(decision="allow", risk_level=definition.risk_level)
