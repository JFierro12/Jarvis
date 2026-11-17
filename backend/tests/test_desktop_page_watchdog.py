"""The host-side supervision of the desktop page.

Covers the two long-uptime failures the watchdog exists for — a session
that expires with no way to renew it, and a dead WebView2 renderer that
leaves the window painted white — and, more importantly, the case where
acting would be wrong: a page the user disconnected on purpose.

The policy is tested through `page_watchdog_action`, which is pure, so
none of this needs a window or a GUI toolkit.
"""

from app.core.runtime import RuntimeStore
from app.desktop_main import (
    PAGE_SILENCE_LIMIT_SECONDS,
    SESSION_RENEWAL_FRACTION,
    WATCHDOG_INTERVAL_SECONDS,
    page_watchdog_action,
)

SILENT = PAGE_SILENCE_LIMIT_SECONDS + 1


def test_silent_page_with_a_live_session_is_reloaded():
    """A renderer that died stops making requests but keeps its session."""
    assert page_watchdog_action(
        now=1000.0, last_response=1000.0 - SILENT, renew_at=float("inf"), has_session=True
    ) == "reload"


def test_disconnected_page_is_never_reloaded():
    """The security-relevant case.

    After Disconnect the page stops making requests, exactly like a dead
    one. Reloading it would re-inject a session the user had just
    dismissed, so the absence of a session has to veto recovery.
    """
    assert page_watchdog_action(
        now=1000.0, last_response=1000.0 - SILENT, renew_at=float("inf"), has_session=False
    ) is None


def test_silence_just_inside_the_limit_is_left_alone():
    assert page_watchdog_action(
        now=1000.0, last_response=1000.0 - PAGE_SILENCE_LIMIT_SECONDS,
        renew_at=float("inf"), has_session=True,
    ) is None


def test_session_is_renewed_once_the_deadline_passes():
    assert page_watchdog_action(
        now=1000.0, last_response=999.0, renew_at=1000.0, has_session=True
    ) == "renew"


def test_a_responsive_page_before_its_deadline_needs_nothing():
    assert page_watchdog_action(
        now=1000.0, last_response=999.0, renew_at=2000.0, has_session=True
    ) is None


def test_a_dead_renderer_is_reloaded_rather_than_renewed():
    """Renewal injects credentials with run_js, which a dead renderer
    cannot receive. Reload has to win, since it re-runs `loaded` and the
    load handler injects the session anyway."""
    assert page_watchdog_action(
        now=1000.0, last_response=1000.0 - SILENT, renew_at=0.0, has_session=True
    ) == "reload"


def test_reload_is_followed_by_its_own_injection():
    """`window.load_url` does not re-fire pywebview's `loaded` event
    (measured on 6.2.1 / Windows), so the reloaded page gets no credentials
    from the load handler. The watchdog has to inject them itself, or
    recovery swaps a white window for one asking to be paired."""
    assert page_watchdog_action(
        now=1000.0, last_response=999.0, renew_at=float("inf"),
        has_session=True, reinject_at=1000.0,
    ) == "reinject"


def test_a_pending_injection_outranks_another_reload():
    """Otherwise a page that was just reloaded could be reloaded again
    before it was ever given a session, and never recover."""
    assert page_watchdog_action(
        now=1000.0, last_response=1000.0 - SILENT, renew_at=0.0,
        has_session=True, reinject_at=1000.0,
    ) == "reinject"


def test_no_injection_is_pending_by_default():
    """A healthy page must not be re-bootstrapped on every tick."""
    assert page_watchdog_action(
        now=1000.0, last_response=999.0, renew_at=float("inf"), has_session=True
    ) is None


def test_renewal_lands_well_inside_the_session_lifetime(tmp_path):
    """The renewal deadline must leave room for several missed ticks.

    This is what the 30-hour failure came down to: a 3600-second session
    and nothing scheduled before it ran out.
    """
    store = RuntimeStore(str(tmp_path / "renewal.db"))
    try:
        lifetime = store.bootstrap("desktop", [])["expires_in_seconds"]
    finally:
        store.close()
    headroom = lifetime * (1 - SESSION_RENEWAL_FRACTION)
    # Many ticks of slack, so a missed one is not fatal...
    assert headroom > 10 * WATCHDOG_INTERVAL_SECONDS
    # ...and enough that a renderer dying at the renewal point can still be
    # noticed and reloaded before the session it holds runs out.
    assert headroom > PAGE_SILENCE_LIMIT_SECONDS + WATCHDOG_INTERVAL_SECONDS


def test_has_session_tracks_bootstrap_logout_and_expiry(tmp_path):
    now = [10.0]
    store = RuntimeStore(str(tmp_path / "sessions.db"), clock=lambda: now[0])
    try:
        credentials = store.bootstrap("desktop", [])
        assert store.has_session("desktop") is True
        assert store.has_session("phone") is False

        # Expiry and an explicit logout both clear it, which is what makes
        # it usable as the "was this on purpose?" signal.
        now[0] += credentials["expires_in_seconds"] + 1
        assert store.has_session("desktop") is False

        now[0] = 10.0
        store.bootstrap("desktop", [])
        fresh = store.bootstrap("desktop", [])
        store.logout(fresh["access_token"])
        assert store.has_session("desktop") is False
    finally:
        store.close()


def test_renewal_replaces_the_credentials_the_page_holds(tmp_path):
    """Renewal is a re-bootstrap, so the previous token must stop working —
    otherwise an hour's worth of tokens would accumulate as valid."""
    store = RuntimeStore(str(tmp_path / "rotate.db"))
    try:
        first = store.bootstrap("desktop", [])
        assert store.identity(first["access_token"]) is not None
        second = store.bootstrap("desktop", [])
        assert store.identity(second["access_token"]) is not None
        assert store.identity(first["access_token"]) is None
    finally:
        store.close()
