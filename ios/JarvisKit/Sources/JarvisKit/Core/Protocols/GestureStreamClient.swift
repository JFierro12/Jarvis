import Foundation

public enum GestureStreamError: Error, Equatable {
    case connectionFailed(String)
    case unauthorized
}

/// Real-time, one-way (phone -> Mac) channel for classified gesture events.
/// The first streaming/WebSocket precedent in this codebase — deliberately
/// minimal for v1: no reconnect loop, no heartbeat. If the connection drops,
/// gesture control simply stops (`HandGestureController` surfaces this via
/// its `state` stream) and the user says "browse" again.
public protocol GestureStreamClient: AnyObject, Sendable {
    func connect() async throws
    func send(_ event: GestureEvent) async
    func disconnect() async
}
