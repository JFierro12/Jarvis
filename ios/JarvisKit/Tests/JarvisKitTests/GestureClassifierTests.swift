import CoreGraphics
import XCTest
@testable import JarvisKit

/// Synthetic landmark fixtures only — no Vision, no camera, no SDK. Wrist is
/// placed at the origin for simplicity; the classifier only cares about
/// relative geometry, not absolute image position.
final class GestureClassifierTests: XCTestCase {
    private let wrist = CGPoint(x: 0, y: 0)

    private func observation(_ hands: [HandLandmarks]) -> FrameHandsObservation {
        FrameHandsObservation(hands: hands, timestamp: 0)
    }

    /// One hand with the index finger at `indexTip`/`indexPIP` extended, the
    /// middle finger at `middleTip`/`middlePIP` positioned per the caller
    /// (extended or curled), and ring/little always curled.
    private func hand(
        indexTip: CGPoint, indexPIP: CGPoint = CGPoint(x: -0.02, y: 0.1),
        middleTip: CGPoint, middlePIP: CGPoint = CGPoint(x: 0.02, y: 0.1),
        middleCurled: Bool = false
    ) -> HandLandmarks {
        HandLandmarks(
            wrist: wrist,
            thumbTip: CGPoint(x: 0.05, y: 0.02),
            indexTip: indexTip,
            indexPIP: indexPIP,
            middleTip: middleCurled ? CGPoint(x: 0.02, y: 0.03) : middleTip,
            middlePIP: middlePIP,
            ringTip: CGPoint(x: 0.03, y: 0.03),
            ringPIP: CGPoint(x: 0.05, y: 0.05),
            littleTip: CGPoint(x: 0.02, y: 0.02),
            littlePIP: CGPoint(x: 0.04, y: 0.04)
        )
    }

    private func fist() -> HandLandmarks {
        HandLandmarks(
            wrist: wrist,
            thumbTip: CGPoint(x: 0.02, y: 0.01),
            indexTip: CGPoint(x: 0.02, y: 0.02), indexPIP: CGPoint(x: 0.05, y: 0.05),
            middleTip: CGPoint(x: 0.02, y: 0.02), middlePIP: CGPoint(x: 0.05, y: 0.05),
            ringTip: CGPoint(x: 0.02, y: 0.02), ringPIP: CGPoint(x: 0.05, y: 0.05),
            littleTip: CGPoint(x: 0.02, y: 0.02), littlePIP: CGPoint(x: 0.05, y: 0.05)
        )
    }

    /// Only `wrist` matters for the two-hand zoom classification (2-hand
    /// frames always classify as zoom regardless of finger pose) — other
    /// landmarks are unused placeholders.
    private func handAt(wrist: CGPoint) -> HandLandmarks {
        HandLandmarks(
            wrist: wrist,
            thumbTip: wrist, indexTip: wrist, indexPIP: wrist,
            middleTip: wrist, middlePIP: wrist, ringTip: wrist, ringPIP: wrist,
            littleTip: wrist, littlePIP: wrist
        )
    }

    // MARK: - Cursor (single extended index finger)

    func testPointingProducesCursorDeltaProportionalToDrift() {
        let classifier = GestureClassifier()
        let first = hand(indexTip: CGPoint(x: 0, y: 0.2), middleTip: CGPoint(x: 0, y: 0.2), middleCurled: true)
        XCTAssertEqual(classifier.classify(observation([first])), .idle, "first frame only seeds the filter")

        let secondSmallDrift = hand(indexTip: CGPoint(x: 0.01, y: 0.2), middleTip: CGPoint(x: 0, y: 0.2), middleCurled: true)
        guard case .cursorDelta(let smallDx, _) = classifier.classify(observation([secondSmallDrift])) else {
            return XCTFail("expected cursorDelta")
        }

        let classifier2 = GestureClassifier()
        _ = classifier2.classify(observation([first]))
        let secondBigDrift = hand(indexTip: CGPoint(x: 0.05, y: 0.2), middleTip: CGPoint(x: 0, y: 0.2), middleCurled: true)
        guard case .cursorDelta(let bigDx, _) = classifier2.classify(observation([secondBigDrift])) else {
            return XCTFail("expected cursorDelta")
        }

        XCTAssertGreaterThan(smallDx, 0, "moving right should produce a positive dx")
        XCTAssertGreaterThan(bigDx, smallDx, "a bigger hand movement should produce a proportionally bigger delta (relative mapping)")
    }

