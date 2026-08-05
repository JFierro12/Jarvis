import XCTest
@testable import Jarvis
import JarvisKit

/// App-target tests: anything that depends on `AppEnvironment`'s wiring
/// itself, as opposed to `JarvisKit`'s pure business logic (see
/// ios/JarvisKit/Tests, which is the primary, hardware-independent test
/// suite and covers the state machine, intent routing, memory, and policy
/// engine in depth).
final class JarvisTests: XCTestCase {
    @MainActor
    func testDemoEnvironmentStartsIdle() {
        let environment = AppEnvironment.makeDemo()
        XCTAssertEqual(environment.coordinator.state, .idle)
    }

    @MainActor
    func testDemoEnvironmentDiagnosticsSnapshotReportsNoNativeVoiceInvocation() {
        let environment = AppEnvironment.makeDemo()
        XCTAssertFalse(environment.diagnosticsSnapshot().supportsNativeVoiceInvocation)
    }
}
