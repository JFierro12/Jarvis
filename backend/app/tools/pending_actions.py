"""Server-owned pending actions with an explicit approval lifecycle.

Three defects in the previous version drove this rewrite:

1. The fingerprint joined fields with ``\\x1f`` and nothing stopped a value
   from containing that byte, so ``("write_file", "notes.txt\\x1fx",
   "hello")`` and ``("write_file", "notes.txt", "x\\x1fhello")`` produced an
   identical preimage. An encoding ambiguity, not a SHA-256 weakness.
2. ``propose`` handed back an immediately usable confirmation token, so one
   authenticated caller could propose and execute back to back. That proves
   the action wasn't swapped or replayed; it does not establish that a
   person approved anything.
3. Tokens bound only tool/target/content — not arguments, principal,
   session, device, or policy revision.

The fix for all three is the same shape: normalize once into a canonical
action, keep it server-side, and execute *that stored object*. Execution
takes an action id, never action fields, so there is nothing for a client
to resubmit differently.

Trust boundary, stated plainly: approval requires a credential the API
bearer token does not carry, which separates "something called the API"
from "the trusted UI said a person approved". It does **not** prove human
intent — a compromised trusted UI can approve on its own, and nothing here
detects that. Proving a human approved would need an independent channel
this process cannot self-certify.

Concurrency: state transitions are guarded by one lock and this store is
in-memory, so it is correct for the single-process desktop deployment this
release targets. Multi-worker operation would need shared transactional
state; it is not supported, and `app/main.py` must not be run with
multiple workers.
"""

import hashlib
import json
import secrets
import threading
import time
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Dict, Mapping, Optional, Tuple

# Long enough to hear a prompt and answer; short enough that an approval
# is an answer to a live question rather than a standing grant.
APPROVAL_TTL_SECONDS = 120.0

# Bounded so a caller cannot exhaust memory by proposing endlessly.
MAX_TRACKED_ACTIONS = 256


