import XCTest

/// UI smoke tests. Requires a simulator to run (`xcodebuild test -scheme
/// Jarvis`); not exercised by `swift test` / JarvisKit's package tests.
final class JarvisUITests: XCTestCase {
    func testOnboardingContinueButtonReachesMainScreen() {
        let app = XCUIApplication()
        app.launchArguments += ["-hasCompletedOnboarding", "0"]
        app.launch()

        let continueButton = app.buttons["Continue"]
        XCTAssertTrue(continueButton.waitForExistence(timeout: 5), "Onboarding Continue button never appeared")
        continueButton.tap()

        XCTAssertTrue(app.staticTexts["JARVIS"].waitForExistence(timeout: 5), "Main screen title never appeared after completing onboarding")
    }

    func testStopButtonIsAlwaysReachableFromMainScreen() {
        let app = XCUIApplication()
        app.launchArguments += ["-hasCompletedOnboarding", "1"]
        app.launch()

        XCTAssertTrue(app.buttons["Stop"].waitForExistence(timeout: 5))
    }
}
