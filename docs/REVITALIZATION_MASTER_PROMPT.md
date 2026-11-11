JARVIS revitalization master prompt

Prepared from a source review of the working tree on November 10, 2026. This is an implementation brief for a future coding agent, not a claim that these improvements already exist. Copy the text below into the implementation session.

---

You are the lead engineer responsible for revitalizing this JARVIS project into a reliable, fast, voice-first personal assistant across my Windows PC and iPhone, with supported Ray-Ban Meta integrations. Read the actual repository before changing it. Implement the product through working, tested increments; do not stop at an architecture proposal or a cosmetic redesign.

My goal is the closest practical experience to JARVIS from the Iron Man films: natural conversation, useful memory, contextual awareness, research, hands-free computer operation, task planning, and authorized integrations. Translate that ambition into verifiable capabilities. Do not claim fictional intelligence, unavailable sensors, universal access, or actions that did not happen.

1. Establish the real baseline and preserve existing work.

Read `AGENTS.md`, `docs/ARCHITECTURE.md`, `docs/THREAT_MODEL.md`, `docs/META_SDK_NOTES.md`, the privacy and setup documents, and the relevant implementation and tests. Treat source code and reproduced behavior as evidence; documentation may be stale. Inspect Git status and preserve existing modified and untracked files. Do not reset the repository, overwrite user changes, delete databases, or rewrite history as part of cleanup.

The reviewed working tree already has these foundations and gaps. Re-check each before implementation:

- This is already a monorepo: SwiftUI and JarvisKit in `ios/`, FastAPI in `backend/`, a Python companion service in `pc-agent/`, and Windows packaging in `desktop/`.
- The desktop UI is `backend/app/static/`, hosted inside a pywebview application through `backend/app/desktop_main.py`. Desktop orchestration is largely in `static/assets/app.js`; iOS orchestration is in `AssistantCoordinator.swift`.
- `JarvisApp` calls `makeFromStoredPreferences()`. Live mode and wake-word code exist despite older README claims. `AppEnvironment.makeLive` still injects `MockToolExecutor`, which handles some real local operations and PC status but does not provide desktop tool parity. Missing configuration can also select mocks in live mode.
- Reasoning adapters exist for Anthropic and Ollama. Ollama explicitly uses `stream: False`; responses and remote speech are delivered as complete payloads. Provider names in configuration are not proof that a model exists or supports the requested parameters.
- Desktop conversation history is browser-local; iOS memories use SwiftData and backend memories use SQLAlchemy. There is no complete shared conversation and memory synchronization system. Backend memory search uses keyword matches plus recency, not semantic retrieval or model training.
- Tool definitions and supported executions differ across Swift, Python, and JavaScript. Some registered tools have no executable implementation. `/v1/integrations/pc/status` returns mock data, while another tool reads local host metrics.
- PC-agent commands include macOS-specific argv and placeholder commands. Its gesture implementation imports Quartz, and its dependency declaration is not platform-gated. Windows desktop automation lives separately in the backend.
- `backend/app/services/os_automation.py` passes application text to `subprocess.Popen(..., shell=True)`. Replace this before expanding computer control; it is inconsistent with a constrained action system.
- Backend tool execution rechecks policy, but accepts client-supplied `granted_permissions` and `confirmed`. `/v1/session` returns an identifier without maintaining an authoritative conversation or authorization session.
- The dashboard root injects an API bearer token into HTML without authenticating the page request. Desktop confirmation summaries use `innerHTML` with text containing tool targets. Address these before making the service reachable from another device.
- Desktop history uses a `CONVERSATION_HISTORY` source that is absent from Swift's `ContextSource`; the backend prompt calls this history trusted. Preserve original roles and provenance instead of letting old assistant text or quoted external content become authorization.
- `jarvis.db` and generated `backend/jarvis_backend.egg-info/` files are tracked. Establish what the database contains and preserve it through a migration; do not assume it is disposable.
- Existing tests cover policy, prompt injection, provider adapters, speech, file boundaries, gestures, and the assistant state machine. Old test counts are not current evidence. The shell test script also masks iOS UI-test failures as launch limitations; distinguish actual failures from unavailable environments.
- The Meta notes record some physical-device registration and connection verification, while other behavior and timing remain unverified. Preserve this distinction.

