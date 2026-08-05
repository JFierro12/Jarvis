# Architecture

## System architecture

```mermaid
graph TD
    Glasses["Ray-Ban Meta glasses<br/>(camera via DAT SDK)"]
    Phone["iPhone: Jarvis app"]
    Backend["Backend (FastAPI)<br/>reasoning / vision / memory"]
    PCAgent["pc-agent (localhost)"]
    SmartHome["Home Assistant"]

    Glasses -- "photo capture (Bluetooth)" --> Phone
    Phone -- "Bluetooth HFP/A2DP audio" --> Glasses
    Phone -- "HTTPS, per-request only" --> Backend
    Phone -- "HTTPS, localhost/LAN, pairing token" --> PCAgent
    Backend -- "HTTPS, allowlisted domains, confirmation-gated" --> SmartHome

    subgraph iOS["ios/Jarvis + ios/JarvisKit"]
        UI["SwiftUI Views"]
        Coordinator["AssistantCoordinator<br/>(orchestration)"]
        Protocols["Protocols: WearableDeviceClient,\nSpeechToTextProvider, TextToSpeechProvider,\nVisionReasoningProvider, LanguageReasoningProvider,\nMemoryRepository, ToolExecutor, AudioRouteManager"]
        Mocks["Mock implementations\n(demo mode, tests)"]
        RealAdapters["Real implementations\n(MetaWearableDeviceClient, AppleSpeech*,\nCloud*Provider, HomeAssistantClient)"]

        UI --> Coordinator
        Coordinator --> Protocols
        Protocols -.-> Mocks
        Protocols -.-> RealAdapters
    end

    Phone --- iOS
```

Every hardware/network/AI dependency is a protocol (`ios/JarvisKit/Sources/JarvisKit/Core/Protocols/`).
`AssistantCoordinator` only knows about protocols, never concrete SDK types — see
`ios/JarvisKit/Sources/JarvisKit/Services/AssistantCoordinator.swift`.

## Request sequence — "what am I looking at?"

```mermaid
sequenceDiagram
    participant U as User
    participant App as Jarvis app
    participant SDK as Meta DAT SDK / MockDeviceKit
    participant Vision as VisionReasoningProvider
    participant TTS as TextToSpeechProvider

    U->>App: Press activation button
    App->>App: state = .activating -> .listening
    U->>App: "what am I looking at?"
    App->>App: state = .transcribing -> .deciding
    App->>App: IntentRouter -> analyze_scene (requiresVisualContext)
    App->>App: state = .capturingVisualContext
    App->>SDK: capturePhoto()
    SDK-->>App: CapturedImage
    App->>App: state = .reasoning
    App->>Vision: analyze(image, question)
    Vision-->>App: VisionAnalysisResult (answer, confidence)
    App->>App: state = .speaking
    App->>TTS: speak(answer)
    App->>App: state = .completed -> .idle
```

## Assistant state machine

```mermaid
stateDiagram-v2
    [*] --> idle
    idle --> activating
    activating --> listening
    listening --> transcribing
    transcribing --> deciding
    deciding --> capturingVisualContext
    deciding --> reasoning
    deciding --> awaitingConfirmation
    deciding --> executing: resolved confirmation
    deciding --> speaking: purely local answer
    capturingVisualContext --> reasoning
    reasoning --> awaitingConfirmation
    reasoning --> executing
    reasoning --> speaking
    awaitingConfirmation --> executing
    awaitingConfirmation --> speaking: rejected/expired
    executing --> speaking
    speaking --> completed
    completed --> idle
    completed --> activating: bounded follow-up
    idle --> [*]

    state "any state" as any
    any --> failed
    failed --> idle
```

See `ios/JarvisKit/Sources/JarvisKit/Core/AssistantState.swift` for the exact transition
table and `ios/JarvisKit/Tests/JarvisKitTests/AssistantStateMachineTests.swift` for the
tests that enforce it (no overlapping listening sessions, no executing without going
through reasoning/confirmation, etc.).

