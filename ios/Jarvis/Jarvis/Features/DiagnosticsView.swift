import SwiftUI
import JarvisKit

struct DiagnosticsView: View {
    @EnvironmentObject private var environment: AppEnvironment
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

    private func row(_ label: String, _ value: String) -> some View {
        HStack {
            Text(label)
            Spacer()
            Text(value).foregroundStyle(.secondary)
        }
    }
}
