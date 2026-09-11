import Foundation
import SafariServices

/// Safari routes native messages to this appex, not to Chrome's stdio host.
/// The companion is launched by the user; this sandboxed extension only checks
/// the public health endpoint. It never reads cookies, tokens or archive files.
class SafariWebExtensionHandler: NSObject, NSExtensionRequestHandling {
    private let baseURL = "http://127.0.0.1:18765"

    func beginRequest(with context: NSExtensionContext) {
        guard let item = context.inputItems.first as? NSExtensionItem,
              let message = item.userInfo?[SFExtensionMessageKey] as? [String: Any],
              let kind = message["type"] as? String,
              kind == "ping" || kind == "start" else {
            reply(context, ["ok": false, "error": "unknown_command"])
            return
        }

        var request = URLRequest(url: URL(string: baseURL + "/health")!)
        request.timeoutInterval = 3
        request.cachePolicy = .reloadIgnoringLocalCacheData
        let configuration = URLSessionConfiguration.ephemeral
        configuration.httpShouldSetCookies = false
        configuration.httpCookieStorage = nil
        configuration.urlCache = nil
        let session = URLSession(configuration: configuration)
        session.dataTask(with: request) { data, response, _ in
            let object = data.flatMap { try? JSONSerialization.jsonObject(with: $0) }
                as? [String: Any]
            let running = (response as? HTTPURLResponse)?.statusCode == 200
                && response?.url?.absoluteString == self.baseURL + "/health"
                && (object?["service"] as? String) == "zevent-manual"
            var result: [String: Any] = [
                "ok": kind == "ping" || running,
                "running": running,
                "url": self.baseURL
            ]
            if running {
                result["already"] = true
            } else if kind == "start" {
                result["error"] = "companion_required"
            }
            self.reply(context, result)
            session.finishTasksAndInvalidate()
        }.resume()
    }

    private func reply(_ context: NSExtensionContext, _ value: [String: Any]) {
        let response = NSExtensionItem()
        response.userInfo = [SFExtensionMessageKey: value]
        context.completeRequest(returningItems: [response], completionHandler: nil)
    }
}
