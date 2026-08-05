import Foundation

/// Used by the diagnostics screen's "copy diagnostics" action (spec §29) to
/// guarantee nothing resembling a token/key/secret ever hits the pasteboard.
public enum SecretRedaction {
    private static let patterns: [String] = [
        #"(?i)(bearer)\s+[a-z0-9._-]+"#,
        #"(?i)(api[_-]?key|token|secret|password)\s*[:=]\s*\S+"#
    ]

    public static func redacted(_ text: String) -> String {
        var result = text
        for pattern in patterns {
            guard let regex = try? NSRegularExpression(pattern: pattern) else { continue }
            let range = NSRange(result.startIndex..., in: result)
            result = regex.stringByReplacingMatches(in: result, range: range, withTemplate: "$1 [REDACTED]")
        }
        return result
    }
}
