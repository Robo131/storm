import SwiftUI
import WebKit

@main
struct StormScopeApp: App {
    var body: some Scene {
        WindowGroup {
            WebView()
                .ignoresSafeArea()
                .background(Color(red: 0.043, green: 0.051, blue: 0.063))
                .preferredColorScheme(.dark)
        }
    }
}

struct WebView: UIViewRepresentable {
    func makeUIView(context: Context) -> WKWebView {
        let view = WKWebView(frame: .zero)
        view.isOpaque = false
        view.backgroundColor = UIColor(red: 0.043, green: 0.051, blue: 0.063, alpha: 1)
        view.scrollView.backgroundColor = view.backgroundColor
        view.scrollView.bounces = false
        view.scrollView.contentInsetAdjustmentBehavior = .never
        if let url = Bundle.main.url(forResource: "index", withExtension: "html", subdirectory: "public") {
            view.loadFileURL(url, allowingReadAccessTo: url.deletingLastPathComponent())
        }
        return view
    }
    func updateUIView(_ uiView: WKWebView, context: Context) {}
}
