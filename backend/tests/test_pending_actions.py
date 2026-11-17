"""Unit coverage for the canonical action and its approval lifecycle.

The serialization tests below use inputs that genuinely reproduce the old
defect. The previous delimiter test did not: it compared
``("a","b","c")`` with ``("a\\x1fb","","c")``, which serialize differently
even under the broken scheme, so it passed while proving nothing.
"""

import threading

import pytest

from app.tools.pending_actions import (
    ActionState,
    PendingActionError,
    PendingActionStore,
    normalize_action,
)


def act(**overrides):
    base = dict(
        tool_name="write_file",
        target="notes.txt",
        content="hello",
        arguments={},
        principal="user-1",
        session_id="sess-1",
        origin_device="desktop",
        target_device="desktop",
        policy_revision="v1",
    )
    base.update(overrides)
    return normalize_action(**base)


# --- Serialization: the defect the brief reproduced ---


def test_separator_byte_in_a_value_cannot_merge_fields():
    """The exact tuples that collided under delimiter joining."""
    a = act(target="notes.txt\x1fx", content="hello")
    b = act(target="notes.txt", content="x\x1fhello")
    assert a.fingerprint() != b.fingerprint()


@pytest.mark.parametrize(
    "left,right",
    [
        (act(target='a",“b'), act(target='a",“b-different')),
        (act(target="a\\x1fb"), act(target="a\x1fb")),  # literal backslash vs. real control byte
        (act(target="", content="ab"), act(target="a", content="b")),
        (act(target='{"x":1}'), act(target='{"x":2}')),
        (act(arguments={"a": "b"}), act(arguments={"ab": ""})),
        (act(arguments={"a": "b=c"}), act(arguments={"a=b": "c"})),
    ],
)
def test_adjacent_field_values_stay_distinct(left, right):
    assert left.fingerprint() != right.fingerprint()


def test_fingerprint_is_stable_and_order_independent():
    assert act(arguments={"a": "1", "b": "2"}).fingerprint() == act(arguments={"b": "2", "a": "1"}).fingerprint()


@pytest.mark.parametrize(
    "field,value",
    [
        ("tool_name", "delete_file"),
        ("target", "taxes.xlsx"),
        ("content", "goodbye"),
        ("arguments", {"state": "off"}),
        ("principal", "user-2"),
        ("session_id", "sess-2"),
        ("origin_device", "iphone"),
        ("target_device", "other-pc"),
        ("policy_revision", "v2"),
    ],
)
def test_every_bound_field_changes_the_fingerprint(field, value):
    assert act().fingerprint() != act(**{field: value}).fingerprint()


def test_target_case_and_whitespace_are_not_conflated():
    """Folding these together would let one approval cover a different path."""
    assert act(target="Notes.txt").fingerprint() != act(target="notes.txt").fingerprint()
    assert act(target="notes.txt ").fingerprint() != act(target="notes.txt").fingerprint()


# --- Lifecycle ---


def test_proposal_is_pending_and_not_yet_executable():
    store = PendingActionStore()
    record = store.propose(act())
    assert record.state is ActionState.PENDING
    with pytest.raises(PendingActionError, match="not approved"):
        store.begin_execution(record.action_id, "user-1")


def test_approved_action_executes_once_then_reports_already_executed():
    store = PendingActionStore()
    record = store.propose(act())
    store.approve(record.action_id, approved_by="trusted-ui")

    _, already = store.begin_execution(record.action_id, "user-1")
    assert already is False

    # A retry after an uncertain result must not repeat the side effect.
    second, already_again = store.begin_execution(record.action_id, "user-1")
    assert already_again is True
    assert second.execution_id is not None


def test_execution_is_refused_for_a_different_principal():
    store = PendingActionStore()
    record = store.propose(act(principal="user-1"))
    store.approve(record.action_id, approved_by="trusted-ui")
    with pytest.raises(PendingActionError, match="different principal"):
        store.begin_execution(record.action_id, "user-2")


def test_rejected_and_cancelled_actions_cannot_execute():
    store = PendingActionStore()
    rejected = store.propose(act())
    store.reject(rejected.action_id)
    with pytest.raises(PendingActionError):
        store.begin_execution(rejected.action_id, "user-1")

    cancelled = store.propose(act())
    store.approve(cancelled.action_id, approved_by="ui")
    store.cancel(cancelled.action_id)
    with pytest.raises(PendingActionError):
        store.begin_execution(cancelled.action_id, "user-1")


