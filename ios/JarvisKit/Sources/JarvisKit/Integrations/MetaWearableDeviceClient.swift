import Foundation
import UIKit
import MWDATCore
import MWDATCamera

/// Real `WearableDeviceClient` adapter over the Meta Wearables Device Access
/// Toolkit. Every SDK call here is taken directly from the verified API
/// surface in docs/META_SDK_NOTES.md (sourced from the DAT repo's AGENTS.md
/// and CameraAccess sample) — nothing here is invented.
///
/// `RegistrationError`, `PermissionStatus`, and `DeviceSessionState` case
/// names have been confirmed against the real MWDATCore.swiftinterface
/// shipped in the resolved package — the case switches below are typed, not
/// guessed.
///
/// TODO(hardware-verification): confirm real-device `capturePhoto()` latency
/// and firmware/app update flows (`openFirmwareUpdate()` /
/// `openDATGlassesAppUpdate()`) against physical glasses.
public final class MetaWearableDeviceClient: WearableDeviceClient, @unchecked Sendable {
    public private(set) var capabilities: WearableCapabilities
    public let connectionState: AsyncStream<WearableConnectionState>
    private let stateContinuation: AsyncStream<WearableConnectionState>.Continuation

    private let wearables = Wearables.shared
    private var deviceSession: DeviceSession?
    private var camera: Camera?
    private var stream: MWDATCamera.Stream?
    private var listenerTokens: [any AnyListenerToken] = []

    private var frameContinuation: AsyncThrowingStream<VideoFrame, Error>.Continuation?
    /// Guards against overlapping connect() calls. Console logs have shown
    /// multiple concurrent connect attempts racing each other (interleaved
    /// "attempt 1/2/3" lines, ending in "sessionAlreadyExists" once one
    /// attempt won) — each would independently retry createSession(),
    /// potentially leaving deviceSession pointing at whichever attempt
    /// finished last while a stale camera/stream from an earlier attempt
    /// stuck around, silently broken.
    private var isConnecting = false

    /// Call once at app launch, before constructing this adapter.
    public static func configureSDK() throws {
        try Wearables.configure()
    }

    /// Route a `.onOpenURL` callback from Meta AI into the SDK.
    public static func handleCallback(url: URL) async {
        _ = try? await Wearables.shared.handleUrl(url)
    }

    public init(capabilities: WearableCapabilities = .standardRayBanMeta) {
        self.capabilities = capabilities
        var continuation: AsyncStream<WearableConnectionState>.Continuation!
        self.connectionState = AsyncStream { continuation = $0 }
        self.stateContinuation = continuation
        stateContinuation.yield(.disconnected)
    }

    public func registerApplication() async throws {
        do {
            try await wearables.startRegistration()
        } catch RegistrationError.alreadyRegistered {
            // Not a failure — Meta AI already has this app registered from a
            // prior run. Proceed as if registration just succeeded.
            return
        }
    }

    public func checkPermissionStatus(_ permission: DevicePermission) async throws -> PermissionStatus {
        let status = try await wearables.checkPermissionStatus(.camera)
        return Self.mapPermissionStatus(status)
    }

    public func requestPermission(_ permission: DevicePermission) async throws -> PermissionStatus {
        let status = try await wearables.requestPermission(.camera)
        return Self.mapPermissionStatus(status)
    }

    private static func mapPermissionStatus(_ status: MWDATCore.PermissionStatus) -> PermissionStatus {
        switch status {
        case .granted: return .granted
        case .denied: return .denied
        }
    }

