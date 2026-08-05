import XCTest
@testable import JarvisKit

final class IntentRouterTests: XCTestCase {
    let router = IntentRouter()

    func testWakePrefixIsOptional() {
        let withPrefix = router.route("Jarvis, what am I looking at?")
        let withoutPrefix = router.route("what am I looking at?")
        XCTAssertEqual(withPrefix.intent, .analyzeScene)
        XCTAssertEqual(withoutPrefix.intent, .analyzeScene)
    }

    func testHeyJarvisPrefixIsStripped() {
        XCTAssertEqual(router.normalizingWakePrefix("Hey Jarvis, what's next"), "what's next")
    }

    func testAnalyzeSceneRequiresVisualContext() {
        let result = router.route("what am I looking at")
        XCTAssertTrue(result.requiresVisualContext)
        XCTAssertFalse(result.requiresLocation)
    }

    func testRememberSceneRequiresVisualAndLocation() {
        let result = router.route("remember where I put my keys")
        XCTAssertEqual(result.intent, .rememberScene)
        XCTAssertTrue(result.requiresVisualContext)
        XCTAssertTrue(result.requiresLocation)
    }

    func testControlHomeDeviceRequiresConfirmation() {
        let result = router.route("turn off my bedroom light")
        XCTAssertEqual(result.intent, .controlHomeDevice)
        XCTAssertTrue(result.requiresConfirmation)
    }

    func testCalendarQueryDoesNotRequireVisualContext() {
        let result = router.route("what is next on my calendar")
        XCTAssertEqual(result.intent, .getCalendar)
        XCTAssertFalse(result.requiresVisualContext)
    }

    func testOpenEndedFallsThroughWithLowConfidence() {
        let result = router.route("tell me something interesting about black holes")
        XCTAssertEqual(result.intent, .openEnded)
        XCTAssertLessThan(result.confidence, intentConfidenceFloor)
    }

    func testConfirmAndRejectRoute() {
        XCTAssertEqual(router.route("confirm").intent, .confirm)
        XCTAssertEqual(router.route("no").intent, .reject)
    }
}