Produce a short baseline report listing actual capabilities, broken or incomplete paths, documentation drift, test results, and environment limitations. Read-only inspection is sufficient for unsafe-to-run hardware actions. Then begin implementation.

2. Make the monorepo deliver one assistant across devices.

Retain useful existing Swift and Python code. Reorganize only when it makes ownership, dependencies, or testing clearer. Do not replace native iOS just to make directory names symmetrical.

Create a shared assistant runtime responsible for conversation state, model routing, retrieval, task execution, and authoritative policy. Make mobile and desktop clients use its versioned API and event protocol. Keep device audio, UI, camera, and operating-system access in injected platform adapters.

Define one canonical contract for messages, context provenance, tool arguments and results, capabilities, confirmations, errors, and streaming events. Generate or validate Swift, Python, and TypeScript representations from it. Maintain a single tool manifest with matching policy fixtures; do not keep independently edited tool lists in three languages. Where iOS requires local policy, enforce behavioral agreement with shared contract tests and retain the backend execution check.

For shared online operation, use the same pinned model deployment, prompt version, tools, user preferences, and session memory on both devices. PC-hosted inference should be usable from the phone through an authenticated connection. Offer cloud hosting as an explicit configuration option. A phone connecting to PC inference is not running the model on-device. Offline fallback must disclose reduced capabilities and synchronize appropriately after reconnection. Do not promise identical generated wording across nondeterministic runs or different hardware.

Make Windows and iOS the first supported platforms. Preserve macOS adapters behind explicit capability boundaries. Document how Android could later use the same contracts without expanding this implementation into an unrequested Android rewrite.

Implement persistent conversation IDs, ordered turn/event IDs, device identity, resumable connections, and conversation handoff. Synchronize explicitly saved memories, preferences, edits, and deletion tombstones with a documented conflict policy. A deleted memory must not reappear from an offline device. Keep transcripts, personal memories, retrieved documents, and training records as separate data classes with separate retention controls.

Create secure pairing and revocation. Keep loopback binding by default; remote use requires an authenticated, encrypted private transport or relay. An outbound PC-agent connection is acceptable. Never solve mobile access by publicly exposing an unauthenticated desktop backend. Capabilities must identify which device will perform an action; a cloud server must never execute a desktop command against its own host by accident.

3. Make hands-free PC operation useful and dependable.

Move Windows execution behind a dedicated, authenticated device-agent interface. Reuse proven automation code after fixing its boundaries. Use platform-specific adapters with lazy imports and platform markers so macOS libraries cannot prevent Windows startup.

Support voice commands to launch and focus approved applications; identify and safely close windows; search supported websites; control media and volume; inspect PC health; read, create, append, rename, and recycle files within permitted folders; take a requested screenshot; and run predefined development tasks. Add typed interaction with supported UI controls where practical, using accessibility identifiers and verified window identities before coordinate clicking.

Examples that should work end to end include “Open VS Code on my PC,” “What is using my memory?”, “Create a note in my permitted notes folder,” “Read the error on my screen,” and “Find this on YouTube.” Follow-up references such as “close that window” must resolve against verified current state and the selected device.

Use allowlisted command IDs mapped to fixed argv for subprocess tasks. Application launching must resolve an approved application ID to a known executable without a shell. Do not add raw shell, arbitrary PowerShell, eval, or model-generated script execution. Richer browser and UI actions need explicit schemas, bounded parameters, target validation, and policy checks. Typed text remains inert data; typing commands into a terminal cannot become an alternate route around command restrictions.

Support bounded multistep tasks through a plan/execute/observe loop. Recheck authorization and target state for every action. Bound steps, retries, duration, and resource use. Return actual tool results to the assistant before it reports success. Track partial completion and provide undo where supported. Never replay a destructive or non-idempotent action automatically after an ambiguous timeout.

Provide spoken confirmation and cancellation, a visible emergency stop, and a local stop path that does not depend on the LLM or network. Stopping cancels queued actions and active generation/audio; report actions already completed or impossible to interrupt. Do not promise rollback for irreversible work.

4. Enforce authorization throughout the expanded system.

