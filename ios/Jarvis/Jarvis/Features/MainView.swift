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
            .sheet(isPresented: $showingDiagnostics) { DiagnosticsView() }
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
            connectionIndicator
        }
    }

    private var connectionIndicator: some View {
        Label("Disconnected", systemImage: "eyeglasses")
            .font(.caption)
            .foregroundStyle(.secondary)
            .accessibilityLabel("Glasses disconnected")
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
