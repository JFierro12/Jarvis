import SwiftUI
import JarvisKit

struct MainView: View {
    @EnvironmentObject private var environment: AppEnvironment
    @ObservedObject var coordinator: AssistantCoordinator
    @State private var showingSettings = false
    @State private var showingMemories = false
    @State private var showingDiagnostics = false
    @State private var typedCommand = ""

    var body: some View {
        NavigationStack {
            ZStack {
                Color.black.ignoresSafeArea()
                VStack(spacing: 24) {
                    header
                    if case .error(let message) = coordinator.wearableConnectionState {
                        Text(message)
                            .font(.caption)
                            .foregroundStyle(.red)
                            .multilineTextAlignment(.center)
                            .accessibilityLabel("Glasses connection error: \(message)")
                    }
                    if case .error(let message) = coordinator.gestureControlState {
                        Text(message)
                            .font(.caption)
                            .foregroundStyle(.red)
                            .multilineTextAlignment(.center)
                            .accessibilityLabel("Gesture control error: \(message)")
                    }
                    Spacer()
                    stateText
                    if !coordinator.partialTranscript.isEmpty {
                        Text(coordinator.partialTranscript)
                            .font(.body)
                            .foregroundStyle(.secondary)
                            .multilineTextAlignment(.center)
                            .padding(.horizontal)
                            .accessibilityLabel("Transcript: \(coordinator.partialTranscript)")
                    }
                    if !coordinator.lastResponse.isEmpty {
                        Text(coordinator.lastResponse)
                            .font(.title3)
                            .foregroundStyle(.white)
                            .multilineTextAlignment(.center)
                            .padding(.horizontal)
                            .accessibilityLabel("Response: \(coordinator.lastResponse)")
                    }
                    Spacer()
                    activationControl
                    quickActions
                    stopButton
                }
                .padding()
            }
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Settings") { showingSettings = true }
                }
            }
            .sheet(isPresented: $showingSettings) { SettingsView() }
            .sheet(isPresented: $showingMemories) { MemoriesView() }
            .sheet(isPresented: $showingDiagnostics) { DiagnosticsView(coordinator: coordinator) }
        }
    }

    private var header: some View {
        HStack {
            VStack(alignment: .leading) {
                Text("JARVIS").font(.largeTitle.bold()).foregroundStyle(.white)
                Text(environment.configuration.runtimeMode == .demo ? "Demo Mode" : "Live")
                    .font(.caption)
                    .foregroundStyle(.orange)
            }
            Spacer()
            if environment.wakeWordState == .listening {
                Image(systemName: "waveform")
                    .foregroundStyle(.blue)
                    .accessibilityLabel("Listening for the wake phrase")
                    .accessibilityHint("Say \"Jarvis\" to activate")
            }
            connectionIndicator
            gestureControlIndicator
        }
    }

    private var gestureControlIndicator: some View {
        Group {
            if case .active = coordinator.gestureControlState {
                Button {
                    Task { await coordinator.stopBrowseMode() }
                } label: {
                    Label("Browsing", systemImage: "hand.point.up.left.fill")
                        .font(.caption)
                        .foregroundStyle(.blue)
                }
                .accessibilityLabel("Gesture control active")
                .accessibilityHint("Double tap to stop browsing")
            }
        }
    }

    private var connectionIndicator: some View {
        Button {
            Task {
                if case .connected = coordinator.wearableConnectionState {
                    await coordinator.disconnectWearable()
                } else {
                    await coordinator.connectWearable()
                }
            }
        } label: {
            Label(connectionLabelText, systemImage: connectionSystemImage)
                .font(.caption)
                .foregroundStyle(connectionColor)
        }
        .disabled(isConnecting)
        .accessibilityLabel("Glasses \(connectionLabelText)")
        .accessibilityHint(connectionAccessibilityHint)
    }

    private var isConnecting: Bool {
        if case .connecting = coordinator.wearableConnectionState { return true }
        return false
    }

    private var connectionLabelText: String {
        switch coordinator.wearableConnectionState {
        case .unavailable: return "Unavailable"
        case .disconnected: return "Disconnected"
        case .connecting: return "Connecting…"
        case .connected: return "Connected"
        case .paused: return "Paused"
        case .error: return "Connection error"
        }
    }

    private var connectionSystemImage: String {
        switch coordinator.wearableConnectionState {
        case .connected: return "eyeglasses"
        case .connecting: return "arrow.triangle.2.circlepath"
        case .error: return "exclamationmark.triangle"
        default: return "eyeglasses"
        }
    }

    private var connectionColor: Color {
        switch coordinator.wearableConnectionState {
        case .connected: return .green
        case .error: return .red
        case .connecting: return .orange
        default: return .secondary
        }
    }

    private var connectionAccessibilityHint: String {
        if case .connected = coordinator.wearableConnectionState {
            return "Double tap to disconnect your glasses"
        }
        return "Double tap to connect your glasses"
    }

    private var stateText: some View {
        Text(describe(coordinator.state))
            .font(.headline)
            .foregroundStyle(.white)
            .accessibilityLabel("Assistant state: \(describe(coordinator.state))")
    }

    private var activationControl: some View {
        VStack(spacing: 12) {
            TextField("Type a command (demo)", text: $typedCommand)
                .textFieldStyle(.roundedBorder)
                .foregroundStyle(.black)
            Button {
                let text = typedCommand
                typedCommand = ""
                Task { await coordinator.activate(mode: .pressToActivateSession, spokenText: text.isEmpty ? nil : text) }
            } label: {
                Circle()
                    .fill(Color.blue)
                    .frame(width: 96, height: 96)
                    .overlay(Image(systemName: "waveform").font(.title).foregroundStyle(.white))
            }
            .accessibilityLabel("Activate JARVIS")
            .accessibilityHint("Press once to start a listening session")
        }
    }

    private var quickActions: some View {
        HStack(spacing: 16) {
            quickActionButton("Look", systemImage: "eye") {
                Task { await coordinator.activate(mode: .pressToActivateSession, spokenText: "what am I looking at") }
            }
            quickActionButton("Remember", systemImage: "bookmark") {
                Task { await coordinator.activate(mode: .pressToActivateSession, spokenText: "remember this") }
            }
            quickActionButton("Memories", systemImage: "tray.full") { showingMemories = true }
            quickActionButton("Diagnostics", systemImage: "wrench.and.screwdriver") { showingDiagnostics = true }
        }
    }

    private func quickActionButton(_ title: String, systemImage: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            VStack {
                Image(systemName: systemImage)
                Text(title).font(.caption2)
            }
        }
        .foregroundStyle(.white)
        .accessibilityLabel(title)
    }

    private var stopButton: some View {
        Button(role: .destructive) {
            Task { await coordinator.stopEverything() }
        } label: {
            Text("Stop").frame(maxWidth: .infinity)
        }
        .buttonStyle(.bordered)
        .tint(.red)
        .accessibilityLabel("Stop everything")
        .accessibilityHint("Cancels listening, speech, and any active capture")
    }

    private func describe(_ state: AssistantState) -> String {
        switch state {
        case .idle: return "Ready."
        case .activating: return "Activating…"
        case .listening: return "Listening…"
        case .transcribing: return "Transcribing…"
        case .deciding: return "Thinking…"
        case .capturingVisualContext: return "Looking…"
        case .reasoning: return "Reasoning…"
        case .awaitingConfirmation(let action): return "Confirm: \(action.summary)?"
        case .executing(let action): return "Doing: \(action.summary)"
        case .speaking: return "Speaking…"
        case .completed: return "Done."
        case .failed(let error): return error.message
        }
    }
}
