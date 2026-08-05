import XCTest
@testable import JarvisKit

final class FrameSelectorTests: XCTestCase {
    func testFirstFrameIsSelected() {
        let selector = FrameSelector()
        let frame = VideoFrame(data: Data([1, 2, 3]), timestamp: 0, width: 100, height: 100, sharpnessScore: 0.9)
        XCTAssertTrue(selector.shouldSelect(frame, now: Date()))
    }

    func testMinimumIntervalIsEnforced() {
        let selector = FrameSelector(configuration: .init(minimumInterval: 2.0, maxUploadsPerMinute: 10, sharpnessFloor: 0))
        let now = Date()
        let frame1 = VideoFrame(data: Data([1]), timestamp: 0, width: 1, height: 1)
        let frame2 = VideoFrame(data: Data([2]), timestamp: 0, width: 1, height: 1)
        XCTAssertTrue(selector.shouldSelect(frame1, now: now))
        XCTAssertFalse(selector.shouldSelect(frame2, now: now.addingTimeInterval(0.5)))
        XCTAssertTrue(selector.shouldSelect(frame2, now: now.addingTimeInterval(2.1)))
    }

    func testDuplicateFramesAreSuppressed() {
        let selector = FrameSelector(configuration: .init(minimumInterval: 0, maxUploadsPerMinute: 10, sharpnessFloor: 0))
        let now = Date()
        let data = Data([9, 9, 9])
        XCTAssertTrue(selector.shouldSelect(VideoFrame(data: data, timestamp: 0, width: 1, height: 1), now: now))
        XCTAssertFalse(selector.shouldSelect(VideoFrame(data: data, timestamp: 0, width: 1, height: 1), now: now.addingTimeInterval(5)))
    }

    func testLowSharpnessFramesAreRejected() {
        let selector = FrameSelector(configuration: .init(minimumInterval: 0, maxUploadsPerMinute: 10, sharpnessFloor: 0.5))
        let frame = VideoFrame(data: Data([1]), timestamp: 0, width: 1, height: 1, sharpnessScore: 0.1)
        XCTAssertFalse(selector.shouldSelect(frame, now: Date()))
    }

    func testMaxUploadRateIsEnforced() {
        let selector = FrameSelector(configuration: .init(minimumInterval: 0, maxUploadsPerMinute: 2, sharpnessFloor: 0))
        let now = Date()
        XCTAssertTrue(selector.shouldSelect(VideoFrame(data: Data([1]), timestamp: 0, width: 1, height: 1), now: now))
        XCTAssertTrue(selector.shouldSelect(VideoFrame(data: Data([2]), timestamp: 0, width: 1, height: 1), now: now.addingTimeInterval(0.1)))
        XCTAssertFalse(selector.shouldSelect(VideoFrame(data: Data([3]), timestamp: 0, width: 1, height: 1), now: now.addingTimeInterval(0.2)))
    }
}
