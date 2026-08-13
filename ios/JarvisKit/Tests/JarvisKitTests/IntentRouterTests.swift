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

    func testBrowseStartsGestureMode() {
        XCTAssertEqual(router.route("browse").intent, .startBrowseMode)
        XCTAssertEqual(router.route("Jarvis, browse").intent, .startBrowseMode)
    }

    func testStopBrowsingDoesNotCollideWithStartOrCancel() {
        XCTAssertEqual(router.route("stop browsing").intent, .stopBrowseMode)
        XCTAssertEqual(router.route("exit browse mode").intent, .stopBrowseMode)
    }

    func testIdentifyCoverageRequiresVisualContext() {
        let result = router.route("identify the coverage")
        XCTAssertEqual(result.intent, .identifyCoverage)
        XCTAssertTrue(result.requiresVisualContext)
    }

    func testIdentifyCoverageDoesNotCollideWithAnalyzeScene() {
        XCTAssertEqual(router.route("what coverage is this").intent, .identifyCoverage)
        XCTAssertEqual(router.route("what am I looking at").intent, .analyzeScene)
    }

    func testFootballAnalysisModeStartAndStop() {
        XCTAssertEqual(router.route("football mode").intent, .startFootballAnalysisMode)
        XCTAssertEqual(router.route("start football analysis").intent, .startFootballAnalysisMode)
        XCTAssertEqual(router.route("stop football mode").intent, .stopFootballAnalysisMode)
        XCTAssertEqual(router.route("exit football mode").intent, .stopFootballAnalysisMode)
    }

    func testGeniusPlaylistTrigger() {
        XCTAssertEqual(router.route("Let's remind everyone who the genius in the room is").intent, .playGeniusPlaylist)
        XCTAssertEqual(router.route("who's the genius in the room").intent, .playGeniusPlaylist)
    }
}
