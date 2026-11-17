"""Personal deployment identity and conversation store. No model has approval credentials.

SQLite persists conversations/devices; credentials are stored only as hashes. Sessions
and enrollment invitations expire. The native host is the local enrollment authority.
"""

import hashlib
import json
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


@dataclass(frozen=True)
class Identity:
    principal: str
    session_id: str
    device_id: str
    grants: frozenset
    admin: bool = False


class RuntimeStore:
    def __init__(self, path, clock=time.time):
        self.clock = clock
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS devices (
                id TEXT PRIMARY KEY, principal TEXT NOT NULL, name TEXT NOT NULL,
                grants TEXT NOT NULL, admin INTEGER NOT NULL, revoked INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY, id TEXT UNIQUE NOT NULL, device TEXT NOT NULL,
                approval TEXT NOT NULL, expires REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS invitations (
                code TEXT PRIMARY KEY, grants TEXT NOT NULL, expires REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY, principal TEXT NOT NULL, provider TEXT NOT NULL,
                model TEXT NOT NULL, prompt_version TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS turns (
                conversation TEXT NOT NULL, id TEXT NOT NULL, ordinal INTEGER NOT NULL,
                question TEXT NOT NULL, response TEXT, state TEXT NOT NULL,
                PRIMARY KEY (conversation,id));
            CREATE TABLE IF NOT EXISTS action_results (id TEXT PRIMARY KEY, output TEXT NOT NULL, success INTEGER NOT NULL);
        """)
        self.db.commit()
        # Process death must not permanently block a conversation or replay a turn.
        self.db.execute("UPDATE turns SET state='failed' WHERE state='running'")
        self.db.commit()

    def record_action_result(self, action_id, output, success):
        with self.lock, self.db:
            self.db.execute("INSERT OR REPLACE INTO action_results VALUES (?,?,?)", (action_id, output, int(success)))

    def action_result(self, action_id):
        with self.lock:
            row = self.db.execute("SELECT * FROM action_results WHERE id=?", (action_id,)).fetchone()
            return dict(row) if row else None

    def close(self):
        self.db.close()

    def _session(self, device):
        token, approval, sid = (secrets.token_urlsafe(32) for _ in range(3))
        self.db.execute("DELETE FROM sessions WHERE expires <= ?", (self.clock(),))
        self.db.execute(
            "INSERT INTO sessions VALUES (?,?,?,?,?)", (digest(token), sid, device, digest(approval), self.clock() + 3600)
        )
        return dict(
            access_token=token,
            approval_key=approval,
            session_id=sid,
            device_id=device,
            expires_in_seconds=3600,
            contract_version="2",
        )

    def bootstrap(self, device, grants):
        """Native host only; never exposed as an unauthenticated HTTP endpoint."""
        with self.lock, self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO devices VALUES (?,?,?,?,1,0)", (device, "owner", "This PC", json.dumps(sorted(grants)))
            )
            self.db.execute("DELETE FROM sessions WHERE device=?", (device,))
            return self._session(device)

    def identity(self, token):
        with self.lock:
            row = self.db.execute(
                """SELECT s.id AS sid,d.* FROM sessions s JOIN devices d ON d.id=s.device
                WHERE s.token=? AND s.expires>? AND d.revoked=0""",
                (digest(token), self.clock()),
            ).fetchone()
            if row is None:
                return None
            return Identity(row["principal"], row["sid"], row["id"], frozenset(json.loads(row["grants"])), bool(row["admin"]))

    def has_session(self, device):
        """Whether a live session exists for a device.

        The native host needs this to tell a page that was disconnected on
        purpose from one that has stopped responding: from outside, both
        simply stop making requests. Without the distinction, recovering a
        dead page would also undo an explicit Disconnect.
        """
        with self.lock:
            row = self.db.execute(
                "SELECT 1 FROM sessions WHERE device=? AND expires>?", (device, self.clock())
            ).fetchone()
            return row is not None

    def approval_valid(self, token, approval):
        with self.lock:
            row = self.db.execute("SELECT approval FROM sessions WHERE token=?", (digest(token),)).fetchone()
            return bool(self.identity(token) and row and secrets.compare_digest(row[0], digest(approval)))

    def invite(self, identity, grants):
        if not identity.admin or not set(grants).issubset(identity.grants):
            raise PermissionError("Enrollment requires a desktop administrator and scoped grants")
        with self.lock, self.db:
            self.db.execute("DELETE FROM invitations WHERE expires<=?", (self.clock(),))
            if self.db.execute("SELECT COUNT(*) FROM invitations").fetchone()[0] >= 16:
                raise ValueError("Too many pending enrollments")
            code = secrets.token_urlsafe(24)
            self.db.execute(
                "INSERT INTO invitations VALUES (?,?,?)", (digest(code), json.dumps(sorted(grants)), self.clock() + 120)
            )
            return code

    def enroll(self, code, name):
        with self.lock, self.db:
            row = self.db.execute("SELECT * FROM invitations WHERE code=? AND expires>?", (digest(code), self.clock())).fetchone()
            if row is None:
                raise PermissionError("Invalid or expired enrollment code")
            self.db.execute("DELETE FROM invitations WHERE code=?", (digest(code),))
            device = secrets.token_urlsafe(16)
            self.db.execute("INSERT INTO devices VALUES (?,?,?,?,0,0)", (device, "owner", name, row["grants"]))
            return self._session(device)

    def devices(self, identity):
        with self.lock:
            return [
                dict(r) for r in self.db.execute("SELECT id,name,revoked FROM devices WHERE principal=?", (identity.principal,))
            ]

    def device_active(self, device):
        with self.lock:
            row = self.db.execute("SELECT revoked FROM devices WHERE id=?", (device,)).fetchone()
            return row is not None and not row[0]

    def revoke(self, identity, device):
        if not identity.admin and identity.device_id != device:
            raise PermissionError("Only the desktop administrator may revoke another device")
        with self.lock, self.db:
            self.db.execute("UPDATE devices SET revoked=1 WHERE id=? AND principal=?", (device, identity.principal))
            self.db.execute("DELETE FROM sessions WHERE device=?", (device,))

    def logout(self, token):
        with self.lock, self.db:
            self.db.execute("DELETE FROM sessions WHERE token=?", (digest(token),))

    def conversation(self, identity, cid, provider, model, prompt_version):
        with self.lock, self.db:
            if not cid:
                cid = secrets.token_urlsafe(16)
                self.db.execute(
                    "INSERT INTO conversations VALUES (?,?,?,?,?)", (cid, identity.principal, provider, model, prompt_version)
                )
            row = self.db.execute("SELECT * FROM conversations WHERE id=? AND principal=?", (cid, identity.principal)).fetchone()
            if row is None:
                raise PermissionError("Conversation unavailable")
            return dict(row)

    def conversations(self, identity):
        with self.lock:
            return [
                dict(r)
                for r in self.db.execute(
                    "SELECT * FROM conversations WHERE principal=? ORDER BY rowid DESC LIMIT 100", (identity.principal,)
                )
            ]

    def history(self, cid):
        with self.lock:
            return [dict(r) for r in self.db.execute("SELECT * FROM turns WHERE conversation=? ORDER BY ordinal", (cid,))]

    def begin_turn(self, cid, turn_id, question):
        with self.lock, self.db:
            existing = self.db.execute("SELECT * FROM turns WHERE conversation=? AND id=?", (cid, turn_id)).fetchone()
            if existing:
                if existing["question"] != question:
                    raise ValueError("Turn ID already used for another question")
                return dict(existing)
            if self.db.execute("SELECT 1 FROM turns WHERE conversation=? AND state='running'", (cid,)).fetchone():
                raise ValueError("A turn is already running")
            ordinal = self.db.execute("SELECT COALESCE(MAX(ordinal),0)+1 FROM turns WHERE conversation=?", (cid,)).fetchone()[0]
            self.db.execute("INSERT INTO turns VALUES (?,?,?,?,NULL,'running')", (cid, turn_id, ordinal, question))
            return None

    def finish_turn(self, cid, turn_id, response, state="completed"):
        with self.lock, self.db:
            self.db.execute(
                "UPDATE turns SET response=?,state=? WHERE conversation=? AND id=?", (json.dumps(response), state, cid, turn_id)
            )
