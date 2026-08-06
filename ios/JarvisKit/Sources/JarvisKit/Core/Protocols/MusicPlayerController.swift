import Foundation

public enum MusicPlayerError: Error, Equatable {
    case permissionDenied
    case playlistNotFound(String)
    case playbackFailed(String)
}

/// Deliberately minimal — only what "shuffle this playlist" needs. Not a
/// general play/pause/skip transport; add to this only when a command
/// actually needs it.
public protocol MusicPlayerController: AnyObject, Sendable {
    func shufflePlaylist(named name: String) async throws
}