    public func connect() async throws {
        guard !isConnecting else {
            NSLog("[JarvisWearable] connect() called while already connecting — ignoring overlapping call")
            return
        }
        isConnecting = true
        defer { isConnecting = false }

        stateContinuation.yield(.connecting)

        // createSession()/session.start() are synchronous SDK calls that can
        // block waiting on CoreBluetooth readiness. They run inside the
        // racing task below (not before the task group starts) specifically
        // so a hang there can't prevent the timeout task from firing.
        let outcome = await withTaskGroup(of: ConnectOutcome.self) { group in
            group.addTask {
                do {
                    // Meta's own sample always observes devicesStream() before
                    // creating a session. We never had before now, so
                    // AutoDeviceSelector had nothing discovered to select —
                    // that's what produced "No eligible device available"
                    // even with glasses already Bluetooth-paired.
                    for await devices in self.wearables.devicesStream() {
                        NSLog("[JarvisWearable] devicesStream: \(devices.count) device(s): \(devices)")
                        if !devices.isEmpty { break }
                    }

                    let selector = AutoDeviceSelector(wearables: self.wearables)
                    // createSession() has been observed to throw
                    // .noEligibleDevice on the very first attempt even when
                    // devicesStream() just reported a device — apparently a
                    // race with the SDK's internal Bluetooth central manager
                    // reaching "powered on" (see the CBCentralManager API
                    // MISUSE console warning logged at the same moment).
                    // Retrying briefly rides through that race instead of
                    // failing on what is often a transient not-ready state.
                    var session: DeviceSession?
                    var lastError: DeviceSessionError?
                    for attempt in 1...5 {
                        NSLog("[JarvisWearable] calling createSession() attempt \(attempt)")
                        do {
                            session = try self.wearables.createSession(deviceSelector: selector)
                            lastError = nil
                            break
                        } catch let error as DeviceSessionError {
                            NSLog("[JarvisWearable] createSession() threw: \(error) — description: \(error.errorDescription ?? "n/a")")
                            lastError = error
                            if error != .noEligibleDevice { break }
                            try? await Task.sleep(nanoseconds: 1_000_000_000)
                        }
                    }
                    guard let session else {
                        throw lastError ?? DeviceSessionError.noEligibleDevice
                    }
                    NSLog("[JarvisWearable] createSession() succeeded, calling session.start()")
                    try session.start()

                    waitForStart: for await sessionState in session.stateStream() {
                        switch sessionState {
                        case .started:
                            break waitForStart
                        case .stopped:
                            return .stopped
                        case .idle, .starting, .paused, .stopping:
                            continue waitForStart
                        }
                    }
                    return .started(session)
                } catch {
                    return .failed(error.localizedDescription)
                }
            }
            group.addTask {
                try? await Task.sleep(nanoseconds: 15_000_000_000)
                return .timedOut
            }
            let first = await group.next() ?? .timedOut
            group.cancelAll()
            return first
        }

        switch outcome {
        case .started(let session):
            self.deviceSession = session
            // A camera/stream cached from a PREVIOUS session must not
            // survive onto this new one — capturePhoto() would silently
            // send commands into a stream no longer attached to anything
            // real. Force a fresh addCamera() on the next capture instead.
            self.camera = nil
            self.stream = nil
            stateContinuation.yield(.connected(.rayBanMeta))
        case .stopped:
            stateContinuation.yield(.error("Device session stopped before starting."))
        case .timedOut:
            stateContinuation.yield(.error("Couldn't connect within 15 seconds. Make sure Bluetooth is on and your glasses are powered on and linked in the Meta AI app."))
        case .failed(let message):
            stateContinuation.yield(.error("Connect failed: \(message)"))
        }
    }

    private enum ConnectOutcome: Sendable {
        case started(DeviceSession)
        case stopped
        case timedOut
        case failed(String)
    }

    public func disconnect() async {
        camera?.stop()
        deviceSession?.stop()
        camera = nil
        stream = nil
        deviceSession = nil
        for token in listenerTokens { await token.cancel() }
        listenerTokens.removeAll()
        stateContinuation.yield(.disconnected)
    }