Preserve the repository's rule: models propose actions; `PolicyEngine` and `evaluate_tool_call` authorize them. All hardware, network, and AI dependencies remain injected protocols/interfaces. Keep implementation types out of views and orchestration logic, including mock defaults that conceal missing dependencies.

Derive permissions from authenticated server-side user/device grants. Replace `confirmed: true` as an authority with an expiring, single-use pending action bound to the user, session, target device, tool, complete normalized arguments/content, and policy version. Confirmation must consume that exact action. Changes to its target or content require a new decision. Preserve arguments through confirmation; do not reconstruct a call from only a name and target.

Only `SYSTEM_POLICY` and authenticated `USER_REQUEST` context may express authorization, and policy remains the final authority. Reject unknown context-source values. Construct system policy on the server; a client cannot promote arbitrary text by labeling it `SYSTEM_POLICY`. Keep camera observations, webpages, retrieved memories, documents, tool output, and quoted content untrusted throughout summarization and history storage. Previous assistant messages never grant permissions.

Replace token injection into publicly readable HTML with a secure desktop bootstrap and authenticated remote session design. Add appropriate origin/host checks, safe text rendering, request limits, credential storage, token expiry/revocation, and sanitized diagnostics. Audit default tokens and logs containing questions, answers, or tool content. Scope file access against traversal, symlinks, junctions, and target changes between validation and execution.

Keep demo mode explicit and deterministic. An unavailable live service must return an honest unavailable/degraded state; it must not silently pretend a mock action changed the real world.

5. Deliver fast, natural spoken conversation.

Build an incremental voice pipeline: local activation, voice activity detection, partial transcription, turn endpoint detection, streaming reasoning, sentence-level speech synthesis/playback, and interruption. Support push-to-talk, an explicitly enabled local wake word where the platform permits it, bounded follow-up listening, and a clear microphone indicator. Treat TV audio and the assistant's own speech as interference, not authenticated approval.

Add barge-in so I can interrupt a response, correct a request, or say “stop.” Cancel superseded requests and discard late events using turn IDs. Prevent overlapping microphone ownership and duplicate speech. Restore Bluetooth routes safely and test echo, background noise, disconnects, and audio-device changes.

Separate spoken text events from structured tool proposals. Buffer and validate a complete proposal before policy evaluation; never execute partially streamed JSON. Do not speak raw JSON or announce success before a tool result. Streaming is supported by Ollama's API; the current disabled streaming path is an implementation choice to revisit. See [Ollama streaming documentation](https://docs.ollama.com/api/streaming).

Use a deterministic fast path for simple recognized commands, still through policy. Reuse connections, warm configured models, trim irrelevant context, cache stable prompt components, and use bounded queues. Choose larger reasoning models only when the task benefits. Benchmark actual CPU, RAM, GPU/VRAM, and network conditions before choosing inference settings; comments about hardware in the repository are not a machine inventory.

Adopt these initial engineering targets, then publish measured results and justified revisions: local activation acknowledgement p95 below 200 ms; warm common commands starting execution p95 below 1 second after the end of speech; ordinary conversational first audible answer p50 below 1 second and p95 below 2 seconds; audio interruption p95 below 250 ms after stop detection. These are targets, not guarantees or claims of current performance.

Measure physical end-of-speech to first useful audio, including endpointing, transcription, network, model, and synthesis. Report cold-start and warm results separately, with sample size, hardware, model, and network conditions. An acknowledgement chime does not count as an answer. Slow research tasks should give useful progress without filler. Resource contention from training must not degrade interactive inference.

Use a calm, concise JARVIS-inspired personality with configurable formality and forms of address. Prefer one or two spoken sentences unless the task needs detail. Provide longer explanations and sources visually. Preserve personal features such as football analysis, music, and gestures as explicit modules rather than deleting them merely because they are specialized.

6. Build current knowledge and a high-quality learning pipeline.

Interpret “constantly trains itself and is never out of date” as three distinct systems: refreshed knowledge, editable personal memory, and evaluated model improvement. No dataset guarantees universal freshness or correctness. Do not build an uncontrolled loop that treats every conversation or generated answer as truth.

