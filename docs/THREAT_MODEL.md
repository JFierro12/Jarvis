# Threat model: Milestone 1 closeout and first shared runtime

The supported deployment in this increment is one personal Windows desktop host,
with one backend process and an injected local Windows agent. An optional private
HTTPS listener exposes the same runtime to enrolled clients. This is not a
multi-user service or a separately deployed remote-agent fleet.

## Identity and approval

- Public dashboard HTML contains no API or approval credential. The native host
  creates a one-hour session and injects it into the exact trusted dashboard URL.
  Session and approval secrets stay in page memory, not localStorage or URLs.
- SQLite stores hashes of credentials. Pairing invitations contain high-entropy,
  single-use secrets and expire after 120 seconds. Enrollment gives a new device
  identity and a separate session approval credential. Grants cannot exceed the
  enrolling desktop administrator's grants; installation grants are an additional ceiling.
- Live mode rejects legacy static bearer tokens, including development defaults.
  Legacy tokens remain usable only in explicitly selected demo/test mode.
- Approval requires both the proposing session and its distinct approval secret.
  A model/provider receives neither. The automatic executor cannot approve with
  its API token alone. Device/session fields come from server identity, not claims
  in a proposal body. Device revocation and logout invalidate credentials/actions.
- A compromised trusted UI can use its own approval credential. This does NOT
  prove human intent or resist recorded/spoofed approval speech. A fully compromised
  backend can bypass checks inside itself. Independent verification would be
  needed to protect against those stronger threats.

## Actions, outcomes, and cancellation

The server stores a canonical action: tool, target, content, immutable arguments,
principal, session, origin device, target device, and a configuration/policy hash.
Structured JSON encoding removes the former delimiter ambiguity. Execution uses
that stored object; changing fields in the execution request does not change it.

Policy is evaluated at proposal and execution. Automatically permitted actions
also receive server-owned IDs. Pending actions require explicit approval through
the trusted UI path. Unsupported argument maps are rejected in this increment.
Live capabilities are restricted to implemented, available, permitted adapters.

Execution IDs are consumed atomically. Repeated requests return the recorded
success/failure; an in-flight or unknown outcome is never reported as successful.
Only the original session can inspect/retry its action. Cancellation can stop work
before worker dispatch. Work already started may be non-interruptible and is
reported as such. Client generation counters discard superseded model responses.

A process-local store holds at most 256 actions. Settled records may be evicted;
unknown/evicted IDs are refused, never recreated or replayed. Pending approvals
are lost on restart. Conversation and completed action results persist separately.
An OS file lease prevents multiple runtime owners using the same runtime database.
Multiple workers and independent replicas are unsupported. Keep the same database
path on all listeners. Starting another installation with another database is a
separate deployment, not a shared worker.

The runtime routes PC status/application launch only to its registered device
adapter, checking availability and enrollment. It never silently executes on a
cloud host. Applications use the existing strict fixed-argv allowlist, without a
shell. Executable lookup still relies on the host's trusted installation/PATH;
an already compromised local environment is outside this boundary. File tools
remain unavailable in live shared-runtime capability discovery until their
execution/device integration is migrated. Legacy demo paths retain their folder
checks; this pass does not claim protection against every filesystem race.

## Transport and desktop boundary

Loopback is the default. Optional remote binding requires a specific private
address and configured TLS certificate/key. The listener shares the native
process's state and disables proxy-header trust. Mobile code requires HTTPS with
normal certificate verification. Host and Origin allowlists must match the
configured endpoint. Credential-bearing APIs use explicit authorization headers,
not ambient cookies. Native phone requests may omit Origin; browser requests
with unexpected origins are rejected. Request windows and invitation/action
stores are bounded. No public port forwarding is created.

WebView2 navigation is restricted to the local dashboard; new windows are blocked.
Microphone permission is granted only to that origin. Production debugging is off.
CSP disallows third-party scripts and framing; text is rendered using textContent.
Bootstrap uses the native raw-script API, avoiding an eval requirement in CSP.
The native rendering/navigation/microphone path still needs an interactive Windows
verification; browser and packaged headless smoke tests do not prove it.

The older standalone pc-agent keeps pairing-token authentication. Its macOS gesture
WebSocket rejects unexpected browser origins; the Quartz route is unavailable on
Windows. It is not the Windows adapter used by the new runtime. Do not expose it
as a substitute for v2 enrollment.

## Context and provider trust

Client-supplied SYSTEM_POLICY is rejected. The iOS legacy provider filters that
field during migration. V2 accepts a user question and server-owned conversation
identity, not client-supplied history or policy. Stored prior turns remain
CONVERSATION_HISTORY data; recorded execution output is TOOL_RESULT. Neither
source grants permission. Unknown provenance is rejected on the legacy endpoint.
Provider prompts remain a defense-in-depth measure, not proof of injection
immunity. Deterministic policy tests are independent of generated text.

Live reasoning/vision/transcription do not silently fall back to mock success.
Live clients without a configured runtime use unavailable providers. Demo mode
remains explicitly selectable. Speech may fall back to the device's own voice.
Question, response, and proposed-action bodies are no longer logged by /v1/reason.
Private conversations remain in local SQLite; at-rest encryption and account-wide
retention/export controls are not added by this increment. Device Keychain storage
is retained on iOS. Do not include runtime databases, recordings, or credentials
in Git or model training datasets.

## Verification limits

Automated tests cover serialization, policy, session ownership, replay,
configuration isolation, pairing/revocation, conversation handoff, cancellation,
provider/agent unavailability, and contract fixtures. Tests prohibit external
network connections, model loading, screenshots, and arbitrary child processes.
The configuration-isolation test explicitly permits its own Python subprocess.

No claim is made of formal penetration testing, physical iPhone/glasses validation,
real voice replay resistance, or security against a compromised operating system.
See MILESTONE_2_SETUP.md and MILESTONE_2_VALIDATION.md for scope and evidence.
