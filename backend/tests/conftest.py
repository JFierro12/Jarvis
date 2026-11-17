# ruff: noqa: E402 - imports deliberately follow the isolation block
# below: it must run before anything reads configuration, which is the
# entire point. Hoisting these imports would silently undo it.
"""Test fixtures.

Isolation happens at import time, before anything reads configuration.
Two separate leaks have to be closed, not one: the dotenv file *and* the
process environment. Pinning only `Settings(...)` kwargs was not enough —
`app.memory.database` builds its engine from settings at import, so the
suite was bound to the real ./jarvis.db and `lifespan`'s `init_db()` ran
against it. It survived only because `create_all` is idempotent.
"""

import os
import socket
import subprocess
import sys
import tempfile

# Must precede every app import below.
_TEST_HOME = tempfile.mkdtemp(prefix="jarvis-tests-")
_ALLOWED = os.path.join(_TEST_HOME, "files")
os.makedirs(_ALLOWED, exist_ok=True)

# 1. Neutralize the developer's shell environment.
for _key in [k for k in os.environ if k.startswith("JARVIS_")]:
    del os.environ[_key]

# 2. Neutralize the dotenv file by pointing at one that cannot exist.
os.environ["JARVIS_ENV_FILE"] = os.path.join(_TEST_HOME, "absent.env")

# 3. Pin everything the suite depends on, so no test can reach a real
#    database, model download, paid API, screen capture, or user folder.
os.environ.update(
    {
        "JARVIS_DATABASE_URL": f"sqlite:///{_TEST_HOME}/test.db".replace("\\", "/"),
        "JARVIS_AUTH_TOKENS": "dev-local-token",
        "JARVIS_APPROVAL_KEY": "test-approval-key",
        "JARVIS_REASONING_PROVIDER": "mock",
        "JARVIS_VISION_PROVIDER": "mock",
        "JARVIS_TTS_PROVIDER": "mock",
        "JARVIS_STT_PROVIDER": "mock",
        "JARVIS_GRANTED_PERMISSIONS": "",
        "JARVIS_ALLOWED_FOLDERS": _ALLOWED,
        "JARVIS_POLICY_REVISION": "test",
        "JARVIS_RUNTIME_DATABASE": os.path.join(_TEST_HOME, "runtime.db"),
    }
)

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.memory.database as database_module
from app.core.config import Settings, get_settings
from app.main import app
from app.memory.models import Base
from app.services.learning import reset_learning_store_for_tests
from app.services.location import reset_location_store_for_tests
from app.services.news import reset_news_service_for_tests
from app.services.training_jobs import reset_job_runner_for_tests
from app.services.weather import reset_weather_service_for_tests
from app.tools.pending_actions import reset_pending_action_store

AUTH_HEADERS = {"Authorization": "Bearer dev-local-token"}
# The approval path requires a credential the API token does not carry.
APPROVAL_HEADERS = {**AUTH_HEADERS, "X-Jarvis-Approval-Key": "test-approval-key"}


@pytest.fixture(autouse=True)
def prohibit_external_effects(monkeypatch):
    from PIL import ImageGrab

    from app.services.local_speech import LocalWhisperTranscriber

    def blocked(*args, **kwargs):
        raise AssertionError("External effect attempted in an isolated test")

    original = subprocess.Popen

    def guarded_process(argv, *args, **kwargs):
        # The configuration-isolation test runs a fresh Python interpreter.
        if not isinstance(argv, list) or len(argv) < 2 or argv[:2] != [sys.executable, "-c"]:
            return blocked()
        return original(argv, *args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", guarded_process)
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(ImageGrab, "grab", blocked)
    monkeypatch.setattr(LocalWhisperTranscriber, "_ensure_model", blocked)


def _build_client(granted_permissions: str):
    """Fresh in-memory SQLite per test so tests never share state."""
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    test_session_local = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def override_get_db_session():
        db = test_session_local()
        try:
            yield db
        finally:
            db.close()

    def override_get_settings():
        # Tests must be hermetic regardless of whatever the developer's
        # local .env currently has configured — force every provider back
        # to "mock" so the suite never makes a real network call to
        # Anthropic/ElevenLabs/etc. Real-provider request construction and
        # response parsing are covered separately by the
        # test_anthropic_providers.py / test_elevenlabs_speech.py style
        # tests, which inject a fake client directly.
        # granted_permissions is pinned explicitly for the same reason:
        # it is read from .env otherwise, so whether a capability test
        # passed would depend on the developer's local configuration.
        return Settings(
            auth_tokens="dev-local-token",
            reasoning_provider="mock",
            vision_provider="mock",
            tts_provider="mock",
            granted_permissions=granted_permissions,
            approval_key="test-approval-key",
        )

    # Fresh action store per test: approvals must never leak between tests.
    reset_pending_action_store()
    # Weather/news/location hold process-wide state on purpose (caches that
    # must outlive a request, and a device fix that must outlive the turn that
    # reported it). That is exactly what leaks between tests, so each test
    # starts from a clean one.
    reset_weather_service_for_tests()
    reset_news_service_for_tests()
    reset_location_store_for_tests()
    reset_learning_store_for_tests()
    reset_job_runner_for_tests()

    app.dependency_overrides[database_module.get_db_session] = override_get_db_session
    app.dependency_overrides[get_settings] = override_get_settings

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()


@pytest.fixture()
def client():
    """Default: this installation grants no capabilities (fail closed)."""
    yield from _build_client("")


@pytest.fixture()
def granted_client():
    """An installation that has been configured to grant capabilities."""
    yield from _build_client("pc_agent,smart_home,os_control,file_access,screen_capture")