For current knowledge, implement permission-scoped retrieval over approved documents and live information sources. Combine lexical and semantic retrieval where benchmarks justify it, with metadata filtering and reranking. Store source URLs, publication/update times, fetch times, content hashes, and expiry rules. Answer time-sensitive questions from fresh evidence; disclose stale or unavailable evidence and conflicting sources. Document refresh cadence by source type. RAG combines model knowledge with retrieved external information; it does not itself mean the model's weights have been updated. See the [original RAG paper](https://arxiv.org/abs/2005.11401).

For personal memory, let me explicitly save, inspect, correct, export, and delete preferences and facts. Record provenance and timestamps. Distinguish an observed fact from an inference and “last recorded” from “currently true.” Personal memory should normally update storage, not model weights, so corrections and deletion remain practical.

For model improvement, deliver a versioned dataset pipeline and a reproducible training job for a compatible open-weight model. Provider-hosted models may have different or unavailable fine-tuning options; verify their supported interfaces instead of claiming the app can modify their weights. Prefer adapter training when appropriate to measured hardware and quality goals.

Research and compare candidate datasets from original dataset/model cards. Create a manifest recording exact revisions, licenses, permitted uses, provenance, languages, size, task coverage, quality concerns, and download instructions. Select a justified blend covering concise conversation, grounded Q&A, typed tool use, multistep task execution, clarification, corrections, speech-transcription errors, interruption, and prompt-injection resistance. Do not select a dataset merely because it is large or popular.

Add project-specific examples from sandboxed tool traces with verified outcomes, reviewed user corrections, and synthetic tasks checked against executable environments. Include negative examples: unknown tools, wrong devices, stale confirmations, missing permissions, misleading tool results, unavailable capabilities, and false success claims. Never label a trace successful solely because an LLM evaluator says it is.

