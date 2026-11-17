"""The candidate-example queue: the "collect" and "validate" stages of the
self-training lifecycle.

The whole point of this module is to make a bad training set *hard to build*,
so the interesting parts are the refusals rather than the happy path.

Three rules are enforced structurally rather than by convention, because a
documented rule that lives only in a comment is one refactor away from being
gone:

1. **Nothing here reads the conversation store.** `LearningStore` is
   constructed with no database session and no memory repository, so there is
   no code path by which a private conversation, a recorded utterance, a
   location history row or a personal document can become a training example.
   Examples arrive only as an explicit `prompt`/`expected` pair submitted
   through the API. `test_learning.py` asserts the absence of those imports,
   because this is the rule most likely to be "helpfully" undone later.

2. **A model cannot get its own output into a dataset.** A
   `MODEL_GENERATED` example reaches `APPROVED` only when an independent
   verifier has checked it *and* that verifier's identity differs from the
   generator's. `approve()` raises `SelfApproval` when they match. "The same
   model confidently approved its own answer" is the specific failure this
   exists to prevent.

3. **Approval is never automatic.** Passing every gate moves an example to
   `AWAITING_REVIEW`, never to `APPROVED`. Validation can only ever *reject*.
   Promotion into a dataset requires a human call to `approve()`.

Feedback is treated as evidence, not ground truth: a thumbs-down becomes a
`PENDING_VALIDATION` candidate carrying the user's claim, and a human decides
whether the claim is correct. A user asserting that 2+2=5 does not teach the
model that 2+2=5.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from datetime import timezone as tz
from enum import Enum
from typing import Dict, List, Optional, Sequence, Tuple


class ExampleSource(str, Enum):
    """Where a candidate example came from. Determines which gates apply."""

    USER_CORRECTION = "user_correction"
    """The user said the assistant got something wrong and supplied the right
    answer. Evidence to investigate, not ground truth."""

    USER_RATING = "user_rating"
    """A thumbs up/down with no corrected answer. Useful as a signal of where
    to look; never directly trainable, because there is no target text."""

    CURATED_FIXTURE = "curated_fixture"
    """Written deliberately by a maintainer as a test or teaching case."""

    MODEL_GENERATED = "model_generated"
    """Synthesised. Requires independent verification by a different checker
    before it can be approved."""


class ReviewState(str, Enum):
    PENDING_VALIDATION = "pending_validation"
    REJECTED = "rejected"
    AWAITING_REVIEW = "awaiting_review"
    APPROVED = "approved"
    WITHDRAWN = "withdrawn"


class RejectReason(str, Enum):
    MALFORMED_JSON = "malformed_json"
    SCHEMA_VIOLATION = "schema_violation"
    UNKNOWN_TOOL = "unknown_tool"
    NO_TARGET_TEXT = "no_target_text"
    TOO_LONG = "too_long"
    EMPTY = "empty"
    DUPLICATE = "duplicate"
    POSSIBLE_SECRET = "possible_secret"
    POSSIBLE_PRIVATE_DATA = "possible_private_data"
    TEACHES_REFUSAL = "teaches_refusal"
    AUTHORIZES_FROM_CONTENT = "authorizes_from_content"


class LearningError(Exception):
    """Base for queue errors."""


class SelfApproval(LearningError):
    """Raised when a verifier tries to approve examples it generated itself."""


class NotFound(LearningError):
    pass


class InvalidTransition(LearningError):
    pass


# ---------------------------------------------------------------------------
# Content gates
# ---------------------------------------------------------------------------

# Credential-shaped strings. Deliberately broad: a false positive costs one
# rejected example, a false negative bakes a secret into model weights where
# it cannot be deleted.
_SECRET_PATTERNS: Sequence[Tuple[str, re.Pattern]] = (
    ("anthropic key", re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}")),
    ("openai key", re.compile(r"\bsk-[A-Za-z0-9]{20,}")),
    ("elevenlabs key", re.compile(r"\bel_[A-Za-z0-9]{24,}")),
    ("aws access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("github token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{16,}")),
    ("google api key", re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}")),
    ("slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}")),
    ("bearer token", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{16,}")),
    ("private key block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}")),
    ("assigned password", re.compile(r"(?i)\b(?:password|passwd|secret|api[_ -]?key)\b\s*[:=]\s*\S{6,}")),
)

# Personal data that should not enter a training corpus by default. These are
# heuristics on purpose -- the gate is a backstop, not the primary control.
# The primary control is that nothing auto-harvests the conversation store.
_PRIVATE_PATTERNS: Sequence[Tuple[str, re.Pattern]] = (
    ("email address", re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")),
    ("phone number", re.compile(r"(?<!\d)(?:\+?\d{1,2}[ .\-])?\(?\d{3}\)?[ .\-]\d{3}[ .\-]\d{4}(?!\d)")),
    ("payment card", re.compile(r"(?<!\d)(?:\d[ \-]?){13,19}(?!\d)")),
    ("us ssn", re.compile(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)")),
    ("precise coordinates", re.compile(r"-?\d{1,3}\.\d{4,}\s*,\s*-?\d{1,3}\.\d{4,}")),
    ("windows user path", re.compile(r"(?i)[A-Z]:\\Users\\[^\\\s\"']+\\")),
    ("unix home path", re.compile(r"/(?:home|Users)/[A-Za-z0-9._\-]+/")),
)

# Phrasings that teach the exact failure mode the brief calls out: equating
# "I have no tool for this" with "I cannot answer". A tuned model that learns
# this becomes less useful than its own base model at ordinary questions.
_REFUSAL_PATTERNS: Sequence[re.Pattern] = (
    re.compile(r"(?i)\b(?:i|I)\s+(?:do not|don't|cannot|can't)\s+have\s+(?:a\s+)?"
               r"(?:tool|capability|function|ability)\b[^.]{0,60}\bso\b"),
    re.compile(r"(?i)\bno\s+(?:available\s+)?tool\b[^.]{0,40}\b(?:cannot|can't|unable)\b"),
    re.compile(r"(?i)\bunable to (?:answer|help|assist)\b[^.]{0,40}\b(?:tool|capability|access)\b"),
    re.compile(r"(?i)\bi'?m\s+(?:just|only)\s+an?\s+(?:ai|assistant|language model)\b"),
)

# Content that tries to make retrieved text authorize an action. An example
# whose *expected answer* obeys an instruction embedded in quoted material is
# teaching prompt-injection compliance.
_INJECTION_PATTERNS: Sequence[re.Pattern] = (
    re.compile(r"(?i)\bignore (?:all )?(?:previous|prior|above) instructions\b"),
    re.compile(r"(?i)\bdisregard (?:your|all|the) (?:policy|rules|instructions)\b"),
    re.compile(r"(?i)\byou are now (?:in )?(?:developer|admin|god) mode\b"),
    re.compile(r"(?i)\bwithout (?:asking|confirmation|approval)\b.{0,30}\bexecute\b"),
)

MAX_PROMPT_CHARS = 2000
MAX_EXPECTED_CHARS = 4000


def scan_for_secrets(text: str) -> List[str]:
    """Names of credential patterns found in `text`."""
    return [name for name, pattern in _SECRET_PATTERNS if pattern.search(text)]


def scan_for_private_data(text: str) -> List[str]:
    """Names of personal-data patterns found in `text`."""
    return [name for name, pattern in _PRIVATE_PATTERNS if pattern.search(text)]


def teaches_refusal(expected: str) -> bool:
    """True when the expected answer equates missing tools with inability.

    Checked against the *expected answer* only. A user is free to complain
    "you said you couldn't answer" in a prompt; what must not happen is that
    phrasing becoming a training target.
    """
    return any(pattern.search(expected) for pattern in _REFUSAL_PATTERNS)


def looks_like_injection(text: str) -> bool:
    return any(pattern.search(text) for pattern in _INJECTION_PATTERNS)


# ---------------------------------------------------------------------------
# Contract conformance
# ---------------------------------------------------------------------------

def _known_tool_names() -> frozenset:
    """Tool names from the live registry.

    Imported lazily and by name so this module stays importable in contexts
    where the registry's dependencies are not installed -- and so the set is
    never a hand-maintained copy that could drift from what the policy engine
    actually accepts.
    """
    from app.tools.registry import TOOL_DEFINITIONS

    return frozenset(TOOL_DEFINITIONS)


def validate_expected_response(expected: str) -> Tuple[Optional[RejectReason], str]:
    """Check an expected answer against the response contract.

    Returns `(None, "")` when the text conforms. Training rows must be exactly
    the shape the application parses at runtime; a row that is merely
    *plausible* JSON teaches a format the policy engine will reject.
    """
    text = expected.strip()
    if not text:
        return RejectReason.EMPTY, "expected answer is blank"

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        return RejectReason.MALFORMED_JSON, f"not valid JSON: {exc.msg}"

    if not isinstance(payload, dict):
        return RejectReason.SCHEMA_VIOLATION, "top level must be a JSON object"

    required = {"spoken_answer", "requires_confirmation", "proposed_tool_call"}
    missing = required - set(payload)
    if missing:
        return RejectReason.SCHEMA_VIOLATION, f"missing {sorted(missing)}"
    extra = set(payload) - required
    if extra:
        return RejectReason.SCHEMA_VIOLATION, f"unexpected keys {sorted(extra)}"

    if not isinstance(payload["spoken_answer"], str):
        return RejectReason.SCHEMA_VIOLATION, "spoken_answer must be a string"
    if not isinstance(payload["requires_confirmation"], bool):
        return RejectReason.SCHEMA_VIOLATION, "requires_confirmation must be a boolean"

    call = payload["proposed_tool_call"]
    if call is None:
        return None, ""
    if not isinstance(call, dict):
        return RejectReason.SCHEMA_VIOLATION, "proposed_tool_call must be an object or null"

    call_required = {"tool_name", "target", "content", "arguments"}
    call_missing = call_required - set(call)
    if call_missing:
        return RejectReason.SCHEMA_VIOLATION, f"tool call missing {sorted(call_missing)}"
    call_extra = set(call) - call_required
    if call_extra:
        return RejectReason.SCHEMA_VIOLATION, f"tool call has unexpected keys {sorted(call_extra)}"

    # `arguments` is schema-locked to empty in the live contract. A row that
    # populates it would teach the model to emit a free-form argument channel
    # the application does not accept and the policy engine cannot inspect.
    if call["arguments"] != {}:
        return RejectReason.SCHEMA_VIOLATION, "arguments must be an empty object"

    name = call["tool_name"]
    if not isinstance(name, str) or name not in _known_tool_names():
        return RejectReason.UNKNOWN_TOOL, f"{name!r} is not in the tool registry"

    return None, ""


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------

def _utcnow() -> datetime:
    return datetime.now(tz.utc)


@dataclass(frozen=True)
class AuditEntry:
    """One immutable line in an example's history.

    Append-only: the queue never rewrites an audit entry, so "who approved
    this and on what evidence" survives later state changes.
    """

    at: datetime
    actor: str
    action: str
    detail: str = ""

    def as_dict(self) -> Dict[str, str]:
        return {
            "at": self.at.isoformat(),
            "actor": self.actor,
            "action": self.action,
            "detail": self.detail,
        }


@dataclass
class CandidateExample:
    prompt: str
    expected: str
    source: ExampleSource
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    state: ReviewState = ReviewState.PENDING_VALIDATION
    created_at: datetime = field(default_factory=_utcnow)
    reject_reason: Optional[RejectReason] = None
    reject_detail: str = ""
    note: str = ""
    generator_id: str = ""
    verifier_id: str = ""
    verified_at: Optional[datetime] = None
    approved_by: str = ""
    audit: List[AuditEntry] = field(default_factory=list)

    @property
    def dedup_key(self) -> str:
        """Prompt-only hash.

        Two different expected answers for the same prompt are a conflict to
        resolve, not two examples to train on -- so the key deliberately
        ignores the answer.
        """
        normalized = re.sub(r"\s+", " ", self.prompt.strip().lower())
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    @property
    def is_trainable(self) -> bool:
        return self.state is ReviewState.APPROVED

    def as_dict(self) -> Dict[str, object]:
        return {
            "id": self.id,
            "prompt": self.prompt,
            "expected": self.expected,
            "source": self.source.value,
            "state": self.state.value,
            "created_at": self.created_at.isoformat(),
            "reject_reason": self.reject_reason.value if self.reject_reason else None,
            "reject_detail": self.reject_detail,
            "note": self.note,
            "generator_id": self.generator_id,
            "verifier_id": self.verifier_id,
            "verified_at": self.verified_at.isoformat() if self.verified_at else None,
            "approved_by": self.approved_by,
            "audit": [entry.as_dict() for entry in self.audit],
        }


# ---------------------------------------------------------------------------
# The queue
# ---------------------------------------------------------------------------

class LearningStore:
    """In-memory candidate queue.

    Note the constructor signature: no session, no engine, no repository. The
    store cannot reach the conversation database even if a future caller wants
    it to, which is the structural form of "do not train on private
    conversations by default".

    In-memory is a deliberate default too. A queue that survives restarts is a
    second durable store of user utterances, and this project does not keep
    one without being asked. `export_approved()` writes the reviewed subset to
    disk when a dataset is actually being built.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._examples: Dict[str, CandidateExample] = {}

    # -- collect ----------------------------------------------------------

    def submit(
        self,
        prompt: str,
        expected: str,
        source: ExampleSource,
        *,
        actor: str = "user",
        note: str = "",
        generator_id: str = "",
    ) -> CandidateExample:
        """Add a candidate and run the validation gates immediately.

        A passing example lands in `AWAITING_REVIEW`. Validation never
        approves anything; it can only reject.
        """
        example = CandidateExample(
            prompt=prompt,
            expected=expected,
            source=source,
            note=note,
            generator_id=generator_id,
        )
        example.audit.append(AuditEntry(_utcnow(), actor, "submitted", source.value))

        with self._lock:
            self._validate_locked(example)
            self._examples[example.id] = example
        return example

    def _validate_locked(self, example: CandidateExample) -> None:
        reason, detail = self._evaluate_gates_locked(example)
        if reason is not None:
            example.state = ReviewState.REJECTED
            example.reject_reason = reason
            example.reject_detail = detail
            example.audit.append(
                AuditEntry(_utcnow(), "validator", "rejected", f"{reason.value}: {detail}")
            )
            return
        example.state = ReviewState.AWAITING_REVIEW
        example.audit.append(
            AuditEntry(_utcnow(), "validator", "passed_gates", "awaiting human review")
        )

    def _evaluate_gates_locked(
        self, example: CandidateExample
    ) -> Tuple[Optional[RejectReason], str]:
        prompt, expected = example.prompt.strip(), example.expected.strip()

        if not prompt:
            return RejectReason.EMPTY, "prompt is blank"

        # A rating carries no target text, so there is nothing to train on.
        # It is still recorded -- it says where to look -- but it is not a
        # trainable example and is not allowed to pretend to be one.
        if example.source is ExampleSource.USER_RATING:
            return RejectReason.NO_TARGET_TEXT, (
                "a rating records where to investigate; it is not a training target"
            )

        if len(prompt) > MAX_PROMPT_CHARS:
            return RejectReason.TOO_LONG, f"prompt is {len(prompt)} chars"
        if len(expected) > MAX_EXPECTED_CHARS:
            return RejectReason.TOO_LONG, f"expected answer is {len(expected)} chars"

        combined = f"{prompt}\n{expected}"

        found_secrets = scan_for_secrets(combined)
        if found_secrets:
            # The detail names the *pattern*, never the matched text: a
            # rejection report must not become the place the secret is written
            # down in plaintext.
            return RejectReason.POSSIBLE_SECRET, f"matched {', '.join(found_secrets)}"

        found_private = scan_for_private_data(combined)
        if found_private:
            return RejectReason.POSSIBLE_PRIVATE_DATA, f"matched {', '.join(found_private)}"

        reason, detail = validate_expected_response(expected)
        if reason is not None:
            return reason, detail

        if teaches_refusal(expected):
            return RejectReason.TEACHES_REFUSAL, (
                "expected answer equates having no tool with being unable to answer"
            )

        if looks_like_injection(expected):
            return RejectReason.AUTHORIZES_FROM_CONTENT, (
                "expected answer complies with an injected instruction"
            )

        key = example.dedup_key
        for other in self._examples.values():
            if other.id == example.id:
                continue
            if other.state in (ReviewState.REJECTED, ReviewState.WITHDRAWN):
                continue
            if other.dedup_key == key:
                return RejectReason.DUPLICATE, f"same prompt as {other.id}"

        return None, ""

    # -- review -----------------------------------------------------------

    def record_verification(
        self, example_id: str, verifier_id: str, *, passed: bool, detail: str = ""
    ) -> CandidateExample:
        """Record an independent check of a model-generated example.

        `verifier_id` identifies *what* did the checking. It is compared
        against `generator_id` at approval time, which is the point of
        recording it at all.
        """
        with self._lock:
            example = self._require(example_id)
            if not verifier_id:
                raise InvalidTransition("a verification must name its verifier")
            if passed:
                example.verifier_id = verifier_id
                example.verified_at = _utcnow()
                example.audit.append(
                    AuditEntry(_utcnow(), verifier_id, "verified", detail)
                )
            else:
                example.state = ReviewState.REJECTED
                example.reject_reason = RejectReason.SCHEMA_VIOLATION
                example.reject_detail = detail or "failed independent verification"
                example.audit.append(
                    AuditEntry(_utcnow(), verifier_id, "verification_failed", detail)
                )
            return example

    def approve(self, example_id: str, approver: str) -> CandidateExample:
        """Mark an example trainable. Human action, never automatic.

        A `MODEL_GENERATED` example additionally needs an independent
        verification whose verifier differs from its generator. "The model
        approved its own output" is rejected here rather than warned about.
        """
        with self._lock:
            example = self._require(example_id)
            if not approver:
                raise InvalidTransition("an approval must name its approver")
            if example.state is not ReviewState.AWAITING_REVIEW:
                raise InvalidTransition(
                    f"cannot approve an example in state {example.state.value}"
                )

            if example.source is ExampleSource.MODEL_GENERATED:
                if not example.verifier_id:
                    raise InvalidTransition(
                        "a model-generated example needs an independent check before approval"
                    )
                if example.verifier_id == example.generator_id:
                    raise SelfApproval(
                        f"{example.verifier_id!r} generated this example and cannot also "
                        "be the independent check on it"
                    )
                if approver == example.generator_id:
                    raise SelfApproval(
                        f"{approver!r} generated this example and cannot approve it"
                    )

            example.state = ReviewState.APPROVED
            example.approved_by = approver
            example.audit.append(AuditEntry(_utcnow(), approver, "approved"))
            return example

    def reject(self, example_id: str, actor: str, detail: str = "") -> CandidateExample:
        with self._lock:
            example = self._require(example_id)
            example.state = ReviewState.REJECTED
            example.reject_detail = detail or "rejected on review"
            example.audit.append(AuditEntry(_utcnow(), actor, "rejected", detail))
            return example

    def withdraw(self, example_id: str, actor: str) -> CandidateExample:
        """Remove an approved example from future datasets.

        Kept rather than deleted so the audit trail of what was once approved
        survives, but `is_trainable` goes false immediately.
        """
        with self._lock:
            example = self._require(example_id)
            example.state = ReviewState.WITHDRAWN
            example.audit.append(AuditEntry(_utcnow(), actor, "withdrawn"))
            return example

    def _require(self, example_id: str) -> CandidateExample:
        example = self._examples.get(example_id)
        if example is None:
            raise NotFound(example_id)
        return example

    # -- read -------------------------------------------------------------

    def get(self, example_id: str) -> CandidateExample:
        with self._lock:
            return self._require(example_id)

    def list(
        self,
        *,
        state: Optional[ReviewState] = None,
        source: Optional[ExampleSource] = None,
        limit: int = 100,
    ) -> List[CandidateExample]:
        with self._lock:
            items = [
                example
                for example in self._examples.values()
                if (state is None or example.state is state)
                and (source is None or example.source is source)
            ]
        items.sort(key=lambda e: e.created_at, reverse=True)
        return items[:limit]

    def counts(self) -> Dict[str, int]:
        with self._lock:
            tally = {state.value: 0 for state in ReviewState}
            for example in self._examples.values():
                tally[example.state.value] += 1
            return tally

    def rejection_summary(self) -> Dict[str, int]:
        """Rejections by reason -- the useful diagnostic when a candidate run
        produces far fewer rows than expected."""
        with self._lock:
            tally: Dict[str, int] = {}
            for example in self._examples.values():
                if example.reject_reason is not None:
                    key = example.reject_reason.value
                    tally[key] = tally.get(key, 0) + 1
            return tally

    def approved(self) -> List[CandidateExample]:
        with self._lock:
            items = [e for e in self._examples.values() if e.is_trainable]
        items.sort(key=lambda e: e.created_at)
        return items

    # -- build ------------------------------------------------------------

    def export_approved(self, system_prompt: str) -> List[Dict[str, object]]:
        """Render approved examples as chat rows for the trainer.

        Only `APPROVED` rows are emitted. A withdrawn or awaiting-review
        example cannot reach a dataset through this function, which is the
        only exporter.
        """
        rows = []
        for example in self.approved():
            rows.append(
                {
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": example.prompt},
                        {"role": "assistant", "content": example.expected},
                    ],
                    "meta": {
                        "example_id": example.id,
                        "source": example.source.value,
                        "approved_by": example.approved_by,
                    },
                }
            )
        return rows

    def clear(self) -> None:
        with self._lock:
            self._examples.clear()


_store: Optional[LearningStore] = None
_store_lock = threading.Lock()


def get_learning_store() -> LearningStore:
    global _store
    with _store_lock:
        if _store is None:
            _store = LearningStore()
        return _store


def reset_learning_store_for_tests() -> None:
    global _store
    with _store_lock:
        _store = None


__all__ = [
    "AuditEntry",
    "CandidateExample",
    "ExampleSource",
    "InvalidTransition",
    "LearningError",
    "LearningStore",
    "MAX_EXPECTED_CHARS",
    "MAX_PROMPT_CHARS",
    "NotFound",
    "RejectReason",
    "ReviewState",
    "SelfApproval",
    "get_learning_store",
    "looks_like_injection",
    "reset_learning_store_for_tests",
    "scan_for_private_data",
    "scan_for_secrets",
    "teaches_refusal",
    "validate_expected_response",
]
