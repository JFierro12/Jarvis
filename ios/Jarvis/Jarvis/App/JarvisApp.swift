import SwiftUI
import JarvisKit

@main
struct JarvisApp: App {
    @StateObject private var environment = AppEnvironment.makeFromStoredPreferences()
    @AppStorage("hasCompletedOnboarding") private var hasCompletedOnboarding = false

    init() {
        // TODO(hardware-verification): call MetaWearableDeviceClient.configureSDK()
        // here once the app is registered with the Wearables Developer Center
        // and Info.plist carries real MWDAT keys. Demo mode does not touch the
        // SDK at all, so this is intentionally not called yet.
    }

    var body: some Scene {
        WindowGroup {
            Group {
                if hasCompletedOnboarding {
                    MainView(coordinator: environment.coordinator)
                } else {
                    OnboardingView(onComplete: { hasCompletedOnboarding = true })
                }
            }
            .environmentObject(environment)
            .preferredColorScheme(.dark)
            .onOpenURL { url in
                Task {
                    await MetaWearableDeviceClient.handleCallback(url: url)
                }
            }
        }
    }
}