Use conversational supervised fine-tuning records and, when justified by evaluation, chosen/rejected preference records. Maintain adapters to the selected training library's formats; [TRL's dataset documentation](https://huggingface.co/docs/trl/dataset_formats) distinguishes these formats and trainer requirements.

Each internal record must carry an ID, schema version, source/license, consent scope, timestamps, task category, context roles/provenance, available tool schemas, expected or observed results, quality labels, lineage, and split assignment. Validate schemas, redact secrets and personal data, deduplicate exact and near duplicates, quarantine poisoned/unverified content, and separate train/validation/test by source, conversation, task family, and time where relevant. Keep evaluation fixtures out of training.

Implement an opt-in scheduled workflow: ingest approved changes, validate and curate, create an immutable dataset version, train a candidate under a resource budget, evaluate it against the deployed baseline, and promote only if configured release gates pass. Failed candidates never replace production. Default to review before promotion; allow automatic promotion only under an explicit standing policy with bounded canary rollout and rollback. Automated ingestion may run frequently; retraining should be triggered by sufficient useful data or demonstrated quality gaps, not every utterance.

Measure tool correctness, end-to-end task success, unsupported success claims, injection resistance, grounded-answer quality, general capability regression, latency, and memory consumption. Use both fixed holdouts and new temporal tests. Log dataset/model/prompt versions for every release. Ship reproducible recipes, a small clearly labeled pipeline-validation dataset, and a dry run; do not advertise that seed dataset as an advanced trained model. Report separately whether full training actually ran and whether a candidate improved results.

Exclude private recordings, screenshots, files, credentials, and transcripts from training by default. Implement lineage-based removal from future datasets and retrieval indexes. Removing training examples does not reliably remove their influence from existing weights: document affected model versions and replacement/retraining requirements. Keep large corpora and checkpoints outside ordinary Git history.

7. Expand the practical JARVIS feature set.

Deliver conversation continuity, factual research with sources, personal memory, PC control, requested screen/camera understanding, system diagnostics, reminders/calendar, music, and configured smart-home control as explicit capabilities. Add proactive briefings and routines only for user-configured triggers, schedules, data sources, and allowed actions. Make quiet hours, pause, and audit history available.

Keep unsupported integrations visible as unavailable or planned. Never fabricate account connectivity. Preserve Meta DAT restrictions: read `docs/META_SDK_NOTES.md` before integration changes; verify uncertain APIs against the SDK's own instructions, changelog, samples, and resolved `.swiftinterface`. Isolate uncertainty behind a protocol/mock with a precise `TODO(hardware-verification)`.

Do not upgrade DAT without reviewing its capability matrix. Never claim firmware-native “Jarvis” activation or set `supportsNativeVoiceInvocation` true without current SDK evidence. Reconcile existing background-wake-word flags with verified platform support and privacy documentation; no silent-audio keep-alive tricks or claims of continuous recording. Display features require actual display hardware and verified APIs.

8. Clean the repository using evidence.

Inventory imports, runtime registrations, reflection, packaging resources, tests, feature flags, dependencies, and deployment entry points before deleting anything. Record each removal and why it is unreachable, duplicated, generated, or superseded. Remove stale wrappers, dead branches, obsolete debug patches, and unused dependencies only after demonstrating their replacement or lack of use.

Keep useful mocks, protocol seams, migrations, and regression tests. Split oversized orchestration and UI files by responsibility. Extract shared prompts/contracts from provider-specific modules. Remove duplicate dependency declarations where possible, introduce reproducible locks, and align supported Python versions, tooling, and CI deliberately.

Stop tracking generated package metadata and local databases only through a migration that preserves local data. Expand ignore rules for databases, build artifacts, logs, datasets, and checkpoints. Never publish or inspect private database contents unnecessarily. Update README, architecture, threat model, privacy, setup, and limitations to match the implemented behavior.

Provide practical development/test commands for PowerShell and macOS/Linux. Add CI appropriate to Windows backend/agent/desktop and macOS iOS builds. A platform test that could not run is “not run,” not “passed.”

9. Implement in dependency order and prove completion.

Use these milestones, delivering a runnable increment after each:

1. Baseline audit, preservation of existing work, regression coverage for dangerous execution and authorization gaps, and fixes for those gaps.
2. Canonical contracts, shared assistant runtime, secure pairing, capability discovery, and real mobile-to-PC tool execution.
3. Streaming conversation, reliable cancellation, voice confirmation, cross-device sessions, and measured latency improvements.
4. Shared personal memory and source-backed knowledge retrieval with refresh, deletion, and offline behavior.
5. Curated dataset infrastructure, reproducible candidate training/evaluation, promotion gates, and rollback.
6. Additional authorized integrations, modular personal features, evidence-based cleanup, packaging, documentation, and release validation.

Acceptance must include the following end-to-end scenarios, using deterministic fixtures for contracts and real hardware/provider tests separately:

- Start a conversation on iPhone, continue it on PC, and resolve a follow-up against the same stored conversation and model configuration.
- Give the same supported request through both clients and obtain equivalent policy decisions, target-device selection, and verified effects.
- Launch an approved Windows application by voice; reject an unapproved executable or shell-like payload without executing it.
- Complete a safe multistep task, then interrupt another task and prove no queued actions execute afterward.
- Require a fresh confirmation for a risky action; reject forged, expired, reused, cross-session, or modified-action confirmations and permission claims.
- Show that malicious camera text, retrieved documents, webpages, tool outputs, and stored history cannot grant authorization or bypass policy.
- Disconnect the PC or model provider and show an accurate unavailable state without mock success or duplicate execution after reconnecting.
- Save, correct, and delete a memory across devices; an offline client's resynchronization must preserve deletion.
- Refresh a known external fact and answer with the new source timestamp; disclose stale evidence when refresh fails.
- Produce a validated dataset version and reproducible candidate evaluation; reject a deliberately regressive candidate and demonstrate rollback.
- Publish latency distributions and voice-interruption results under the declared benchmark conditions.
- Run relevant existing and new tests, including exhaustive `AssistantStateMachineTests` whenever states/transitions change. Build Windows packaging and validate iOS on macOS/Xcode. Report hardware-dependent checks honestly.

Use reasonable engineering judgment and continue through authorized, reversible implementation work. Ask for missing secrets, hardware access, costs, or consequential product choices only when necessary, while continuing independent work. Do not ask me to approve every file edit or substitute a long plan for working code.

At completion provide a concise change summary, architecture and contract documentation, capability matrix, cleanup/migration ledger, test and benchmark evidence, dataset/model cards, reproducible setup and training commands, and remaining limitations. Distinguish implemented, tested, hardware-verified, and still planned features. Never invent passing tests, training results, performance figures, provider capabilities, or movie-level functionality.
