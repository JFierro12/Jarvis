import Foundation

/// Decides which streamed frames are worth spending a cloud vision call on.
/// For the MVP, explicit photo capture is preferred over continuous
/// streaming (spec §8) — this selector exists for when `enableVideoStreaming`
/// is turned on, so streaming never becomes "upload every frame."
public final class FrameSelector: Sendable {
    public struct Configuration: Sendable {
        public var minimumInterval: TimeInterval
        public var maxUploadsPerMinute: Int
        public var sharpnessFloor: Double

        public init(minimumInterval: TimeInterval = 2.0, maxUploadsPerMinute: Int = 6, sharpnessFloor: Double = 0.15) {
            self.minimumInterval = minimumInterval
            self.maxUploadsPerMinute = maxUploadsPerMinute
            self.sharpnessFloor = sharpnessFloor
        }
    }

    private let configuration: Configuration
    private let state = OSAllocatedLock(initialState: SelectorState())

    private struct SelectorState {
        var lastSelectedAt: Date?
        var lastFrameData: Data?
        var uploadsInWindow: [Date] = []
    }

    public init(configuration: Configuration = Configuration()) {
        self.configuration = configuration
    }

    /// Returns `true` if this frame should be sent onward. Applies minimum
    /// interval, duplicate suppression, sharpness floor, and a rolling
    /// max-upload-rate cap. Explicit user requests should bypass this and
    /// call `capturePhoto()` directly rather than going through the selector.
    public func shouldSelect(_ frame: VideoFrame, now: Date = Date()) -> Bool {
        state.withLock { s in
            if let last = s.lastSelectedAt, now.timeIntervalSince(last) < configuration.minimumInterval {
                return false
            }
            if let sharpness = frame.sharpnessScore, sharpness < configuration.sharpnessFloor {
                return false
            }
            if let lastData = s.lastFrameData, lastData == frame.data {
                return false
            }
            s.uploadsInWindow = s.uploadsInWindow.filter { now.timeIntervalSince($0) < 60 }
            guard s.uploadsInWindow.count < configuration.maxUploadsPerMinute else {
                return false
            }
            s.lastSelectedAt = now
            s.lastFrameData = frame.data
            s.uploadsInWindow.append(now)
            return true
        }
    }
}

/// Minimal cross-platform lock wrapper so this file has no availability
/// constraints tighter than the package's iOS 17 floor.
final class OSAllocatedLock<State>: @unchecked Sendable {
    private var state: State
    private let lock = NSLock()

    init(initialState: State) {
        self.state = initialState
    }

    func withLock<R>(_ body: (inout State) -> R) -> R {
        lock.lock()
        defer { lock.unlock() }
        return body(&state)
    }
}
