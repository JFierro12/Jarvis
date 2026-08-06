import Foundation
import UIKit
import Vision

/// Owns Vision-based hand-gesture classification against the glasses'
/// continuous video stream and forwards classified events to the paired
/// Mac. Vision integration lives here — not in `GestureClassifier`, which
/// stays a pure landmarks-in/gesture-out type — since decoding JPEG frames
/// and running `VNDetectHumanHandPoseRequest` needs UIKit/Vision.
///
/// Deliberately does NOT touch `MetaWearableDeviceClient.capturePhoto()`/
/// `attemptCapture()` — it only calls the already-existing
/// `startVideoStream()`/`frames()`/`stopVideoStream()` surface. Mutual
/// exclusion with point-in-time vision capture is enforced by
/// `AssistantCoordinator`, not here.
public final class LiveHandGestureController: HandGestureController, @unchecked Sendable {
    private let wearableClient: WearableDeviceClient
    private let gestureStreamClient: GestureStreamClient
    private let classifier: GestureClassifier
    private let streamConfiguration: VideoStreamConfiguration

    private let stateContinuation: AsyncStream<GestureControlState>.Continuation
    public let state: AsyncStream<GestureControlState>

    private var currentState: GestureControlState = .inactive
    private var frameTask: Task<Void, Never>?

    public init(
        wearableClient: WearableDeviceClient,
        gestureStreamClient: GestureStreamClient,
        classifier: GestureClassifier = GestureClassifier(),
        streamConfiguration: VideoStreamConfiguration = VideoStreamConfiguration(resolution: .low, frameRate: 15, maxDurationSeconds: 3600)
    ) {
        self.wearableClient = wearableClient
        self.gestureStreamClient = gestureStreamClient
        self.classifier = classifier
        self.streamConfiguration = streamConfiguration
        var continuation: AsyncStream<GestureControlState>.Continuation!
        self.state = AsyncStream { continuation = $0 }
        self.stateContinuation = continuation
        stateContinuation.yield(.inactive)
    }

    public func start() async throws {
        guard currentState != .active, currentState != .starting else {
            NSLog("[JarvisGesture] start() called while already \(currentState) — ignoring")
            return
        }
        currentState = .starting
        stateContinuation.yield(.starting)
        classifier.reset()

        do {
            try await gestureStreamClient.connect()
            try await wearableClient.startVideoStream(configuration: streamConfiguration)
        } catch {
            currentState = .error(error.localizedDescription)
            stateContinuation.yield(currentState)
            throw error
        }

        frameTask = Task { [weak self] in
            await self?.consumeFrames()
        }

        currentState = .active
        stateContinuation.yield(.active)
    }

    public func stop() async {
        guard currentState != .inactive else { return }
        frameTask?.cancel()
        frameTask = nil
        await wearableClient.stopVideoStream()
        await gestureStreamClient.disconnect()
        classifier.reset()
        currentState = .inactive
        stateContinuation.yield(.inactive)
    }

    private func consumeFrames() async {
        do {
            for try await frame in wearableClient.frames() {
                if Task.isCancelled { break }
                guard let cgImage = Self.decodeCGImage(from: frame.data),
                      let observation = Self.detectHands(in: cgImage, timestamp: frame.timestamp) else {
                    continue
                }
                let event = classifier.classify(observation)
                if event != .idle {
                    await gestureStreamClient.send(event)
                }
            }
        } catch {
            NSLog("[JarvisGesture] frame stream ended: \(error)")
        }
    }

    private static func decodeCGImage(from data: Data) -> CGImage? {
        UIImage(data: data)?.cgImage
    }

    private static func detectHands(in cgImage: CGImage, timestamp: TimeInterval) -> FrameHandsObservation? {
        let request = VNDetectHumanHandPoseRequest()
        request.maximumHandCount = 2
        let handler = VNImageRequestHandler(cgImage: cgImage, options: [:])
        do {
            try handler.perform([request])
        } catch {
            return nil
        }
        guard let results = request.results else {
            return FrameHandsObservation(hands: [], timestamp: timestamp)
        }
        let hands = results.compactMap { landmarks(from: $0) }
        return FrameHandsObservation(hands: hands, timestamp: timestamp)
    }

    private static let minimumJointConfidence: Float = 0.3

    private static func landmarks(from observation: VNHumanHandPoseObservation) -> HandLandmarks? {
        guard
            let wrist = try? observation.recognizedPoint(.wrist),
            let thumbTip = try? observation.recognizedPoint(.thumbTip),
            let indexTip = try? observation.recognizedPoint(.indexTip),
            let indexPIP = try? observation.recognizedPoint(.indexPIP),
            let middleTip = try? observation.recognizedPoint(.middleTip),
            let middlePIP = try? observation.recognizedPoint(.middlePIP),
            let ringTip = try? observation.recognizedPoint(.ringTip),
            let ringPIP = try? observation.recognizedPoint(.ringPIP),
            let littleTip = try? observation.recognizedPoint(.littleTip),
            let littlePIP = try? observation.recognizedPoint(.littlePIP)
        else { return nil }

        let points = [wrist, thumbTip, indexTip, indexPIP, middleTip, middlePIP, ringTip, ringPIP, littleTip, littlePIP]
        guard points.allSatisfy({ $0.confidence >= minimumJointConfidence }) else { return nil }

        return HandLandmarks(
            wrist: wrist.location,
            thumbTip: thumbTip.location,
            indexTip: indexTip.location,
            indexPIP: indexPIP.location,
            middleTip: middleTip.location,
            middlePIP: middlePIP.location,
            ringTip: ringTip.location,
            ringPIP: ringPIP.location,
            littleTip: littleTip.location,
            littlePIP: littlePIP.location
        )
    }
}
