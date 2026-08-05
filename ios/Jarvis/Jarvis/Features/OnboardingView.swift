import SwiftUI

struct OnboardingView: View {
    let onComplete: () -> Void

    var body: some View {
        ZStack {
            Color.black.ignoresSafeArea()
            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    Text("Welcome to JARVIS").font(.largeTitle.bold()).foregroundStyle(.white)

                    notice(
                        title: "What the glasses can capture",
                        body: "When you explicitly ask, JARVIS can take a single photo through your Ray-Ban Meta glasses' camera. It does not stream continuous video by default and never captures a photo without an explicit request."
                    )
                    notice(
                        title: "What's processed on-device",
                        body: "Speech recognition and cached responses run on your iPhone when possible. Nothing is analyzed until you activate JARVIS."
                    )
                    notice(
                        title: "What's sent to a backend",
                        body: "If you configure a backend, only the specific image or text needed to answer your request is sent — never a continuous feed."
                    )
                    notice(
                        title: "What is stored",
                        body: "Photos are deleted immediately after analysis unless you say \"remember this.\" Saved memories stay on your device (and, if you enable sync, your configured backend) until you delete them."
                    )
                    notice(
                        title: "When the microphone is active",
                        body: "Only while a listening session is visibly active on screen — shown by a clear listening indicator."
                    )
                    notice(
                        title: "This app is not Meta's assistant",
                        body: "JARVIS is a separate, third-party app. It does not replace or control \"Hey Meta,\" and it cannot make your glasses respond to the word \"Jarvis\" at the firmware level."
                    )
                    notice(
                        title: "Your responsibility",
                        body: "You are responsible for complying with recording and privacy laws in your jurisdiction, including around bystanders."
                    )

                    Button(action: onComplete) {
                        Text("Continue").frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.borderedProminent)
                    .padding(.top)
                }
                .padding()
            }
        }
    }

    private func notice(title: String, body: String) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title).font(.headline).foregroundStyle(.white)
            Text(body).font(.subheadline).foregroundStyle(.secondary)
        }
    }
}
