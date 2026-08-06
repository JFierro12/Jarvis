import CoreGraphics
import Foundation

/// Pure `HandLandmarks in -> GestureEvent out` classifier — no Vision/camera
/// dependency, so it's unit-testable with synthetic fixtures exactly like
/// `IntentRouter`/`PolicyEngine`. Vision hand-pose detection happens in
/// `LiveHandGestureController`, which converts its output into
/// `FrameHandsObservation` before calling `classify(_:)`.
///
/// One gesture "channel" (cursor/click/scroll/zoom) is active at a time,
/// determined per-frame by hand count and finger pose. Every delta-based
/// channel (cursor, scroll, zoom) needs a previous smoothed value to diff
/// against — the first frame after switching into a channel always returns
/// `.idle` (seeding the filter) rather than producing a garbage jump from a
/// stale value left over from a different channel.
public final class GestureClassifier {
    public struct Configuration: Sendable {
        /// A finger counts as "extended" when tip-to-wrist distance exceeds
        /// PIP-to-wrist distance by this ratio.
        public var curlExtendedRatio: Double
        /// Angle between index and middle fingertip vectors (from the
        /// wrist) at or above which the pose counts as a spread "V" (click)
        /// rather than fingers held together (scroll).
        public var vSpreadDegrees: Double
        /// Exponential-moving-average smoothing factor applied to
        /// cursor/scroll/zoom tracking, in (0, 1]. Higher = less smoothing.
        public var emaAlpha: Double
        public var cursorSensitivity: Double
        public var scrollSensitivity: Double
        public var zoomSensitivity: Double

        public init(
            curlExtendedRatio: Double = 1.15,
            vSpreadDegrees: Double = 18,
            emaAlpha: Double = 0.35,
            cursorSensitivity: Double = 1400,
            scrollSensitivity: Double = 900,
            zoomSensitivity: Double = 600
        ) {
            self.curlExtendedRatio = curlExtendedRatio
            self.vSpreadDegrees = vSpreadDegrees
            self.emaAlpha = emaAlpha
            self.cursorSensitivity = cursorSensitivity
            self.scrollSensitivity = scrollSensitivity
            self.zoomSensitivity = zoomSensitivity
        }
    }

    private let configuration: Configuration

    private var cursorXState: Double?
    private var cursorYState: Double?
    private var scrollYState: Double?
    private var zoomDistanceState: Double?
    private var isInVPose = false

    public init(configuration: Configuration = Configuration()) {
        self.configuration = configuration
    }

    /// Clears all filter/debounce state. Call when starting or stopping a
    /// browse session so a new session never inherits a stale delta origin.
    public func reset() {
        resetCursorState()
        resetScrollState()
        zoomDistanceState = nil
        isInVPose = false
    }

    public func classify(_ observation: FrameHandsObservation) -> GestureEvent {
        switch observation.hands.count {
        case 2:
            return classifyZoom(observation.hands[0], observation.hands[1])
        case 1:
            zoomDistanceState = nil
            return classifySingleHand(observation.hands[0])
        default:
            reset()
            return .idle
        }
    }

    // MARK: - Single-hand poses

    private func classifySingleHand(_ hand: HandLandmarks) -> GestureEvent {
        let indexExtended = isExtended(tip: hand.indexTip, pip: hand.indexPIP, wrist: hand.wrist)
        let middleExtended = isExtended(tip: hand.middleTip, pip: hand.middlePIP, wrist: hand.wrist)
        let ringExtended = isExtended(tip: hand.ringTip, pip: hand.ringPIP, wrist: hand.wrist)
        let littleExtended = isExtended(tip: hand.littleTip, pip: hand.littlePIP, wrist: hand.wrist)

        if indexExtended, !middleExtended, !ringExtended, !littleExtended {
            resetScrollState()
            isInVPose = false
            return classifyCursor(hand.indexTip)
        }

        if indexExtended, middleExtended, !ringExtended, !littleExtended {
            resetCursorState()
            let spreadDegrees = Self.angleDegrees(wrist: hand.wrist, tipA: hand.indexTip, tipB: hand.middleTip)
            if spreadDegrees >= configuration.vSpreadDegrees {
                resetScrollState()
                return classifyClick()
            } else {
                isInVPose = false
                let midpointY = Double((hand.indexTip.y + hand.middleTip.y) / 2)
                return classifyScroll(midpointY)
            }
        }

        // Ambiguous/unsupported single-hand pose — don't guess.
        reset()
        return .idle
    }

