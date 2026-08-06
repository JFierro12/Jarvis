import SwiftUI
import JarvisKit

struct DiagnosticsView: View {
    @EnvironmentObject private var environment: AppEnvironment
    @ObservedObject var coordinator: AssistantCoordinator
    @Environment(\.dismiss) private var dismiss
    @State private var copiedMessage: String?

    var body: some View {
        NavigationStack {
            List {
                let snapshot = environment.diagnosticsSnapshot()
                Section("Build") {
                    row("SDK version", snapshot.sdkVersion)
                    row("App version", snapshot.appVersion)
                    row("Device model", snapshot.deviceModel)
                }
                Section("Device") {
                    row("Connection", snapshot.connectionState)
                    row("Native voice invocation", snapshot.supportsNativeVoiceInvocation ? "Supported" : "Not supported")
                    row("Foreground wake word", snapshot.foregroundWakeWordEnabled ? "Enabled" : "Disabled")
                }
                Section("Backend") {
                    row("Reachable", snapshot.backendReachable ? "Yes" : "No")
                }
                Section("Memory") {
                    row("Database", snapshot.memoryDatabaseStatus)
                    row("Image storage", snapshot.imageStorageEnabled ? "Enabled" : "Disabled")
                }
                Section {
                    Button("Copy Diagnostics") {
                        UIPasteboard.general.string = snapshot.renderedForClipboard()
                        copiedMessage = "Copied."
                    }
                    if let copiedMessage {
                        Text(copiedMessage).font(.caption).foregroundStyle(.secondary)
                    }
                }
            }
            .navigationTitle("Diagnostics")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Close") { dismiss() } }
            }
        }
    }

    private var connectionStateText: String {
        switch environment.coordinator.wearableConnectionState {
        case .unavailable: return "Unavailable"
        case .disconnected: return "Disconnected"
        case .connecting: return "Connecting"
        case .connected(let model): return "Connected (\(model.rawValue))"
        case .paused: return "Paused"
        case .error(let message): return "Error: \(message)"
        }
    }

    private func row(_ label: String, _ value: String) -> some View {
        HStack {
            Text(label)
            Spacer()
            Text(value).foregroundStyle(.secondary)
        }
    }
}
