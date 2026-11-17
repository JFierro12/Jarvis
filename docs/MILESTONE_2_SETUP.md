# Shared runtime setup and migration

This increment connects the existing desktop and native iOS clients to one
PC-hosted assistant. It is intentionally limited to conversations, capability
discovery, PC metrics, allowlisted application launch, and backend memory deletion
for the approval flow. Calendar, full file automation, memory synchronization,
streaming speech, training, and a separate remote-agent service remain later work.

## Windows host

The reviewed build is `desktop/dist-milestone2/JARVIS/JARVIS.exe`. Run from its
directory so local configuration and database paths are predictable. Keep your
existing `.env` and databases; nothing has migrated or deleted their contents.
Set an absolute `JARVIS_DATABASE_URL` if the existing memory store is elsewhere.
The runtime uses a separate `JARVIS_RUNTIME_DATABASE` SQLite file.

The packaged host forces live sessions and enables its Windows adapter. Configure
a real reasoning provider using the existing provider settings. For local inference,
use the exact installed model name reported by `ollama list`; this pass does not
download a model. Enable `JARVIS_STT_PROVIDER=local` only when the configured speech
model is already installed. Remote TTS is optional; device/browser speech remains
available. Explicitly grant the PC capabilities you want:

```dotenv
JARVIS_GRANTED_PERMISSIONS=pc_agent,os_control
```

Static `JARVIS_AUTH_TOKENS` and `JARVIS_APPROVAL_KEY` are no longer live desktop
login credentials. The native host supplies separate, expiring API and approval
credentials to its trusted page. Sessions last one hour. Reopen/reload the native
window for a new desktop session. Phones must pair again after expiry; refresh
tokens are deliberately not implemented in this first increment.

## Phone connectivity

Loopback-only operation cannot be reached from a phone. To enable the optional
second listener, supply an existing certificate trusted by the iPhone, whose SAN
matches the private hostname or IP. Keep private keys out of the repository.
No certificate or firewall change is performed automatically.

```dotenv
JARVIS_REMOTE_BIND_HOST=192.168.1.50
JARVIS_REMOTE_PORT=8757
JARVIS_TLS_CERTIFICATE=C:/private-certs/jarvis.crt
JARVIS_TLS_PRIVATE_KEY=C:/private-certs/jarvis.key
JARVIS_ALLOWED_HOSTS=127.0.0.1,localhost,192.168.1.50
JARVIS_ALLOWED_ORIGINS=http://127.0.0.1:8756,http://localhost:8756,https://192.168.1.50:8757
```

Replace the example address with the actual private address. Allow the port only
on the intended private network if a firewall rule is needed. Do not use a public
bind address, `0.0.0.0`, forwarded headers, automatic port forwarding, or disabled
certificate verification. The native host owns both listeners and one runtime
store; do not start a second backend worker on the same files.

1. In the desktop sidebar, select **Create phone pairing code**. The code grants
   `pc_agent` and `os_control` only if the host itself has those grants.
2. In iOS Settings, enter the HTTPS runtime URL, paste the one-time code under
   **Shared assistant**, and select **Pair with PC**. The code expires in two minutes.
3. Enable Live Mode and relaunch after changing connection configuration.
4. Select **Refresh capabilities and conversations**. Choose an existing
   conversation or **New conversation**. The desktop has the same conversation selector.
5. Ask for PC status or an approved application such as Calculator. The phone
   reaches the registered Windows adapter; it does not execute on the phone.
6. Revoke a device through **Manage paired devices** on desktop. Its credentials
   and unspent actions immediately become invalid. **Disconnect session** logs out
   the current desktop/browser session.

Sensitive actions use the exact pending action held by the server. Confirm or
cancel using the displayed controls or the supported spoken-intent path. A new
request, cancellation, expiry, or logout must not reuse an earlier approval.
**Stop current action** stops queued work; an already-started OS action may not
be interruptible. Do not retry a reported uncertain outcome without checking it.

## API and data migration

- V2 request/response schemas originate in `backend/app/models/runtime.py` and are
  exported to `contracts/runtime-v2.json`. `contracts/turn-response.json` is the
  shared Python/JavaScript/Swift decoding fixture. Regenerate with
  `backend/.venv/Scripts/python.exe scripts/export_contract.py` from repository root.
- `/v2/turns` creates or resumes a durable conversation and uses a client turn ID
  for deduplication. Concurrent turns in one conversation are refused. Provider,
  model, and prompt version are pinned to the conversation; changed configuration
  requires a new conversation.
- `/v2/capabilities` filters the Python tool registry by implementation, grants,
  registered target, and agent availability. The legacy Swift registry remains
  for demo/local flows; it does not override shared-runtime policy.
- `/v1/tools/propose` returns an action ID, not a usable approval token. Live
  `/v1/tools/execute` accepts that ID and executes the stored action. The old
  `confirmation_token` and `confirmed` fields no longer authorize anything.
  Legacy extra envelope fields are ignored for migration, while unsupported tool
  argument maps are rejected. V2 envelopes reject unknown fields.
- API credentials cannot approve. Only the same enrolled UI session with its
  separate approval credential may call `/v1/actions/{id}/approve` or `/reject`.
  `/cancel` and status lookup are also bound to the proposing session.
- The current iOS client removes client policy from legacy reasoning requests.
  V2 builds context/history on the server. It does not synchronize the existing
  SwiftData memory store or import browser-local transcript history.
- Runtime conversations/devices survive restart; pending action IDs do not.
  Unknown IDs are refused rather than replayed. Tests use fresh temporary stores.
- The previously staged removal of `jarvis.db` and generated egg-info from Git is
  preserved. Local files remain on disk. This pass does not rewrite history.

## Verification commands

From `backend/`:

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
.\.venv\Scripts\python.exe -m ruff check app tests --no-cache
```

From the repository root:

```powershell
node --check backend/app/static/assets/app.js
node scripts/test_contract.cjs
.\backend\.venv\Scripts\python.exe scripts/export_contract.py --check
.\backend\.venv\Scripts\python.exe scripts/smoke_desktop_ui.py
```

The browser smoke test requires an already-installed Playwright Chromium and
uses temporary stores/fake reasoning. It does not download anything or access
the microphone. For the packaged backend smoke test, run the built executable
with `--self-test` from a writable directory; it writes `jarvis-self-test.txt`.

On macOS, run `./scripts/test.sh ios` and inspect the actual Xcode results; the
legacy script can mask UI-test launch failures. Verify the new SharedRuntime
tests, HTTPS pairing, conversation handoff, revoked/expired credentials, spoken
confirmation/cancellation, and Bluetooth audio on a physical iPhone. No Meta SDK
version, capabilities, or state-machine transitions were added by this change.