class ActionState(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    CONSUMED = "consumed"


class PendingActionError(Exception):
    """Raised when a transition is not legal for an action's current state."""


@dataclass(frozen=True)
class CanonicalAction:
    """The single validated object used for the displayed summary, policy
    evaluation, approval, and execution. Normalized once, at proposal."""

    tool_name: str
    target: str
    content: str
    arguments: Mapping[str, str]
    principal: str
    session_id: str
    origin_device: str
    target_device: str
    policy_revision: str

    def as_dict(self) -> dict:
        return {
            "tool_name": self.tool_name,
            "target": self.target,
            "content": self.content,
            "arguments": dict(self.arguments),
            "principal": self.principal,
            "session_id": self.session_id,
            "origin_device": self.origin_device,
            "target_device": self.target_device,
            "policy_revision": self.policy_revision,
        }

    def canonical_bytes(self) -> bytes:
        """Unambiguous, deterministic serialization.

        JSON rather than delimiter joining: field boundaries come from the
        structure, and a separator byte inside a value is escaped (``\\x1f``
        becomes ``\\u001f``) instead of being read as a boundary. Sorted keys
        and fixed separators keep it stable across runs.
        """
        return json.dumps(
            self.as_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode("utf-8")

    def fingerprint(self) -> str:
        return hashlib.sha256(self.canonical_bytes()).hexdigest()


def normalize_action(
    tool_name: str,
    target: str = "",
    content: str = "",
    arguments: Optional[Mapping[str, str]] = None,
    principal: str = "",
    session_id: str = "",
    origin_device: str = "",
    target_device: str = "",
    policy_revision: str = "",
) -> CanonicalAction:
    """Normalize exactly once; the result is what gets stored and executed.

    Only `tool_name` is case-folded, because tool names are registry
    identifiers. `target` and `content` are preserved byte-for-byte: a
    path differing only by trailing space or case is a *different* path,
    and quietly folding them together is precisely the conflation that
    makes one approval authorize a different action.
    """
    return CanonicalAction(
        tool_name=tool_name.strip().lower(),
        target=target,
        content=content,
        arguments=MappingProxyType(dict(arguments or {})),
        principal=principal,
        session_id=session_id,
        origin_device=origin_device,
        target_device=target_device,
        policy_revision=policy_revision,
    )


@dataclass
class PendingActionRecord:
    action_id: str
    action: CanonicalAction
    fingerprint: str
    state: ActionState
    created_at: float
    expires_at: float
    approved_by: Optional[str] = None
    execution_id: Optional[str] = None
    execution_result: Optional[str] = None
    execution_success: Optional[bool] = None
    started: bool = False

    def is_expired(self, now: float) -> bool:
        return now >= self.expires_at

    def can_invalidate(self) -> bool:
        return self.state in (ActionState.PENDING, ActionState.APPROVED) or (
            self.state is ActionState.CONSUMED and not self.started and self.execution_success is None
        )


class PendingActionStore:
    def __init__(self, ttl_seconds: float = APPROVAL_TTL_SECONDS, max_actions: int = MAX_TRACKED_ACTIONS,
                 clock=time.monotonic) -> None:
        self._ttl = ttl_seconds
        self._max = max_actions
        self._lock = threading.Lock()
        self._actions: Dict[str, PendingActionRecord] = {}
        self._clock = clock

    # -- internal helpers (callers already hold the lock) --

    def _expire_due(self, now: float) -> None:
        for record in self._actions.values():
            if record.can_invalidate() and record.is_expired(now):
                record.state = ActionState.EXPIRED

    # -- lifecycle --

    def propose(self, action: CanonicalAction) -> PendingActionRecord:
        now = self._clock()
        record = PendingActionRecord(
            action_id=secrets.token_urlsafe(24),
            action=action,
            fingerprint=action.fingerprint(),
            state=ActionState.PENDING,
            created_at=now,
            expires_at=now + self._ttl,
        )
        with self._lock:
            self._expire_due(now)
            # Eviction never authorizes replay: unknown IDs fail closed. Keep
            # running/uncertain executions and live approvals until resolved.
            if len(self._actions) >= self._max:
                settled = [r for r in self._actions.values() if r.state in (
                    ActionState.REJECTED, ActionState.CANCELLED, ActionState.EXPIRED
                ) or (r.state is ActionState.CONSUMED and r.execution_success is not None)]
                if not settled:
                    raise PendingActionError("Action capacity reached; finish or cancel pending actions")
                oldest = min(settled, key=lambda r: r.created_at)
                del self._actions[oldest.action_id]
            self._actions[record.action_id] = record
        return record

    def approve(self, action_id: str, approved_by: str) -> PendingActionRecord:
        """Only reachable from the dedicated approval path — never from the
        reasoning provider or the automatic executor."""
        with self._lock:
            record = self._require(action_id, self._clock())
            if record.state is not ActionState.PENDING:
                raise PendingActionError(f"Action is {record.state.value}, not awaiting approval")
            record.state = ActionState.APPROVED
            record.approved_by = approved_by
            return record

    def reject(self, action_id: str) -> PendingActionRecord:
        with self._lock:
            record = self._require(action_id, self._clock())
            if record.state is not ActionState.PENDING:
                raise PendingActionError(f"Action is {record.state.value}, not awaiting approval")
            record.state = ActionState.REJECTED
            return record

    def cancel(self, action_id: str) -> PendingActionRecord:
        """Cancellation invalidates an approval that hasn't been spent."""
        with self._lock:
            record = self._require(action_id, self._clock())
            if record.state is ActionState.CONSUMED and record.started:
                raise PendingActionError("Execution already started; it may not be interruptible")
            record.state = ActionState.CANCELLED
            return record

    def invalidate_for_principal(self, principal: str) -> int:
        """Logout/revocation: drop every live action for that principal."""
        count = 0
        with self._lock:
            for record in self._actions.values():
                if record.action.principal == principal and record.can_invalidate():
                    record.state = ActionState.CANCELLED
                    count += 1
        return count

    def invalidate_for_device(self, device: str):
        with self._lock:
            for record in self._actions.values():
                if device in (record.action.origin_device, record.action.target_device) and record.can_invalidate():
                    record.state = ActionState.CANCELLED

    def invalidate_for_session(self, session_id: str):
        with self._lock:
            for record in self._actions.values():
                if record.action.session_id == session_id and record.can_invalidate():
                    record.state = ActionState.CANCELLED

    def invalidate_for_policy_revision(self, current_revision: str) -> int:
        """A configuration/policy change invalidates approvals decided
        under the old rules."""
        count = 0
        with self._lock:
            for record in self._actions.values():
                if record.action.policy_revision != current_revision and record.can_invalidate():
                    record.state = ActionState.CANCELLED
                    count += 1
        return count

    def begin_execution(self, action_id: str, principal: str) -> Tuple[PendingActionRecord, bool]:
        """Atomically spend an approved action.

        Returns ``(record, already_executed)``. When ``already_executed`` is
        true the caller must return the recorded result rather than running
        the side effect again — this is what makes a retry after an
        uncertain outcome safe.
        """
        with self._lock:
            record = self._require(action_id, self._clock())

            if record.action.principal != principal:
                raise PendingActionError("Action belongs to a different principal")

            if record.state is ActionState.CONSUMED:
                return record, True

            if record.state is not ActionState.APPROVED:
                raise PendingActionError(f"Action is {record.state.value}, not approved")

            record.state = ActionState.CONSUMED
            record.execution_id = secrets.token_urlsafe(16)
            return record, False

    def mark_started(self, action_id: str):
        with self._lock:
            record = self._require(action_id, self._clock())
            if record.state is not ActionState.CONSUMED:
                raise PendingActionError("Execution cancelled before dispatch")
            record.started = True

    def record_result(self, action_id: str, result: str, success: bool = True) -> None:
        with self._lock:
            record = self._actions.get(action_id)
            if record is not None:
                record.execution_result = result
                record.execution_success = success

    def get(self, action_id: str) -> Optional[PendingActionRecord]:
        with self._lock:
            now = self._clock()
            self._expire_due(now)
            return self._actions.get(action_id)

    def _require(self, action_id: str, now: float) -> PendingActionRecord:
        self._expire_due(now)
        record = self._actions.get(action_id)
        if record is None:
            raise PendingActionError("Unknown action")
        return record


_store: Optional[PendingActionStore] = None
_store_lock = threading.Lock()


def get_pending_action_store() -> PendingActionStore:
    global _store
    with _store_lock:
        if _store is None:
            _store = PendingActionStore()
        return _store


def reset_pending_action_store(store: Optional[PendingActionStore] = None) -> PendingActionStore:
    """Injection point for tests, so each gets a fresh, isolated store."""
    global _store
    with _store_lock:
        _store = store or PendingActionStore()
        return _store
