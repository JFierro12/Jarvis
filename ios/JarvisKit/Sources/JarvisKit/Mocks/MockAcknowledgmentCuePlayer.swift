import Foundation

public final class MockAcknowledgmentCuePlayer: AcknowledgmentCuePlayer, @unchecked Sendable {
    public private(set) var playCount = 0

    public init() {}

    public func play() async {
        playCount += 1
    }
}
