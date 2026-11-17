JARVIS follow-up implementation prompt

Use this alongside `docs/REVITALIZATION_MASTER_PROMPT.md`. It narrows the next implementation pass to completing Milestone 1 and delivering the first working increment of Milestone 2. Findings below come from source inspection; they are not a fresh certification of the running server or test suite.

---

Continue the JARVIS revitalization on the existing branch. Preserve the security improvements already made, finish their missing boundaries, then implement a small working shared-runtime path across desktop and iPhone. Do not restart the project or jump ahead to training infrastructure.

Read `AGENTS.md`, the master prompt, the current diff, and the actual tests first. The inspected branch is `Mark-VII`; its history already contains merge `f02190e` bringing in `f2bb7da`. Verify this rather than merging again. Preserve staged deletions, untracked implementation/test files, and unrelated edits. Do not reset, force-push, delete local databases, expose secrets, or launch real applications during automated tests.

Treat the previous completion report as a set of claims to check. Keep the strict application allowlist, `shell=False`, server-side permission configuration, untrusted history treatment, safe text rendering, and regression tests. Do not present the remaining work as undoing those improvements.

1. Finish confirmation and authorization semantics.

In `backend/app/tools/pending_actions.py`, tokens currently bind only tool name, target, and content. They do not bind arguments, principal, session, originating device, target device, or policy revision. Both tool routes accept the bearer token but do not use its identity when issuing/consuming pending actions. Installation-wide grants are an interim boundary, not per-device authorization.

Also, `/v1/tools/propose` immediately returns a usable confirmation token. Any authenticated API caller can propose and execute consecutively. This prevents guessing/replay and some action substitutions, but does not establish that a person approved the action. The existing test `test_issued_token_authorizes_exactly_once` demonstrates that immediate path.

Define and document the trusted approval boundary. Introduce server-owned pending actions with explicit pending, approved, rejected, expired, cancelled, and consumed states as appropriate. Proposal creates a pending action; only a dedicated authenticated user-interaction path can approve it. The reasoning provider and automatic tool executor must not have approval capability. Adding another endpoint behind the same unrestricted credential is not sufficient separation. Bind approval to the complete immutable action and authenticated session/device identity. Be candid that a compromised trusted UI cannot be proven to reflect human intent without an additional independent verification mechanism.

Use one canonical validated action object for the displayed summary, policy evaluation, approval, and execution. Include all arguments/content, target device, relevant configuration/policy revision, and resolved target identity where needed. Enforce parameter schemas and reject unsupported fields through a documented compatibility transition. Recheck grants and target state at execution. Voice “yes” must apply to the current pending action and expire; never let another conversation consume it. Cancellation, logout, revocation, and relevant configuration changes invalidate approval.

Keep consumption atomic and single-use, with bounded storage and rate limits. Specify restart and worker behavior: either use shared transactional state or explicitly enforce single-process operation for this release. Retrying after an uncertain execution result must not repeat a side effect; use execution IDs and observable status.

2. Fix fingerprint serialization and its regression test.

The current fingerprint joins strings with `\x1f`, but input strings are not prohibited from containing that character. These distinct tuples produce the same serialized preimage:

```python
("write_file", "notes.txt\x1fx", "hello")
("write_file", "notes.txt", "x\x1fhello")
```

This is an encoding ambiguity, not a cryptographic SHA-256 collision. The existing delimiter test compares inputs that do not reproduce it. Replace delimiter joining with unambiguous, deterministic serialization of the full validated action. Normalize once and execute that same normalized object. Do not conflate different path/content values through incidental trimming or casing. Add genuine boundary-collision, argument-change, content-change, principal/session/device-swap, concurrent-consumption, expiry, and revocation tests. Distinguish the proven serialization defect from any unproven end-to-end filesystem exploit.

3. Remove dashboard credential exposure before remote pairing.

`backend/app/api/dashboard.py` still puts an installation bearer token in HTML served without authentication. Loopback limits network reachability; it does not authenticate local callers. Replace this now rather than labeling it inherently safe on localhost.

Implement a secure desktop bootstrap with short-lived, scoped sessions. A one-time handoff through the native host may be suitable, provided it has a narrowly defined trusted interface and cannot be read by arbitrary web content. Do not move the same long-lived token into public JavaScript, localStorage, logs, or a persistent URL. Document the chosen trust boundary, credential lifetime, logout, and revocation.

Add host/origin validation and CSRF protection where the selected authentication mechanism requires it. Reject unexpected WebSocket origins and unauthorized session connections. Audit the native webview's navigation and microphone-permission behavior so remote content cannot inherit the trusted local application's privileges. Disable production debugging by default. Fail closed for insecure default credentials in live operation.

Keep loopback defaults. Do not bind publicly or add automatic port forwarding. Build phone pairing over an authenticated encrypted transport with explicit device enrollment and revocation. Remote routing must target the paired PC, never the backend host merely because it handles the request.