    private func classifyCursor(_ point: CGPoint) -> GestureEvent {
        let dx = smoothedDelta(Double(point.x), state: &cursorXState)
        let dy = smoothedDelta(Double(point.y), state: &cursorYState)
        guard let dx, let dy else { return .idle }
        // Vision's origin is bottom-left; screen coordinates grow downward.
        return .cursorDelta(dx: dx * configuration.cursorSensitivity, dy: -dy * configuration.cursorSensitivity)
    }

    private func classifyClick() -> GestureEvent {
        if isInVPose {
            return .idle
        }
        isInVPose = true
        return .click
    }

    private func classifyScroll(_ y: Double) -> GestureEvent {
        isInVPose = false
        guard let dy = smoothedDelta(y, state: &scrollYState) else { return .idle }
        // Hand rising (y increasing) scrolls content up, matching the
        // cursor's y-flip for the same bottom-left-origin reason.
        return .scroll(dy: -dy * configuration.scrollSensitivity)
    }

    // MARK: - Two-hand pose

    private func classifyZoom(_ handA: HandLandmarks, _ handB: HandLandmarks) -> GestureEvent {
        resetCursorState()
        resetScrollState()
        isInVPose = false
        let distance = Self.distance(handA.wrist, handB.wrist)
        guard let delta = smoothedDelta(distance, state: &zoomDistanceState) else { return .idle }
        // Distance increasing (hands moving apart) => zoom in, no sign flip.
        return .zoom(delta: delta * configuration.zoomSensitivity)
    }

    // MARK: - Shared helpers

    private func resetCursorState() {
        cursorXState = nil
        cursorYState = nil
    }

    private func resetScrollState() {
        scrollYState = nil
    }

    /// Applies EMA smoothing and returns the delta from the previous
    /// smoothed value. Returns `nil` on the first call for a given `state`
    /// (nothing to diff against yet — this seeds the filter instead of
    /// producing a jump from zero).
    private func smoothedDelta(_ value: Double, state: inout Double?) -> Double? {
        guard let previous = state else {
            state = value
            return nil
        }
        let smoothedValue = configuration.emaAlpha * value + (1 - configuration.emaAlpha) * previous
        let delta = smoothedValue - previous
        state = smoothedValue
        return delta
    }

    private func isExtended(tip: CGPoint, pip: CGPoint, wrist: CGPoint) -> Bool {
        let tipDistance = Self.distance(tip, wrist)
        let pipDistance = Self.distance(pip, wrist)
        guard pipDistance > 0 else { return false }
        return tipDistance > pipDistance * configuration.curlExtendedRatio
    }

    private static func distance(_ a: CGPoint, _ b: CGPoint) -> Double {
        let dx = Double(a.x - b.x)
        let dy = Double(a.y - b.y)
        return (dx * dx + dy * dy).squareRoot()
    }

    private static func angleDegrees(wrist: CGPoint, tipA: CGPoint, tipB: CGPoint) -> Double {
        let vectorA = CGPoint(x: tipA.x - wrist.x, y: tipA.y - wrist.y)
        let vectorB = CGPoint(x: tipB.x - wrist.x, y: tipB.y - wrist.y)
        let dot = Double(vectorA.x * vectorB.x + vectorA.y * vectorB.y)
        let magnitudeA = distance(wrist, tipA)
        let magnitudeB = distance(wrist, tipB)
        guard magnitudeA > 0, magnitudeB > 0 else { return 0 }
        let cosTheta = max(-1, min(1, dot / (magnitudeA * magnitudeB)))
        return acos(cosTheta) * 180 / .pi
    }
}
