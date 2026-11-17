"""The single source of truth for what the tuned model is allowed to emit.

Everything here is derived from the running application, not restated by hand:

* ``TOOL_DEFINITIONS`` is imported from ``backend/app/tools/registry.py``.
* ``REASON_JSON_SCHEMA`` is imported from ``backend/app/services/anthropic_reasoning.py``
  when its dependencies are importable, and otherwise re-read from that file's
  source so the training environment does not need ``anthropic`` installed.

That matters more here than anywhere else in the repo. A fine-tune bakes a tool
vocabulary into weights; if this file drifted from the registry, the model would
confidently propose tools the policy engine has never heard of, and the drift
would be invisible until runtime. ``verify_contract()`` is called by every entry
point so the mismatch fails the build instead.

AGENTS.md: never invent capabilities. A generated dataset is exactly the place
that rule is easiest to break by accident.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = REPO_ROOT / "backend"
_REGISTRY_PATH = BACKEND_ROOT / "app" / "tools" / "registry.py"
_REASONING_PATH = BACKEND_ROOT / "app" / "services" / "anthropic_reasoning.py"


def _load_module_by_path(name: str, path: Path):
    """Import a single file without importing its package.

    ``app.tools.registry`` only needs ``dataclasses``/``typing``, so it loads
    cleanly on its own. Importing ``app`` as a package would instead drag in
    FastAPI, pydantic-settings and the whole service layer, which the training
    venv deliberately does not install.
    """
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise ImportError(f"cannot load {name} from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_registry = _load_module_by_path("_jarvis_registry", _REGISTRY_PATH)
TOOL_DEFINITIONS: Mapping[str, Any] = _registry.TOOL_DEFINITIONS
RISK_ORDER: Mapping[str, int] = _registry.RISK_ORDER

TOOL_NAMES: List[str] = sorted(TOOL_DEFINITIONS)


def _extract_literal(name: str) -> Any:
    """Read a module-level literal out of the provider's source.

    ``anthropic_reasoning`` imports the ``anthropic`` SDK at module scope, which
    the training venv has no reason to carry. These are plain literals, so
    parsing the assignment with ``ast`` gets the real value without importing
    anything -- and it still fails loudly if the literal stops being a literal.
    """
    tree = ast.parse(_REASONING_PATH.read_text(encoding="utf-8"))
    for node in tree.body:
        targets = []
        if isinstance(node, ast.AnnAssign):
            targets = [node.target]
        elif isinstance(node, ast.Assign):
            targets = node.targets
        for target in targets:
            if isinstance(target, ast.Name) and target.id == name:
                if node.value is None:  # pragma: no cover - defensive
                    raise ValueError(f"{name} has no value")
                return ast.literal_eval(node.value)
    raise ValueError(f"{name} not found in {_REASONING_PATH}")


REASON_JSON_SCHEMA: Dict[str, Any] = _extract_literal("REASON_JSON_SCHEMA")

# The fields the schema requires, pulled from the schema itself rather than
# typed out again.
REQUIRED_TOP_LEVEL: List[str] = list(REASON_JSON_SCHEMA["required"])
_CALL_SCHEMA = next(
    option
    for option in REASON_JSON_SCHEMA["properties"]["proposed_tool_call"]["anyOf"]
    if option.get("type") == "object"
)
REQUIRED_CALL_FIELDS: List[str] = list(_CALL_SCHEMA["required"])


# --- Values the application actually accepts -------------------------------
#
# These are not free invention either: each is quoted from the production
# system prompt in anthropic_reasoning.py, which is the behaviour the tuned
# model is being taught to reproduce. verify_contract() re-checks them against
# that file so editing the prompt without updating training data is caught.

SITE_KEYS: List[str] = [
    "youtube",
    "google",
    "netflix",
    "peacock",
    "hulu",
    "disneyplus",
    "gmail",
    "calendar",
]

# Only youtube/google actually receive search terms; every other key opens a
# homepage no matter what was asked.
SEARCHABLE_SITE_KEYS: List[str] = ["youtube", "google"]

HOME_ENTITIES: List[str] = ["light.bedroom"]

# Tools the prompt states take no target at all.
NO_TARGET_TOOLS: List[str] = [
    "get_current_time",
    "delete_all_memories",
    "get_pc_status",
    "list_open_windows",
]

UNTRUSTED_SOURCES: List[str] = [
    "CAMERA_OBSERVATION",
    "TOOL_RESULT",
    "MEMORY_RESULT",
    "EXTERNAL_CONTENT",
    "CONVERSATION_HISTORY",
]

AUTHORITATIVE_SOURCES: List[str] = ["SYSTEM_POLICY", "USER_REQUEST"]


# --- The distilled prompt the tuned model is trained against ---------------
#
# The production prompt is roughly 4k tokens of rules. Teaching those rules to
# the weights is the entire point of this fine-tune: the tuned model gets the
# short prompt below instead, which cuts prefill on every single voice turn.
#
# What stays in the prompt is only what must never be learned-and-forgotten:
# the output shape and the trust boundary. Target formatting, tool choice and
# tone are what the training data is for.

TUNED_SYSTEM_PROMPT: str = _extract_literal("TUNED_SYSTEM_PROMPT")


def render_user_message(context: List[Mapping[str, str]], question: str, available_tools: List[str]) -> str:
    """Byte-identical to LocalLanguageReasoningProvider._render_user_message.

    Training on a different input format than the server sends at inference
    time is the classic way a fine-tune looks good offline and regresses in
    production, so this mirrors the provider exactly.
    """
    rendered = "\n\n".join(f"[{item['source']}: {item['label']}]\n{item['content']}" for item in context)
    tools = ", ".join(available_tools) if available_tools else "(none)"
    return f"{rendered}\n\nAvailable tools: {tools}\n\nQuestion: {question}"


def empty_call() -> Dict[str, Any]:
    return {"tool_name": "", "target": "", "content": "", "arguments": {}}


def make_call(tool_name: str, target: str = "", content: str = "") -> Dict[str, Any]:
    return {"tool_name": tool_name, "target": target, "content": content, "arguments": {}}


def requires_confirmation_for(tool_name: str) -> bool:
    """Ask the real registry, never a hand-kept copy of it."""
    definition = TOOL_DEFINITIONS.get(tool_name)
    if definition is None:
        raise KeyError(f"unknown tool: {tool_name}")
    return bool(definition.requires_confirmation)


class ContractError(RuntimeError):
    """The training contract no longer matches the application."""


def verify_contract() -> None:
    """Fail loudly if the dataset's assumptions have drifted from the app.

    Called by generate/evaluate/train entry points. Cheap, and it converts a
    silent mislabelling bug into a startup error.
    """
    problems: List[str] = []

    for name in NO_TARGET_TOOLS + ["control_home_device", "open_website", "close_website"]:
        if name not in TOOL_DEFINITIONS:
            problems.append(f"tool referenced by training data is absent from the registry: {name}")

    prompt_source = _REASONING_PATH.read_text(encoding="utf-8")
    for key in SITE_KEYS:
        if key not in prompt_source:
            problems.append(f"site_key '{key}' is no longer mentioned in the production system prompt")
    for entity in HOME_ENTITIES:
        if entity not in prompt_source:
            problems.append(f"home entity '{entity}' is no longer mentioned in the production system prompt")

    # The tuned prompt is checked separately because it is the one the
    # fine-tune is trained and served under, and it used to omit these
    # identifiers entirely -- so the model was asked to recall literals that
    # appeared nowhere in its context, and mangled them on unfamiliar
    # phrasings. If a future edit drops them again, that regression should
    # fail here rather than surface as a dispatcher error months later.
    for key in SITE_KEYS:
        if key not in TUNED_SYSTEM_PROMPT:
            problems.append(f"site_key '{key}' is missing from the tuned system prompt")
    for entity in HOME_ENTITIES:
        if entity not in TUNED_SYSTEM_PROMPT:
            problems.append(f"home entity '{entity}' is missing from the tuned system prompt")

    expected_top = {"spoken_answer", "requires_confirmation", "proposed_tool_call"}
    if set(REQUIRED_TOP_LEVEL) != expected_top:
        problems.append(f"schema top-level required fields changed: {REQUIRED_TOP_LEVEL}")

    expected_call = {"tool_name", "target", "content", "arguments"}
    if set(REQUIRED_CALL_FIELDS) != expected_call:
        problems.append(f"schema tool-call required fields changed: {REQUIRED_CALL_FIELDS}")

    if problems:
        raise ContractError(
            "Training contract is out of date with the application:\n  - " + "\n  - ".join(problems)
        )


def contract_summary() -> str:
    return json.dumps(
        {
            "tools": len(TOOL_NAMES),
            "site_keys": SITE_KEYS,
            "no_target_tools": NO_TARGET_TOOLS,
            "confirmation_required": sorted(n for n in TOOL_NAMES if requires_confirmation_for(n)),
        },
        indent=2,
    )


__all__ = [
    "AUTHORITATIVE_SOURCES",
    "ContractError",
    "HOME_ENTITIES",
    "NO_TARGET_TOOLS",
    "REASON_JSON_SCHEMA",
    "REPO_ROOT",
    "SEARCHABLE_SITE_KEYS",
    "SITE_KEYS",
    "TOOL_DEFINITIONS",
    "TOOL_NAMES",
    "TUNED_SYSTEM_PROMPT",
    "UNTRUSTED_SOURCES",
    "contract_summary",
    "empty_call",
    "make_call",
    "render_user_message",
    "requires_confirmation_for",
    "verify_contract",
]