def test_expired_approval_cannot_execute():
    store = PendingActionStore(ttl_seconds=-1.0)
    record = store.propose(act())
    with pytest.raises(PendingActionError, match="expired"):
        store.approve(record.action_id, approved_by="ui")


def test_double_approval_is_refused():
    store = PendingActionStore()
    record = store.propose(act())
    store.approve(record.action_id, approved_by="ui")
    with pytest.raises(PendingActionError, match="not awaiting approval"):
        store.approve(record.action_id, approved_by="ui")


def test_logout_invalidates_that_principals_live_actions():
    store = PendingActionStore()
    mine = store.propose(act(principal="user-1"))
    store.approve(mine.action_id, approved_by="ui")
    theirs = store.propose(act(principal="user-2"))

    assert store.invalidate_for_principal("user-1") == 1
    with pytest.raises(PendingActionError):
        store.begin_execution(mine.action_id, "user-1")
    assert store.get(theirs.action_id).state is ActionState.PENDING


def test_policy_change_invalidates_approvals_decided_under_old_rules():
    store = PendingActionStore()
    record = store.propose(act(policy_revision="v1"))
    store.approve(record.action_id, approved_by="ui")
    assert store.invalidate_for_policy_revision("v2") == 1
    with pytest.raises(PendingActionError):
        store.begin_execution(record.action_id, "user-1")


def test_concurrent_execution_yields_exactly_one_winner():
    store = PendingActionStore()
    record = store.propose(act())
    store.approve(record.action_id, approved_by="ui")

    first_runs: list = []
    barrier = threading.Barrier(8)

    def attempt():
        barrier.wait()
        try:
            _, already = store.begin_execution(record.action_id, "user-1")
            if not already:
                first_runs.append(1)
        except PendingActionError:
            pass

    threads = [threading.Thread(target=attempt) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(first_runs) == 1, "exactly one caller may perform the side effect"


def test_storage_is_bounded_without_evicting_live_actions():
    store = PendingActionStore(max_actions=4)
    live = store.propose(act(target="keep.txt"))
    store.approve(live.action_id, approved_by="ui")
    for i in range(50):
        settled = store.propose(act(target=f"junk{i}.txt"))
        store.reject(settled.action_id)
    assert store.get(live.action_id).state is ActionState.APPROVED


def test_full_live_store_rejects_instead_of_growing():
    store = PendingActionStore(max_actions=2)
    store.propose(act())
    store.propose(act())
    with pytest.raises(PendingActionError, match="capacity"):
        store.propose(act())


def test_consumed_result_is_not_disclosed_to_other_principal():
    store = PendingActionStore()
    record = store.propose(act())
    store.approve(record.action_id, "ui")
    store.begin_execution(record.action_id, "user-1")
    store.record_result(record.action_id, "private result", False)
    with pytest.raises(PendingActionError, match="principal"):
        store.begin_execution(record.action_id, "user-2")


def test_cancel_between_queue_and_dispatch_stops_execution():
    store = PendingActionStore()
    record = store.propose(act())
    store.approve(record.action_id, "ui")
    store.begin_execution(record.action_id, "user-1")
    store.cancel(record.action_id)
    with pytest.raises(PendingActionError, match="cancelled"):
        store.mark_started(record.action_id)


def test_clock_injection_expiry_and_immutable_arguments():
    now = [10.0]
    store = PendingActionStore(clock=lambda: now[0])
    record = store.propose(act(arguments={"a": "b"}))
    with pytest.raises(TypeError):
        record.action.arguments["a"] = "changed"
    now[0] = 131
    with pytest.raises(PendingActionError, match="expired"):
        store.approve(record.action_id, "ui")


@pytest.mark.parametrize("invalidation,value", [
    ("invalidate_for_session", "sess-1"),
    ("invalidate_for_principal", "user-1"),
    ("invalidate_for_device", "desktop"),
    ("invalidate_for_policy_revision", "v2"),
])
def test_revocation_also_stops_queued_actions(invalidation, value):
    store = PendingActionStore()
    record = store.propose(act())
    store.approve(record.action_id, "ui")
    store.begin_execution(record.action_id, "user-1")
    getattr(store, invalidation)(value)
    with pytest.raises(PendingActionError, match="cancelled"):
        store.mark_started(record.action_id)


def test_expiry_while_queued_stops_dispatch():
    now = [10.0]
    store = PendingActionStore(clock=lambda: now[0])
    record = store.propose(act())
    store.approve(record.action_id, "ui")
    store.begin_execution(record.action_id, "user-1")
    now[0] = 131
    with pytest.raises(PendingActionError):
        store.mark_started(record.action_id)
    assert record.state is ActionState.EXPIRED
