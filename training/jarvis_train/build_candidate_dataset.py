"""Turn reviewed candidate examples into a versioned training file.

This is the "build dataset" stage of the self-training lifecycle, and the one
fixed entry point the backend's job runner invokes for it. It takes an
`approved.jsonl` export from the review queue and emits a `train.jsonl` the
trainer can read, plus a report of what it dropped and why.

Two things it refuses to do, both deliberate:

**It will not build a corrections-only dataset by default.** `--base` mixes in
the existing curated corpus. Fine-tuning on nothing but the cases the
assistant got wrong is how a model forgets everything it already did
correctly -- the corrections are, by construction, an unrepresentative sample
of the input distribution. Run 2 is the local evidence: it learned to route
well and became *worse than its own base model* at answering ordinary
questions.

**It will not silently build a dataset too small to evaluate.** Below
`--min-examples` it exits non-zero. A candidate trained on forty corrections
cannot produce an evaluation that distinguishes improvement from noise, and
the resulting "it got better" would be measurement error with a version
number attached.

Every row is re-validated against the live response contract here even though
the queue validated it at submission time. The queue's copy of the registry
could have been read before a tool was renamed; this check happens at the
moment the file is written.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Mapping, Tuple

from jarvis_train.contract import (
    TOOL_DEFINITIONS,
    TUNED_SYSTEM_PROMPT,
    verify_contract,
)


def _load_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows = []
    with path.open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise SystemExit(f"{path}:{number}: not valid JSON: {exc.msg}") from exc
    return rows


def _messages_of(row: Mapping[str, Any]) -> List[Mapping[str, str]]:
    messages = row.get("messages")
    if not isinstance(messages, list):
        return []
    return [m for m in messages if isinstance(m, dict)]


def _assistant_text(row: Mapping[str, Any]) -> str:
    for message in reversed(_messages_of(row)):
        if message.get("role") == "assistant":
            return str(message.get("content", ""))
    return ""


def _user_text(row: Mapping[str, Any]) -> str:
    for message in _messages_of(row):
        if message.get("role") == "user":
            return str(message.get("content", ""))
    return ""


def validate_row(row: Mapping[str, Any]) -> Tuple[bool, str]:
    """Re-check one row against the response contract.

    Mirrors `app.services.learning.validate_expected_response`. The
    duplication is intentional: the backend cannot import the training package
    and the training venv does not carry FastAPI, so each side validates with
    its own copy and `test_dataset_contract.py` asserts they agree.
    """
    messages = _messages_of(row)
    roles = [m.get("role") for m in messages]
    if roles[:1] != ["system"]:
        return False, "first message is not a system prompt"
    if "user" not in roles or "assistant" not in roles:
        return False, "row needs both a user and an assistant message"

    text = _assistant_text(row).strip()
    if not text:
        return False, "assistant message is empty"

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        return False, f"assistant message is not valid JSON: {exc.msg}"
    if not isinstance(payload, dict):
        return False, "assistant message is not a JSON object"

    required = {"spoken_answer", "requires_confirmation", "proposed_tool_call"}
    if set(payload) != required:
        return False, f"keys {sorted(set(payload))} != {sorted(required)}"
    if not isinstance(payload["spoken_answer"], str):
        return False, "spoken_answer is not a string"
    if not isinstance(payload["requires_confirmation"], bool):
        return False, "requires_confirmation is not a boolean"

    call = payload["proposed_tool_call"]
    if call is None:
        return True, ""
    if not isinstance(call, dict):
        return False, "proposed_tool_call is neither an object nor null"
    call_required = {"tool_name", "target", "content", "arguments"}
    if set(call) != call_required:
        return False, f"tool call keys {sorted(set(call))} != {sorted(call_required)}"
    if call["arguments"] != {}:
        return False, "arguments must be an empty object"
    name = call["tool_name"]
    if name not in TOOL_DEFINITIONS:
        return False, f"{name!r} is not a registered tool"

    want_confirm = TOOL_DEFINITIONS[name].requires_confirmation
    if want_confirm and not payload["requires_confirmation"]:
        return False, f"{name} requires confirmation but the row does not set the flag"
    return True, ""


def normalise_row(row: Mapping[str, Any], system_prompt: str) -> Dict[str, Any]:
    """Rewrite a row's system prompt to the current tuned prompt.

    An approved example may have been collected under an older prompt. Keeping
    the stale text would train the model on a prompt the application no longer
    sends, which is a subtle way to make an evaluation unreproducible.
    """
    return {
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": _user_text(row)},
            {"role": "assistant", "content": _assistant_text(row)},
        ]
    }


def dedupe(rows: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], int]:
    """Drop rows repeating an earlier row's user message.

    First occurrence wins, so a curated base row is kept over a later
    correction with the same prompt. A conflict between them is a review
    question, not something to settle by training on both.
    """
    seen = set()
    kept = []
    dropped = 0
    for row in rows:
        key = " ".join(_user_text(row).lower().split())
        if key in seen:
            dropped += 1
            continue
        seen.add(key)
        kept.append(row)
    return kept, dropped


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build a candidate training set from reviewed examples."
    )
    parser.add_argument("--approved", type=Path, required=True,
                        help="JSONL export of approved candidate examples")
    parser.add_argument("--out", type=Path, required=True,
                        help="where to write the assembled train.jsonl")
    parser.add_argument("--base", type=Path, default=None,
                        help="curated corpus to mix in; omitting it builds a "
                             "corrections-only set, which usually causes forgetting")
    parser.add_argument("--min-examples", type=int, default=200,
                        help="refuse to write a set smaller than this")
    parser.add_argument("--allow-corrections-only", action="store_true",
                        help="permit a build with no --base, for a deliberate "
                             "narrow experiment")
    args = parser.parse_args(argv)

    # Fails loudly if the training data's assumptions have drifted from the
    # running application's tool registry.
    verify_contract()

    if not args.approved.exists():
        print(f"no approved export at {args.approved}", file=sys.stderr)
        return 2

    approved = _load_jsonl(args.approved)
    print(f"  {len(approved)} approved examples read from {args.approved.name}")

    if args.base is None and not args.allow_corrections_only:
        print(
            "refusing to build a corrections-only dataset: pass --base with the "
            "curated corpus, or --allow-corrections-only if that is genuinely "
            "what you want. Training on corrections alone causes the model to "
            "forget what it already did correctly.",
            file=sys.stderr,
        )
        return 2

    rejections: Counter = Counter()
    accepted: List[Dict[str, Any]] = []

    base_rows: List[Dict[str, Any]] = []
    if args.base is not None:
        if not args.base.exists():
            print(f"base corpus missing: {args.base}", file=sys.stderr)
            return 2
        base_rows = _load_jsonl(args.base)
        print(f"  {len(base_rows)} rows read from base corpus {args.base.name}")

    # Base rows first so dedupe keeps them over a conflicting correction.
    for row in base_rows + approved:
        ok, why = validate_row(row)
        if not ok:
            rejections[why.split(":")[0][:60]] += 1
            continue
        accepted.append(normalise_row(row, TUNED_SYSTEM_PROMPT))

    accepted, duplicates = dedupe(accepted)

    if rejections:
        print("  dropped rows by reason:")
        for reason, count in rejections.most_common():
            print(f"    {count:5d}  {reason}")
    if duplicates:
        print(f"  {duplicates} duplicate prompts dropped")

    if len(accepted) < args.min_examples:
        print(
            f"only {len(accepted)} usable rows, below --min-examples "
            f"{args.min_examples}. Not writing a dataset: a candidate trained on "
            "this many rows cannot be evaluated against the incumbent with "
            "enough power to tell improvement from noise.",
            file=sys.stderr,
        )
        return 3

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as handle:
        for row in accepted:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    report = {
        "approved_examples": len(approved),
        "base_rows": len(base_rows),
        "usable_rows": len(accepted),
        "duplicates_dropped": duplicates,
        "rejections": dict(rejections),
        "corrections_only": args.base is None,
        "system_prompt_sha": hashlib.sha256(
            TUNED_SYSTEM_PROMPT.encode("utf-8")
        ).hexdigest()[:16],
    }
    report_path = args.out.parent / "dataset_report.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(f"  wrote {len(accepted)} rows to {args.out}")
    print(f"  report written to {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