4. Enforce provenance beyond label validation.

Unknown-source rejection and untrusted history are useful improvements, but `validate_context_sources` still accepts client-provided `SYSTEM_POLICY`. A comment saying policy is constructed server-side does not enforce it.

Construct policy only from trusted server configuration. Reject client attempts to submit privileged source labels; migrate the current iOS context envelope deliberately because it currently includes system policy. Associate user requests with authenticated turns. Preserve provenance through history, summaries, retrieval, and tool results. Stored assistant text and quoted external text cannot grant permission. Keep provider prompts aligned, but do not treat keyword detection in a mock as proof that real models resist every injection. Test deterministic authorization independently of model behavior.

5. Make tests independent of real configuration and resources.

The revised `conftest.py` overrides several settings, but still constructs `Settings` with `.env` loading enabled and leaves settings such as STT and allowed folders unspecified. Other fixtures have narrower overrides. `app.memory.database` constructs its engine from settings at import time, and application lifespan calls `init_db()` on that engine even when the request database dependency is overridden.

Make settings, database engines, startup, pending-action storage, provider factories, and device executors injectable. Configure test isolation before imports/startup that consume settings. Disabling dotenv alone does not disable process environment variables; isolate both sources. Use temporary databases and folders, fake providers, a controlled clock, and fresh action stores. Ensure test startup cannot initialize the real database. Ensure no test downloads models, contacts a paid API, captures a screen, or starts a process without an explicit integration-test opt-in.

Verify isolation with hostile environment values and a disposable fake dotenv file, not by modifying the user's `.env`. Test both absent and present GPU contracts using controlled fixtures, with real hardware smoke tests separately labeled. Run each HTTP verification request exactly once and reuse its response; consuming a confirmation is itself a state-changing operation.

6. Close out Milestone 1 with evidence.

Run the relevant backend regression suite and lint checks, and report the exact commands, results, and limitations. Check desktop startup/login, spoken confirmation/cancellation, and negative authorization paths in a disposable environment. For unsafe operations, use fake executors and assert that no effect occurred. Update `docs/THREAT_MODEL.md` to distinguish fixed behavior, assumed trust, and unresolved limitations. Do not claim a policy engine protects against a fully compromised backend or that a voice-intent matcher prevents replayed approval audio.

Preserve the local database and generated metadata while reviewing their removal from version control. Verify that unrelated files are excluded from the proposed checkpoint. Prepare a concise commit summary and reviewable diff; commit only if the user has authorized commits in the implementation session. Commit permission must not block continued local implementation. Do not push or deploy without authorization.

7. Deliver the first usable Milestone 2 increment.

Use the existing monorepo and native Swift/Python clients. Define versioned canonical contracts for authenticated device/session identity, conversation turns, context provenance, tool definitions, action proposals, approval, execution results, errors, and capabilities. Choose one schema source and generate or validate language-specific representations. Add cross-language fixture tests so Swift, Python, and the desktop client cannot silently drift.

Implement a shared runtime that owns durable conversation state, provider configuration, policy, and routing. Keep device-specific hardware access in injected adapters. Replace duplicate client tool lists with capability discovery filtered by real implementations, paired-device availability, and grants. Replace live-mode mock tool execution for this increment with the real protocol path; retain explicitly labeled demo mode.

Deliver these narrowly scoped workflows:

- Pair a Windows agent securely, show its capabilities on desktop and iPhone, and revoke it.
- Ask for PC status from either client and receive actual metrics from that paired PC.
- Launch one approved application through the same shared policy/runtime path from either client, using a safe test executor in automation.
- Exercise approval with a harmless test action; validate a real risky action manually only in a disposable target environment.
- Start a conversation on one device and continue it on the other using the same conversation ID, history, prompt version, and model deployment. Verify equivalent behavior without requiring identical stochastic wording.
- Disconnect the agent or provider and report unavailability honestly, with no mock success and no replay of queued side effects after reconnection.

On Windows, implement and test the Swift contract changes as far as the environment permits, but do not claim Xcode builds or physical iPhone verification occurred. Provide the exact remaining macOS/Xcode and hardware checks. Preserve Meta DAT restrictions and update state-machine tests with any new state or transition.

Do not expand this pass into a frontend rewrite, unrestricted computer use, model training, or a large integration catalog. Maintain compatibility through explicit adapters/migrations rather than parallel undocumented protocols. Continue with reasonable reversible engineering decisions; ask only for genuinely missing credentials, hardware access, or consequential choices.

Deliver a clear report of changes, security guarantees and remaining trust assumptions, test evidence, migration/setup instructions, and the working phone-to-PC increment. Separate source inspection, mocked tests, live-server checks, and physical-device verification. Never reuse the previous report's test counts or claims without reproducing them.
