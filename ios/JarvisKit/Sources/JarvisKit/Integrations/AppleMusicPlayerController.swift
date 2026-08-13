import Foundation
import MediaPlayer

/// Real `MusicPlayerController` backed by Apple's `MediaPlayer` framework,
/// driving the same system-wide player the Music app uses
/// (`systemMusicPlayer`, not `applicationMusicPlayer`) so playback continues
/// after JARVIS is backgrounded.
public final class AppleMusicPlayerController: MusicPlayerController, @unchecked Sendable {
    private let player = MPMusicPlayerController.systemMusicPlayer

    public init() {}

    public func shufflePlaylist(named name: String) async throws {
        let status = await requestAuthorizationIfNeeded()
        guard status == .authorized else {
            throw MusicPlayerError.permissionDenied
        }
        guard let playlist = findPlaylist(named: name) else {
            throw MusicPlayerError.playlistNotFound(name)
        }
        player.setQueue(with: playlist)
        player.shuffleMode = .songs
        player.play()
    }

    private func requestAuthorizationIfNeeded() async -> MPMediaLibraryAuthorizationStatus {
        let current = MPMediaLibrary.authorizationStatus()
        guard current == .notDetermined else { return current }
        return await withCheckedContinuation { continuation in
            MPMediaLibrary.requestAuthorization { status in
                continuation.resume(returning: status)
            }
        }
    }

    private func findPlaylist(named name: String) -> MPMediaPlaylist? {
        guard let collections = MPMediaQuery.playlists().collections else { return nil }
        let normalizedTarget = name.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        for collection in collections {
            guard let playlist = collection as? MPMediaPlaylist else { continue }
            let playlistName = (playlist.value(forProperty: MPMediaPlaylistPropertyName) as? String)?
                .trimmingCharacters(in: .whitespacesAndNewlines)
                .lowercased()
            if playlistName == normalizedTarget {
                return playlist
            }
        }
        return nil
    }
}
