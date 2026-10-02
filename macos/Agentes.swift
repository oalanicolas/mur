import AppKit
import WebKit

@MainActor var dashboardURL = URL(string: "http://127.0.0.1:1/")!

@MainActor
func isLocalPage(_ url: URL) -> Bool {
    url.scheme == "http" && url.host == "127.0.0.1" && url.port == dashboardURL.port
}

@MainActor
final class PortalWindow: NSWindowController, WKNavigationDelegate, WKUIDelegate {
    let browser: WKWebView
    let status = NSStackView()
    let message = NSTextField(labelWithString: "Abrindo seu histórico…")
    let progress = NSProgressIndicator()
    let retry = NSButton(title: "Tentar novamente", target: nil, action: nil)
    var retryAction: (() -> Void)?
    var newWindow: ((WKWebViewConfiguration) -> WKWebView)?
    #if APP_VALIDATION
    var exportDestination: ((URL) -> Void)?
    #endif

    init(configuration: WKWebViewConfiguration, title: String) {
        browser = WKWebView(frame: .zero, configuration: configuration)
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1320, height: 900),
                              styleMask: [.titled, .closable, .miniaturizable, .resizable],
                              backing: .buffered, defer: false)
        window.title = title
        window.subtitle = "Model Usage Reports"
        window.minSize = NSSize(width: 800, height: 600)
        window.isReleasedWhenClosed = false
        window.center()
        window.setFrameAutosaveName(title)
        super.init(window: window)
        let content = window.contentView!
        browser.frame = content.bounds
        browser.autoresizingMask = [.width, .height]
        browser.navigationDelegate = self
        browser.uiDelegate = self
        browser.allowsBackForwardNavigationGestures = true
        browser.isHidden = true
        content.addSubview(browser)

        let icon = NSImageView(image: NSApp.applicationIconImage)
        icon.translatesAutoresizingMaskIntoConstraints = false
        NSLayoutConstraint.activate([icon.widthAnchor.constraint(equalToConstant: 72), icon.heightAnchor.constraint(equalToConstant: 72)])
        message.font = .systemFont(ofSize: 18, weight: .medium)
        message.alignment = .center
        progress.style = .spinning
        progress.controlSize = .small
        retry.target = self
        retry.action = #selector(tryAgain)
        retry.bezelStyle = .rounded
        retry.isHidden = true
        status.orientation = .vertical
        status.spacing = 18
        status.translatesAutoresizingMaskIntoConstraints = false
        [icon, message, progress, retry].forEach { status.addArrangedSubview($0) }
        content.addSubview(status)
        NSLayoutConstraint.activate([status.centerXAnchor.constraint(equalTo: content.centerXAnchor), status.centerYAnchor.constraint(equalTo: content.centerYAnchor)])
        progress.startAnimation(nil)
    }

    required init?(coder: NSCoder) { fatalError("Use init(configuration:title:)") }

    func show() {
        showWindow(nil)
        window?.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    func loading() {
        browser.isHidden = true
        status.isHidden = false
        message.stringValue = "Abrindo seu histórico…"
        retry.isHidden = true
        progress.isHidden = false
        progress.startAnimation(nil)
    }

    func failed() {
        browser.isHidden = true
        status.isHidden = false
        message.stringValue = "Não foi possível abrir seu histórico."
        progress.stopAnimation(nil)
        progress.isHidden = true
        retry.isHidden = false
    }

    @objc func tryAgain() { retryAction?() }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        status.isHidden = true
        progress.stopAnimation(nil)
        browser.isHidden = false
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        if (error as NSError).code != NSURLErrorCancelled { failed() }
    }

    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        if (error as NSError).code != NSURLErrorCancelled { failed() }
    }

    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) { webView.reload() }

    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let url = action.request.url else { decisionHandler(.cancel); return }
        if isLocalPage(url) {
            if ["/api/export", "/api/transfer"].contains(url.path) {
                decisionHandler(.cancel)
                saveCSV(url)
            } else {
                decisionHandler(.allow)
            }
            return
        }
        decisionHandler(.cancel)
        if action.navigationType == .linkActivated && ["https", "http", "codex"].contains(url.scheme ?? "") {
            NSWorkspace.shared.open(url)
        }
    }

    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                 for navigationAction: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        guard let url = navigationAction.request.url, isLocalPage(url) else { return nil }
        return newWindow?(configuration)
    }

    func saveCSV(_ url: URL) {
        Task { @MainActor in
            do { _ = try await requestExport(url) }
            catch {
                let alert = NSAlert()
                alert.messageText = "Não foi possível salvar a exportação."
                alert.informativeText = "Confira a pasta escolhida e tente novamente."
                await alert.beginSheetModal(for: self.window!)
            }
        }
    }

    func requestExport(_ url: URL) async throws -> Bool {
        #if APP_VALIDATION
        if let exportDestination { exportDestination(url); return true }
        #endif
        let panel = NSSavePanel()
        panel.title = "Exportar consumo"
        panel.nameFieldStringValue = url.path == "/api/transfer" ? "MUR-7-dias.mur" : "MUR-consumo.csv"
        panel.directoryURL = FileManager.default.urls(for: .downloadsDirectory, in: .userDomainMask).first
        let destination: URL? = await withCheckedContinuation { continuation in
            panel.beginSheetModal(for: window!) { result in
                continuation.resume(returning: result == .OK ? panel.url : nil)
            }
        }
        guard let destination else { return false }
        try await exportCSV(url, to: destination)
        return true
    }

    func exportCSV(_ url: URL, to destination: URL) async throws {
        let (data, response) = try await URLSession.shared.data(from: url)
        guard (response as? HTTPURLResponse)?.statusCode == 200,
              response.mimeType == (url.path == "/api/transfer" ? "application/octet-stream" : "text/csv") else { throw URLError(.badServerResponse) }
        try data.write(to: destination, options: .atomic)
    }

    func webView(_ webView: WKWebView, runOpenPanelWith parameters: WKOpenPanelParameters,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping ([URL]?) -> Void) {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = false
        panel.allowsMultipleSelection = false
        panel.beginSheetModal(for: window!) { result in completionHandler(result == .OK ? panel.urls : nil) }
    }
}

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate, WKScriptMessageHandlerWithReply {
    var primary: PortalWindow!
    var reports: [PortalWindow] = []
    var starting = false
    var backend: Process?
    var readyFile: URL?
    var updates: Updates!
    var usageMenu: UsageMenu!

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
        updates = Updates()
        usageMenu = UsageMenu()
        usageMenu.openPanel = { [weak self] in self?.openUsagePanel() }
        usageMenu.refreshBalances = { [weak self] connect in
            Task { @MainActor in await self?.refreshBalances(connectClaude: connect) }
        }
        buildMenu()
        let configuration = WKWebViewConfiguration()
        configuration.userContentController.addScriptMessageHandler(self, contentWorld: .page, name: "murUpdates")
        configuration.userContentController.addScriptMessageHandler(self, contentWorld: .page, name: "murMenu")
        configuration.userContentController.addScriptMessageHandler(self, contentWorld: .page, name: "murExports")
        #if APP_VALIDATION
        configuration.websiteDataStore = .nonPersistent()
        #endif
        primary = makeWindow(configuration: configuration, title: "MUR")
        primary.retryAction = { [weak self] in self?.start() }
        primary.show()
        start()
        #if APP_VALIDATION
        Task { await validateNativeApp(self) }
        #endif
    }

    func makeWindow(configuration: WKWebViewConfiguration, title: String) -> PortalWindow {
        let portal = PortalWindow(configuration: configuration, title: title)
        portal.newWindow = { [weak self] configuration in
            guard let self else { return WKWebView(frame: .zero, configuration: configuration) }
            self.reports.removeAll { $0.window?.isVisible == false }
            let report = self.makeWindow(configuration: configuration, title: "Relatório — MUR")
            report.retryAction = { [weak report] in report?.browser.reload() }
            self.reports.append(report)
            report.show()
            return report.browser
        }
        return portal
    }

    func start() {
        guard !starting else { return }
        starting = true
        primary.loading()
        Task {
            defer { starting = false }
            if !(await serviceReady()) {
                if backend?.isRunning == true { backend?.terminate() }
                for port in [UserDefaults.standard.integer(forKey: "backendPort"), 0] {
                    do { try launchBackend(port: port) } catch { break }
                    let deadline = Date().addingTimeInterval(25)
                    while Date() < deadline && backend?.isRunning == true {
                        if let readyFile, let bytes = try? Data(contentsOf: readyFile),
                           let ready = try? JSONSerialization.jsonObject(with: bytes) as? [String: Int],
                           let chosen = ready["port"] {
                            dashboardURL = URL(string: "http://127.0.0.1:\(chosen)/")!
                            if await serviceReady() {
                                UserDefaults.standard.set(chosen, forKey: "backendPort")
                                break
                            }
                        }
                        try? await Task.sleep(nanoseconds: 250_000_000)
                    }
                    if await serviceReady() { break }
                    if backend?.isRunning == true { backend?.terminate() }
                }
            }
            if await serviceReady() {
                primary.browser.load(URLRequest(url: dashboardURL))
                await usageMenu.refresh()
            } else {
                primary.failed()
            }
        }
    }

    func serviceReady() async -> Bool {
        do {
            var request = URLRequest(url: dashboardURL.appendingPathComponent("api/health"))
            request.timeoutInterval = 2
            request.cachePolicy = .reloadIgnoringLocalCacheData
            let (data, response) = try await URLSession.shared.data(for: request)
            let body = try JSONSerialization.jsonObject(with: data) as? [String: Any]
            return (response as? HTTPURLResponse)?.statusCode == 200 && body?["application"] as? String == "mur"
        } catch { return false }
    }

    func launchBackend(port: Int) throws {
        let manager = FileManager.default
        var profile = manager.homeDirectoryForCurrentUser.appendingPathComponent("Library/Application Support/MUR", isDirectory: true)
        #if APP_VALIDATION
        profile = URL(fileURLWithPath: ProcessInfo.processInfo.environment["MUR_DATA_DIR"]!)
        #endif
        try manager.createDirectory(at: profile, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
        let resources = Bundle.main.resourceURL!
        #if arch(arm64)
        let architecture = "arm64"
        #else
        let architecture = "x86_64"
        #endif
        let process = Process()
        process.executableURL = resources.appendingPathComponent("runtime/\(architecture)/python/bin/python3")
        let ready = profile.appendingPathComponent("ready-\(UUID().uuidString).json")
        readyFile = ready
        process.arguments = [resources.appendingPathComponent("backend/server.py").path, "--port", String(port), "--data-dir", profile.path, "--ready-file", ready.path, "--parent-pid", String(ProcessInfo.processInfo.processIdentifier)]
        process.currentDirectoryURL = resources.appendingPathComponent("backend")
        var environment = ["PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HOME": manager.homeDirectoryForCurrentUser.path,
                           "MUR_TIMEZONE": TimeZone.current.identifier, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1", "PYTHONNOUSERSITE": "1",
                           "MUR_NATIVE_EXECUTABLE": Bundle.main.executableURL!.path]
        #if APP_VALIDATION
        environment["MUR_HOME"] = ProcessInfo.processInfo.environment["MUR_HOME"]!
        #endif
        process.environment = environment
        let log = profile.appendingPathComponent("service.log")
        if !manager.fileExists(atPath: log.path) { manager.createFile(atPath: log.path, contents: nil, attributes: [.posixPermissions: 0o600]) }
        let output = try FileHandle(forWritingTo: log)
        output.seekToEndOfFile()
        process.standardOutput = output
        process.standardError = output
        try process.run()
        backend = process
    }

    func applicationWillTerminate(_ notification: Notification) {
        usageMenu?.stop()
        if backend?.isRunning == true { backend?.terminate() }
        if let readyFile { try? FileManager.default.removeItem(at: readyFile) }
    }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        primary.show()
        if primary.browser.url == nil { start() }
        return true
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { false }

    func openUsagePanel() {
        primary.show()
        var address = URLComponents(url: dashboardURL, resolvingAgainstBaseURL: false)!
        address.fragment = "limits"
        primary.browser.load(URLRequest(url: address.url!))
    }

    func refreshBalances(connectClaude: Bool) async {
        do {
            let (data, response) = try await URLSession.shared.data(from: dashboardURL.appendingPathComponent("api/state"))
            guard (response as? HTTPURLResponse)?.statusCode == 200,
                  let state = try JSONSerialization.jsonObject(with: data) as? [String: Any],
                  let token = state["csrf"] as? String else { return }
            var request = URLRequest(url: dashboardURL.appendingPathComponent("api/limits/refresh"))
            request.httpMethod = "POST"
            request.setValue(token, forHTTPHeaderField: "X-Local-Token")
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
            request.httpBody = try JSONSerialization.data(withJSONObject: ["connectClaude": connectClaude])
            _ = try await URLSession.shared.data(for: request)
            await usageMenu.refresh()
        } catch { openUsagePanel() }
    }

    func userContentController(_ userContentController: WKUserContentController,
                               didReceive message: WKScriptMessage,
                               replyHandler: @escaping (Any?, String?) -> Void) {
        let origin = message.frameInfo.securityOrigin
        guard message.frameInfo.isMainFrame, origin.protocol == "http", origin.host == "127.0.0.1",
              origin.port == dashboardURL.port, let body = message.body as? [String: Any],
              let action = body["action"] as? String else {
            replyHandler(nil, "Origem não autorizada.")
            return
        }
        if message.name == "murMenu" {
            if action == "enabled", let enabled = body["enabled"] as? Bool { usageMenu.setEnabled(enabled) }
            else if action != "status" { replyHandler(nil, "Preferência inválida."); return }
            replyHandler(["enabled": usageMenu.enabled], nil)
            return
        }
        if message.name == "murExports" {
            guard action == "save", let path = body["path"] as? String, path.count <= 16000,
                  let url = URL(string: path, relativeTo: dashboardURL)?.absoluteURL, isLocalPage(url),
                  ["/api/export", "/api/transfer"].contains(url.path),
                  let portal = ([primary!] + reports).first(where: { $0.browser === message.webView }) else {
                replyHandler(nil, "Exportação inválida."); return
            }
            Task { @MainActor in
                do { replyHandler(["saved": try await portal.requestExport(url)], nil) }
                catch { replyHandler(nil, "Não foi possível salvar a exportação. Confira a pasta e tente novamente.") }
            }
            return
        }
        switch action {
        case "status": break
        case "check":
            if updates.controller != nil { updates.checkForUpdates(nil) }
        case "automatic":
            guard let enabled = body["enabled"] as? Bool else {
                replyHandler(nil, "Preferência inválida.")
                return
            }
            updates.controller?.updater.automaticallyChecksForUpdates = enabled
        default:
            replyHandler(nil, "Ação desconhecida.")
            return
        }
        replyHandler(updates.state, nil)
    }

    @objc func openDashboard() {
        primary.show()
        if primary.browser.url == nil { start() }
        else { primary.browser.load(URLRequest(url: dashboardURL)) }
    }

    @objc func reloadPage() {
        let portal = ([primary!] + reports).first { $0.window === NSApp.keyWindow } ?? primary!
        if portal.browser.url == nil { start() } else { portal.browser.reload() }
    }

    func buildMenu() {
        let bar = NSMenu()
        func menu(_ title: String) -> NSMenu {
            let item = NSMenuItem(title: title, action: nil, keyEquivalent: "")
            let submenu = NSMenu(title: title)
            item.submenu = submenu
            bar.addItem(item)
            return submenu
        }
        let application = menu("MUR")
        application.addItem(withTitle: "Sobre MUR", action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)), keyEquivalent: "")
        let update = application.addItem(withTitle: "Verificar atualizações…", action: #selector(Updates.checkForUpdates(_:)), keyEquivalent: "")
        update.target = updates
        application.addItem(.separator())
        application.addItem(withTitle: "Ocultar MUR", action: #selector(NSApplication.hide(_:)), keyEquivalent: "h")
        application.addItem(withTitle: "Sair de MUR", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        let file = menu("Arquivo")
        let open = file.addItem(withTitle: "Abrir painel", action: #selector(openDashboard), keyEquivalent: "n")
        open.target = self
        file.addItem(withTitle: "Fechar janela", action: #selector(NSWindow.performClose(_:)), keyEquivalent: "w")
        let edit = menu("Editar")
        for (title, selector, key) in [("Desfazer", "undo:", "z"), ("Recortar", "cut:", "x"), ("Copiar", "copy:", "c"), ("Colar", "paste:", "v"), ("Selecionar tudo", "selectAll:", "a")] {
            edit.addItem(withTitle: title, action: Selector(selector), keyEquivalent: key)
        }
        let view = menu("Visualizar")
        let reload = view.addItem(withTitle: "Recarregar painel", action: #selector(reloadPage), keyEquivalent: "r")
        reload.target = self
        let fullscreen = view.addItem(withTitle: "Tela cheia", action: #selector(NSWindow.toggleFullScreen(_:)), keyEquivalent: "f")
        fullscreen.keyEquivalentModifierMask = [.command, .control]
        let window = menu("Janela")
        window.addItem(withTitle: "Minimizar", action: #selector(NSWindow.performMiniaturize(_:)), keyEquivalent: "m")
        NSApp.windowsMenu = window
        NSApp.mainMenu = bar
    }
}

@main
struct AgentesLocais {
    @MainActor static func main() {
        if CommandLine.arguments.count == 3 && CommandLine.arguments[1] == "--claude-credential" {
            exit(claudeCredential(interactive: CommandLine.arguments[2] == "interactive"))
        }
        let app = NSApplication.shared
        let delegate = AppDelegate()
        app.delegate = delegate
        withExtendedLifetime(delegate) { app.run() }
    }
}
