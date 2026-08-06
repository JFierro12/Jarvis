import Foundation

public final class MockMusicPlayerController: MusicPlayerController, @unchecked Sendable {
    public private(set) var shuffledPlaylistNames: [String] = []
    public var scriptedError: MusicPlayerError?

    public init(scriptedError: MusicPlayerError? = nil) {
        self.scriptedError = scriptedError
    }

    public func shufflePlaylist(named name: String) async throws {
        if let scriptedError {
            throw scriptedError
        }
        shuffledPlaylistNames.append(name)
    }
}