    func testRecenteringHandDoesNotSnapCursor() {
        // Relative/trackpad-style mapping means returning the hand to its
        // starting position should NOT jump the cursor back — each delta
        // only reflects the most recent movement, there's no absolute
        // "home" position it's being corrected against.
        let classifier = GestureClassifier()
        let start = hand(indexTip: CGPoint(x: 0, y: 0.2), middleTip: CGPoint(x: 0, y: 0.2), middleCurled: true)
        _ = classifier.classify(observation([start]))
        let moved = hand(indexTip: CGPoint(x: 0.1, y: 0.2), middleTip: CGPoint(x: 0, y: 0.2), middleCurled: true)
        guard case .cursorDelta(let outboundDx, _) = classifier.classify(observation([moved])) else {
            return XCTFail("expected cursorDelta")
        }
        XCTAssertGreaterThan(outboundDx, 0)
    }

    // MARK: - Click (V / peace sign)

    func testVPoseFiresClickOnceOnRisingEdgeThenIdleWhileHeld() {
        let classifier = GestureClassifier()
        let vPose = hand(indexTip: CGPoint(x: -0.08, y: 0.25), middleTip: CGPoint(x: 0.08, y: 0.25))
        XCTAssertEqual(classifier.classify(observation([vPose])), .click)
        XCTAssertEqual(classifier.classify(observation([vPose])), .idle, "holding the pose must not repeat-click")
        XCTAssertEqual(classifier.classify(observation([vPose])), .idle)
    }

    func testClickRearmsAfterReleasingPose() {
        let classifier = GestureClassifier()
        let vPose = hand(indexTip: CGPoint(x: -0.08, y: 0.25), middleTip: CGPoint(x: 0.08, y: 0.25))
        XCTAssertEqual(classifier.classify(observation([vPose])), .click)
        XCTAssertEqual(classifier.classify(observation([fist()])), .idle, "releasing to a fist resets debounce")
        XCTAssertEqual(classifier.classify(observation([vPose])), .click, "re-entering the pose fires again")
    }

    // MARK: - Scroll (index + middle together)

    func testFingersTogetherMovingUpScrollsNegativeMovingDownScrollsPositive() {
        let classifierUp = GestureClassifier()
        let low = hand(indexTip: CGPoint(x: -0.01, y: 0.2), middleTip: CGPoint(x: 0.01, y: 0.2))
        let high = hand(indexTip: CGPoint(x: -0.01, y: 0.3), middleTip: CGPoint(x: 0.01, y: 0.3))
        _ = classifierUp.classify(observation([low]))
        guard case .scroll(let dyRising) = classifierUp.classify(observation([high])) else {
            return XCTFail("expected scroll")
        }
        XCTAssertLessThan(dyRising, 0, "hand rising should scroll content up (negative dy)")

        let classifierDown = GestureClassifier()
        _ = classifierDown.classify(observation([high]))
        guard case .scroll(let dyFalling) = classifierDown.classify(observation([low])) else {
            return XCTFail("expected scroll")
        }
        XCTAssertGreaterThan(dyFalling, 0, "hand falling should scroll content down (positive dy)")
    }

    // MARK: - Zoom (two hands)

    func testHandsMovingApartZoomsInHandsMovingTogetherZoomsOut() {
        let classifierApart = GestureClassifier()
        let close = [handAt(wrist: CGPoint(x: -0.1, y: 0)), handAt(wrist: CGPoint(x: 0.1, y: 0))]
        let far = [handAt(wrist: CGPoint(x: -0.3, y: 0)), handAt(wrist: CGPoint(x: 0.3, y: 0))]
        _ = classifierApart.classify(observation(close))
        guard case .zoom(let deltaApart) = classifierApart.classify(observation(far)) else {
            return XCTFail("expected zoom")
        }
        XCTAssertGreaterThan(deltaApart, 0, "hands moving apart should zoom in (positive delta)")

        let classifierTogether = GestureClassifier()
        _ = classifierTogether.classify(observation(far))
        guard case .zoom(let deltaTogether) = classifierTogether.classify(observation(close)) else {
            return XCTFail("expected zoom")
        }
        XCTAssertLessThan(deltaTogether, 0, "hands moving together should zoom out (negative delta)")
    }

    // MARK: - Idle fallbacks

    func testNoHandsIsIdle() {
        let classifier = GestureClassifier()
        XCTAssertEqual(classifier.classify(observation([])), .idle)
    }

    func testFistIsIdle() {
        let classifier = GestureClassifier()
        XCTAssertEqual(classifier.classify(observation([fist()])), .idle)
    }

    func testResetClearsDebounceAndFilterState() {
        let classifier = GestureClassifier()
        let vPose = hand(indexTip: CGPoint(x: -0.08, y: 0.25), middleTip: CGPoint(x: 0.08, y: 0.25))
        XCTAssertEqual(classifier.classify(observation([vPose])), .click)
        classifier.reset()
        XCTAssertEqual(classifier.classify(observation([vPose])), .click, "reset() re-arms the click debounce")
    }
}
