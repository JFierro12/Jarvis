import Foundation

/// Wires every mock together into one ready-to-run `AssistantCoordinator` for
/// demo mode (spec §31/§32 Phase 2) — no backend, no credentials, no physical
/// glasses required. This is what a fresh checkout runs by default.
@MainActor
public enum DemoEnvironment {
    public static func makeCoordinator(demoImageData: Data = Data([0xFF, 0xD8, 0xFF])) -> AssistantCoordinator {
        let memoryRepository = InMemoryMemoryRepository()
        let pcAgent = MockPCAgentClient()
        let smartHome = MockSmartHomeClient()

        return AssistantCoordinator(
            wearableClient: MockWearableDeviceClient(demoImageData: demoImageData),
            speechToText: MockSpeechToTextProvider(),
            textToSpeech: MockTextToSpeechProvider(),
            visionProvider: MockVisionReasoningProvider(),
            languageProvider: MockLanguageReasoningProvider(),
            memoryRepository: memoryRepository,
            toolExecutor: MockToolExecutor(memoryRepository: memoryRepository, pcAgentClient: pcAgent, smartHomeClient: smartHome),
            grantedPermissions: ["calendar", "reminders", "pc_agent", "smart_home"]
        )
    }
}
