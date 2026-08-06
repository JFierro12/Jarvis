import CoreGraphics
import Foundation

/// Landmark points for a single detected hand, in Vision's normalized
/// coordinate space (origin bottom-left, 0...1 on both axes). Only the
/// joints needed for finger-curl/spread classification are carried — Vision
/// reports more (e.g. DIP joints) but they aren't needed here.
public struct HandLandmarks: Sendable, Equatable {
    public let wrist: CGPoint
    public let thumbTip: CGPoint
    public let indexTip: CGPoint
    public let indexPIP: CGPoint
    public let middleTip: CGPoint
    public let middlePIP: CGPoint
    public let ringTip: CGPoint
    public let ringPIP: CGPoint
    public let littleTip: CGPoint
    public let littlePIP: CGPoint

    public init(
        wrist: CGPoint,
        thumbTip: CGPoint,
        indexTip: CGPoint,
        indexPIP: CGPoint,
        middleTip: CGPoint,
        middlePIP: CGPoint,
        ringTip: CGPoint,
        ringPIP: CGPoint,
        littleTip: CGPoint,
        littlePIP: CGPoint
    ) {
        self.wrist = wrist
        self.thumbTip = thumbTip
        self.indexTip = indexTip
        self.indexPIP = indexPIP
        self.middleTip = middleTip
        self.middlePIP = middlePIP
        self.ringTip = ringTip
        self.ringPIP = ringPIP
        self.littleTip = littleTip
        self.littlePIP = littlePIP
    }
}

/// Everything Vision saw in one camera frame — 0, 1, or 2 hands.
public struct FrameHandsObservation: Sendable, Equatable {
    public let hands: [HandLandmarks]
    public let timestamp: TimeInterval

    public init(hands: [HandLandmarks], timestamp: TimeInterval) {
        self.hands = hands
        self.timestamp = timestamp
    }
}

/// A classified gesture for one frame. Doubles as the phone->pc-agent wire
/// message shape (see docs — the `type` discriminator matches directly) so
/// there's no separate translation layer between classification and the
/// network. `.idle` is a valid classification result but is never sent.
public enum GestureEvent: Equatable, Sendable {
    case cursorDelta(dx: Double, dy: Double)
    case click
    case scroll(dy: Double)
    case zoom(delta: Double)
    case idle
}

extension GestureEvent: Codable {
    private enum CodingKeys: String, CodingKey {
        case type, dx, dy, delta
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        switch self {
        case .cursorDelta(let dx, let dy):
            try container.encode("cursor_delta", forKey: .type)
            try container.encode(dx, forKey: .dx)
            try container.encode(dy, forKey: .dy)
        case .click:
            try container.encode("click", forKey: .type)
        case .scroll(let dy):
            try container.encode("scroll", forKey: .type)
            try container.encode(dy, forKey: .dy)
        case .zoom(let delta):
            try container.encode("zoom", forKey: .type)
            try container.encode(delta, forKey: .delta)
        case .idle:
            try container.encode("idle", forKey: .type)
        }
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        let type = try container.decode(String.self, forKey: .type)
        switch type {
        case "cursor_delta":
            self = .cursorDelta(dx: try container.decode(Double.self, forKey: .dx), dy: try container.decode(Double.self, forKey: .dy))
        case "click":
            self = .click
        case "scroll":
            self = .scroll(dy: try container.decode(Double.self, forKey: .dy))
        case "zoom":
            self = .zoom(delta: try container.decode(Double.self, forKey: .delta))
        default:
            self = .idle
        }
    }
}
