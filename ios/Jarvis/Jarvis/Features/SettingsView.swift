import SwiftUI
import JarvisKit

struct SettingsView: View {
    @EnvironmentObject private var environment: AppEnvironment
    @Environment(\.dismiss) private var dismiss

    @AppStorage(LiveModePreferences.liveModeEnabledKey) private var liveModeEnabled = false
    @AppStorage(LiveModePreferences.backendBaseURLKey) private var backendURLString = ""
    @AppStorage(LiveModePreferences.pcAgentBaseURLKey) private var pcAgentURLString = ""
    @State private var tokenText = ""
    @State private var tokenSaveConfirmation: String?
    @State private var pcAgentTokenText = ""
    @State private var pcAgentTokenSaveConfirmation: String?

    var body: some View {
        NavigationStack {
            List {
                Section("Connection") {
                    Toggle("Live Mode", isOn: $liveModeEnabled)
                    TextField("Backend URL (e.g. http://192.168.1.75:8000)", text: $backendURLString)
                        .keyboardType(.URL)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                    SecureField("Backend Auth Token", text: $tokenText)
                    Button("Save Token to Keychain") {
                        try? KeychainStore().set(tokenText, for: AppEnvironment.backendTokenKey)
                        tokenSaveConfirmation = "Saved."
                    }
                    if let tokenSaveConfirmation {
                        Text(tokenSaveConfirmation).font(.caption).foregroundStyle(.secondary)
                    }
                    Text("Live Mode uses your real glasses, on-device speech, and (if a backend URL is set) Claude for reasoning and vision. Changes here take effect the next time you fully quit and relaunch the app — not just background/foreground.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                Section("Privacy") {
                    Toggle("Long-term memory", isOn: bindingFor(\.enableLongTermMemory))
                    Toggle("Location context", isOn: bindingFor(\.enableLocationContext))
                    Toggle("Calendar", isOn: bindingFor(\.enableCalendar))
                    Toggle("Reminders", isOn: bindingFor(\.enableReminders))
                }
                Section("Capture") {
                    Toggle("Photo capture", isOn: bindingFor(\.enablePhotoCapture))
                    Toggle("Video streaming (experimental)", isOn: bindingFor(\.enableVideoStreaming))
                }
                Section("Voice") {
                    Toggle("Experimental foreground wake word", isOn: bindingFor(\.enableForegroundWakeWord))
                    Text("On-phone detection. Not related to your glasses' \"Hey Meta\" wake phrase. Uses extra battery while active and shows a visible listening indicator.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    Toggle("Listen in background", isOn: bindingFor(\.enableBackgroundWakeWord))
                        .disabled(!environment.configuration.flags.enableForegroundWakeWord)
                    Text("Keeps listening after you leave the app, until you fully close it (not just background it). Requires the wake word toggle above. Meaningfully higher battery use than foreground-only, and iOS can still suspend it — not a 100% guarantee.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                Section("Integrations") {
                    Toggle("PC agent", isOn: bindingFor(\.enablePCAgent))
                    TextField("pc-agent URL (e.g. http://192.168.1.50:8765)", text: $pcAgentURLString)
                        .keyboardType(.URL)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                    SecureField("pc-agent Pairing Token", text: $pcAgentTokenText)
                    Button("Save Pairing Token to Keychain") {
                        try? KeychainStore().set(pcAgentTokenText, for: AppEnvironment.pcAgentTokenKey)
                        pcAgentTokenSaveConfirmation = "Saved."
                    }
                    if let pcAgentTokenSaveConfirmation {
                        Text(pcAgentTokenSaveConfirmation).font(.caption).foregroundStyle(.secondary)
                    }
                    Text("Also used for \"browse\" hand-gesture cursor control, streamed to pc-agent over your local network.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    Toggle("Smart home", isOn: bindingFor(\.enableSmartHome))
                    Toggle("Remote (cloud) text-to-speech", isOn: bindingFor(\.enableRemoteTTS))
                }
                Section("Developer") {
                    Toggle("Developer diagnostics", isOn: bindingFor(\.enableDeveloperDiagnostics))
                }
                Section {
                    Button("Privacy Mode: Disable Camera, Location & Memory", role: .destructive) {
                        environment.configuration.flags.enableVisualAnalysis = false
                        environment.configuration.flags.enableLocationContext = false
                        environment.configuration.flags.enableLongTermMemory = false
                    }
                }
            }
            .navigationTitle("Settings")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Close") { dismiss() } }
            }
            .onAppear {
                tokenText = (try? KeychainStore().get(AppEnvironment.backendTokenKey)) ?? ""
                pcAgentTokenText = (try? KeychainStore().get(AppEnvironment.pcAgentTokenKey)) ?? ""
            }
        }
    }

    private func bindingFor(_ keyPath: WritableKeyPath<FeatureFlags, Bool>) -> Binding<Bool> {
        Binding(
            get: { environment.configuration.flags[keyPath: keyPath] },
            set: { environment.configuration.flags[keyPath: keyPath] = $0 }
        )
    }
}