## Tool authorization

```mermaid
sequenceDiagram
    participant LLM as LanguageReasoningProvider
    participant Policy as PolicyEngine
    participant Confirm as ConfirmationManager
    participant Exec as ToolExecutor

    LLM->>Policy: proposes ToolCall(name, target)
    Policy->>Policy: look up ToolDefinition, check permissions
    alt read-only / reversible, permitted
        Policy-->>Exec: allow
        Exec-->>Exec: run, return ToolResult
    else risky (sensitive_write / destructive)
        Policy-->>Confirm: requireConfirmation(PendingAction)
        Confirm-->>Confirm: store with expiry
        Note over Confirm: user must say "confirm" within the window
        Confirm->>Exec: confirmed -> run
    else denied
        Policy-->>Policy: deny(reason)
    end
```

The model never authorizes its own tool calls — `PolicyEngine`
(`ios/JarvisKit/Sources/JarvisKit/Services/PolicyEngine.swift` and its backend twin
`backend/app/tools/policy.py`) is the only place a call is allowed, denied, or sent to
confirmation. The backend re-runs policy at `/v1/tools/execute` time too, independent of
whatever the client believes `/v1/tools/propose` already said.

## Memory flow

```mermaid
flowchart LR
    Request["'remember where I put my keys'"] --> Router[IntentRouter]
    Router -->|rememberScene| Capture[capturePhoto]
    Capture --> Vision[VisionReasoningProvider]
    Vision --> Record["MemoryRecord\n(title, summary, timestamp,\ncoarse location if enabled)"]
    Record --> Repo[MemoryRepository]
    Repo --> Local[(SwiftData, on-device)]
    Repo -.optional sync.-> Backend[(Backend memories table)]

    Search["'where did I last see my keys?'"] --> Router2[IntentRouter]
    Router2 -->|searchMemory| Repo2[MemoryRepository.search]
    Repo2 --> Local
    Repo2 --> Rank["rank by keyword match + recency"]
    Rank --> Answer["'Last recorded 2 hours ago\non the kitchen counter'"]
```

Nothing here claims an object is *currently* at a location — every search result carries
its original timestamp, and `AssistantCoordinator.summarize(searchResults:)` always
phrases it as "last recorded," never "is."

## Meta device lifecycle

```mermaid
stateDiagram-v2
    [*] --> unavailable
    unavailable --> disconnected: registered
    disconnected --> connecting: connect()
    connecting --> connected: DeviceSession reaches .started
    connected --> paused: system pauses (doffed, another app, etc.)
    paused --> connected: device resumes to .started
    paused --> disconnected: DeviceSession reaches .stopped
    connected --> disconnected: disconnect() / session stopped
    disconnected --> [*]
```

Backed by the real DAT SDK's `DeviceSession` states (`idle/starting/started/paused/stopping/stopped`,
see `docs/META_SDK_NOTES.md`) via `MetaWearableDeviceClient`, or by `MockWearableDeviceClient`
in demo mode. The app never tries to restart work while `.paused` — it waits for `.started`
or `.stopped`, per the SDK's own documented lifecycle rules.

## Layering

```
SwiftUI views (ios/Jarvis/Jarvis/Features/)
    -> AssistantCoordinator (application service)
    -> Protocols (Core/Protocols)
    -> Mocks (demo/tests) or real Integrations (Meta SDK, AVFoundation, URLSession, Home Assistant)
```

```
FastAPI routes (backend/app/api/)
    -> auth + rate limiting
    -> intent/orchestration (reasoning, vision services)
    -> PolicyEngine (backend/app/tools/policy.py)
    -> memory repository (SQLAlchemy)
```

No SDK, networking, or persistence logic lives directly in a SwiftUI `View` or a FastAPI
route function beyond simple parameter validation and calling into the layer below.