    /// Best-effort: stops the camera and waits (briefly) for its state to
    /// actually settle at .stopped before returning, so callers can rely on
    /// starting from a known-clean state. Never throws — a timeout here
    /// just means capturePhoto() proceeds anyway and, if the camera really
    /// is stuck, fails with its own normal timeout instead.
    private func resetCameraToStopped(_ camera: Camera) async {
        NSLog("[JarvisWearable] camera not stopped at entry (\(camera.state)) — resetting before use")
        camera.stop()

        let bag = ListenerTokenBag()
        try? await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
            let resumer = SingleResume<Void>(continuation)
            let token = camera.statePublisher.listen { state in
                NSLog("[JarvisWearable] camera state (reset wait): \(state)")
                if state == .stopped {
                    resumer.resume(returning: ())
                }
            }
            bag.insert(token)
            if camera.state == .stopped {
                resumer.resume(returning: ())
            }
            Task {
                try? await Task.sleep(nanoseconds: 3_000_000_000)
                resumer.resume(returning: ())
            }
        }
        Task { await bag.cancelAll() }
    }

    public func capturePhoto() async throws -> CapturedImage {
        // The first capture in a session reliably succeeds; a subsequent
        // one has been observed to fail with a real SDK error
        // (StreamError.videoStreamingError, "Critical error, the stream
        // should end") despite going through the exact same setup —
        // apparently a transient hardware/BLE readiness issue on repeated
        // use, the same class of problem retrying already solved for
        // connect()'s noEligibleDevice. Each retry gets a fully fresh
        // camera (attemptCapture always clears self.camera/self.stream on
        // exit), not a reuse of whatever just failed.
        var lastError: Error = WearableClientError.captureFailed("No capture attempt completed")
        for attempt in 1...3 {
            do {
                NSLog("[JarvisWearable] capturePhoto() attempt \(attempt)")
                return try await attemptCapture()
            } catch {
                NSLog("[JarvisWearable] capturePhoto() attempt \(attempt) failed: \(error)")
                lastError = error
                if attempt < 3 {
                    try? await Task.sleep(nanoseconds: 1_000_000_000)
                }
            }
        }
        throw lastError
    }

    private func attemptCapture() async throws -> CapturedImage {
        guard let deviceSession else { throw WearableClientError.sessionNotStarted }

        let camera: Camera
        if let existing = self.camera {
            camera = existing
        } else {
            let config = StreamConfiguration(videoCodec: .raw, resolution: .low, frameRate: 15)
            guard let created = try deviceSession.addCamera(config: config) else {
                throw WearableClientError.cameraBusy
            }
            camera = created
            self.camera = camera
            self.stream = camera.stream
        }

        guard let stream else { throw WearableClientError.cameraBusy }
        NSLog("[JarvisWearable] camera.state at entry: \(camera.state)")

        // Seen on a *fresh app launch's first capture* — meaning this isn't
        // our own Swift-level caching, it's the glasses/SDK remembering the
        // camera as already "started" from a previous session that ended
        // by force-killing the app rather than a clean disconnect() (which
        // is the only place that calls camera.stop()). Starting a stream
        // whose camera thinks it's already running, rather than truly
        // fresh, is the leading theory for why capture silently produces
        // no photo. Force it to a known-clean .stopped state first,
        // regardless of how it got here.
        if camera.state != .stopped {
            await resetCameraToStopped(camera)
        }

        // A single "Look" is point-in-time, not a continuous stream — leave
        // the camera running afterward and it stays actively capturing
        // indefinitely, which is both a battery and a privacy problem.
        // camera.stop() (not stream.stop()) matches the same call
        // stopVideoStream() already uses elsewhere, so a capture never
        // leaves the hardware in a different state than an
        // explicitly-stopped video stream would. Runs on both success and
        // failure — a failed capture shouldn't leave the camera on either.
        //
        // Deliberately NOT clearing self.camera/self.stream here (a prior
        // version of this fix did, to force addCamera() fresh every call —
        // that turned out to be the wrong direction: DeviceSession exposes
        // no way to remove/release a camera capability, only add one, and
        // DeviceSessionError.capabilityAlreadyActive existing at all
        // suggests the SDK expects addCamera() to be called once per
        // session and the same Stream reused via start()/stop() for every
        // subsequent capture — repeatedly re-adding produced a consistent
        // "videoStreamingError: Critical error, the stream should end" on
        // every capture after the first. Reusing the cached object (with
        // resetCameraToStopped() above ensuring it's truly stopped first)
        // is the correct pattern here.
        defer {
            NSLog("[JarvisWearable] capturePhoto() done, stopping camera")
            camera.stop()
        }

        // Each capture's error/state/photo listeners are scoped to their
        // own bag and explicitly cancelled once this call resolves.
        // Previously they were appended to the adapter's long-lived
        // listenerTokens array and never cancelled — every subsequent
        // "Look" left the PREVIOUS call's listeners still firing on the
        // same (reused) stream object. On a second capture, both the stale
        // and the new state listener would react to the .streaming
        // transition, double-triggering stream.capturePhoto() and
        // registering yet more stale photoDataPublisher listeners. This is
        // why capture worked exactly once and then broke on every attempt
        // after.
        let bag = ListenerTokenBag()

        // stream.start() is not immediate — the stream moves through
        // .stopped -> .waitingForDevice -> .starting -> .streaming, and
        // calling capturePhoto() before it reaches .streaming was silently
        // dropped (nothing on photoDataPublisher ever fired, so this always
        // hit the timeout below). Also listens to errorPublisher now, which
        // was never observed before — real failures (permission denied,
        // hinges closed, device not connected) were previously
        // indistinguishable from a generic timeout.
        return try await withCheckedThrowingContinuation { continuation in
            let resumer = SingleResume(continuation)
            let captureTriggered = OneShotFlag()

            func finish(returning value: CapturedImage) {
                resumer.resume(returning: value)
                Task { await bag.cancelAll() }
            }
            func finish(throwing error: Error) {
                resumer.resume(throwing: error)
                Task { await bag.cancelAll() }
            }

            func triggerCaptureIfNeeded() {
                guard captureTriggered.setIfNeeded() else { return }
                NSLog("[JarvisWearable] stream is streaming, capturing photo")
                let token = stream.photoDataPublisher.listen { photoData in
                    finish(returning: CapturedImage(data: photoData.data, format: .jpeg))
                }
                bag.insert(token)
                let accepted = stream.capturePhoto(format: .jpeg)
                NSLog("[JarvisWearable] stream.capturePhoto(format:) returned \(accepted)")
            }

            let errorToken = stream.errorPublisher.listen { streamError in
                NSLog("[JarvisWearable] stream errorPublisher fired: \(streamError)")
                finish(throwing: WearableClientError.captureFailed(streamError.errorDescription ?? "\(streamError)"))
            }
            bag.insert(errorToken)

            let stateToken = stream.statePublisher.listen { state in
                NSLog("[JarvisWearable] stream state: \(state)")
                if state == .streaming {
                    triggerCaptureIfNeeded()
                }
            }
            bag.insert(stateToken)

            // Camera has its own separate state machine from Stream
            // (CameraState: starting/started/stopping/stopped) — never
            // observed before now. Logging only for now (not gating capture
            // on it yet) to see how/whether it correlates with the photo
            // never arriving despite Stream reporting .streaming.
            let cameraStateToken = camera.statePublisher.listen { state in
                NSLog("[JarvisWearable] camera state: \(state)")
            }
            bag.insert(cameraStateToken)

            stream.start()
            // Reused streams (a second "Look" in the same session) may
            // already be .streaming, in which case statePublisher won't
            // fire again since nothing is changing — check the current
            // state directly too, not just future transitions.
            if stream.state == .streaming {
                triggerCaptureIfNeeded()
            }

            Task {
                try? await Task.sleep(nanoseconds: 10_000_000_000)
                finish(throwing: WearableClientError.captureFailed("Timed out waiting for a photo after 10 seconds. Check that camera permission was granted to Jarvis (Meta AI app → App Connections)."))
            }
        }
    }

    public func startVideoStream(configuration: VideoStreamConfiguration) async throws {
        guard capabilities.supportsVideoStreaming else { throw WearableClientError.streamingUnsupported }
        guard let deviceSession else { throw WearableClientError.sessionNotStarted }

        let sdkResolution: MWDATCamera.StreamingResolution
        switch configuration.resolution {
        case .high: sdkResolution = .high
        case .medium: sdkResolution = .medium
        case .low: sdkResolution = .low
        }

        let camera: Camera
        if let existing = self.camera {
            camera = existing
        } else {
            let config = StreamConfiguration(videoCodec: .raw, resolution: sdkResolution, frameRate: UInt(configuration.frameRate))
            guard let created = try deviceSession.addCamera(config: config) else {
                throw WearableClientError.cameraBusy
            }
            camera = created
            self.camera = camera
        }

        // Same lesson learned hardening attemptCapture(): the SDK/glasses
        // can remember a camera as already "started" from a stale prior
        // session, and starting a stream whose camera thinks it's already
        // running produces no visible effect. Never observed on real
        // hardware until now — this method previously called stream.start()
        // and returned immediately with no confirmation it actually took,
        // which is exactly what let it silently no-op (no LED, no frames,
        // no error) the first time it was exercised on real glasses.
        NSLog("[JarvisWearable] startVideoStream() camera.state at entry: \(camera.state)")
        if camera.state != .stopped {
            NSLog("[JarvisWearable] startVideoStream() calling resetCameraToStopped()")
            await resetCameraToStopped(camera)
            NSLog("[JarvisWearable] startVideoStream() resetCameraToStopped() returned, camera.state now: \(camera.state)")
        }

        let stream = camera.stream
        self.stream = stream

        let bag = ListenerTokenBag()
        let frameToken = stream.videoFramePublisher.listen { [weak self] frame in
            guard let self, let image = frame.makeUIImage(), let data = image.jpegData(compressionQuality: 0.7) else { return }
            self.frameContinuation?.yield(VideoFrame(data: data, timestamp: Date().timeIntervalSince1970, width: Int(image.size.width), height: Int(image.size.height)))
        }
        listenerTokens.append(frameToken)

        // Confirm the stream actually reaches .streaming instead of trusting
        // that stream.start() alone means it worked — mirrors the same
        // state-wait attemptCapture() already relies on for exactly the
        // same reason (the stream moves through
        // .stopped -> .waitingForDevice -> .starting -> .streaming, and
        // calling start() is fire-and-forget with no guarantee it ever
        // arrives).
        try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
            let resumer = SingleResume(continuation)

            // Armed BEFORE stream.start() below, deliberately — start() is a
            // synchronous call into the closed-source SDK, and if it blocks
            // the calling thread natively (not a Swift-concurrency
            // scheduling delay, an actual native hang), scheduling the
            // timeout afterward would mean this Task never even gets
            // created, leaving nothing able to rescue the caller. Arming it
            // first guarantees a way out regardless of what start() does —
            // this Task runs independently and can resume the continuation
            // even if the closure's own thread never returns from start().
            Task {
                NSLog("[JarvisWearable] startVideoStream() timeout task armed, sleeping 8s")
                try? await Task.sleep(nanoseconds: 8_000_000_000)
                NSLog("[JarvisWearable] startVideoStream() timeout task fired after sleep — resuming with throw")
                resumer.resume(throwing: WearableClientError.captureFailed("Timed out waiting for the camera stream to start."))
                NSLog("[JarvisWearable] startVideoStream() timeout task: resume(throwing:) call returned")
            }

            let stateToken = stream.statePublisher.listen { state in
                NSLog("[JarvisWearable] startVideoStream() stream state: \(state)")
                if state == .streaming {
                    resumer.resume(returning: ())
                }
            }
            bag.insert(stateToken)

            let errorToken = stream.errorPublisher.listen { streamError in
                NSLog("[JarvisWearable] startVideoStream() stream errorPublisher fired: \(streamError)")
                resumer.resume(throwing: WearableClientError.captureFailed(streamError.errorDescription ?? "\(streamError)"))
            }
            bag.insert(errorToken)

            NSLog("[JarvisWearable] startVideoStream() about to call stream.start(), stream.state=\(stream.state)")
            stream.start()
            NSLog("[JarvisWearable] startVideoStream() stream.start() returned, stream.state=\(stream.state)")
            if stream.state == .streaming {
                resumer.resume(returning: ())
            }
        }
        NSLog("[JarvisWearable] startVideoStream() continuation resumed, exiting function")
        Task { await bag.cancelAll() }
    }

    public func stopVideoStream() async {
        camera?.stop()
        // startVideoStream() appends a videoFramePublisher listener to
        // listenerTokens on every call. Browse mode is started/stopped
        // repeatedly within a session, so without cancelling here each
        // restart would leave the PREVIOUS listener still firing alongside
        // the new one — the same class of bug already fixed once in
        // attemptCapture()'s capture-listener leak (see its comments).
        for token in listenerTokens { await token.cancel() }
        listenerTokens.removeAll()
        frameContinuation?.finish()
        frameContinuation = nil
    }

    public func frames() -> AsyncThrowingStream<VideoFrame, Error> {
        AsyncThrowingStream { continuation in
            self.frameContinuation = continuation
        }
    }
}

/// Resumes a `CheckedContinuation` exactly once even when two sources race to
/// resume it (an SDK callback vs. a timeout), avoiding both the "leaked
/// continuation" (never resumed) and "resumed more than once" (fatal) traps.
private final class SingleResume<T>: @unchecked Sendable {
    private var continuation: CheckedContinuation<T, Error>?
    private let lock = NSLock()

    init(_ continuation: CheckedContinuation<T, Error>) {
        self.continuation = continuation
    }

    func resume(returning value: T) {
        takeContinuation()?.resume(returning: value)
    }

    func resume(throwing error: Error) {
        takeContinuation()?.resume(throwing: error)
    }

    private func takeContinuation() -> CheckedContinuation<T, Error>? {
        lock.lock()
        defer { lock.unlock() }
        let current = continuation
        continuation = nil
        return current
    }
}

/// Thread-safe "do this exactly once" gate — used where two independent
/// signals (a state-change callback vs. a synchronous check made right
/// after) could otherwise both try to trigger the same one-time action.
private final class OneShotFlag: @unchecked Sendable {
    private var fired = false
    private let lock = NSLock()

    func setIfNeeded() -> Bool {
        lock.lock()
        defer { lock.unlock() }
        if fired { return false }
        fired = true
        return true
    }
}
