import AppKit
import Security

func readCredential(service: String, interactive: Bool, keychain: SecKeychain? = nil) -> (OSStatus, Data?) {
    var previous: DarwinBoolean = false
    if !interactive {
        // File-based login keychains need the legacy API to reliably suppress authentication UI.
        guard SecKeychainGetUserInteractionAllowed(&previous) == errSecSuccess,
              SecKeychainSetUserInteractionAllowed(false) == errSecSuccess else {
            return (errSecInteractionNotAllowed, nil)
        }
    }
    defer {
        if !interactive { SecKeychainSetUserInteractionAllowed(previous.boolValue) }
    }
    var target: CFTypeRef? = keychain
    if !interactive {
        var searchList: CFArray?
        if let keychain {
            searchList = [keychain] as CFArray
        } else if SecKeychainCopySearchList(&searchList) != errSecSuccess {
            return (errSecInteractionNotAllowed, nil)
        }
        let unlocked = (searchList as? [SecKeychain] ?? []).filter {
            var state: SecKeychainStatus = 0
            return SecKeychainGetStatus($0, &state) == errSecSuccess && state & UInt32(kSecUnlockStateStatus) != 0
        }
        // Password lookup can auto-unlock a locked keychain even when interaction is disabled.
        guard !unlocked.isEmpty else { return (errSecInteractionNotAllowed, nil) }
        target = unlocked as CFArray
    }
    var length: UInt32 = 0
    var bytes: UnsafeMutableRawPointer?
    let status = service.withCString {
        SecKeychainFindGenericPassword(target, UInt32(service.utf8.count), $0, 0, nil, &length, &bytes, nil)
    }
    guard status == errSecSuccess, let bytes else { return (status, nil) }
    defer { SecKeychainItemFreeContent(nil, bytes) }
    return (status, Data(bytes: bytes, count: Int(length)))
}

func claudeCredential(interactive: Bool) -> Int32 {
    let (status, data) = readCredential(service: "Claude Code-credentials", interactive: interactive)
    guard status == errSecSuccess, let data,
          (try? JSONSerialization.jsonObject(with: data)) is [String: Any] else { return 1 }
    FileHandle.standardOutput.write(data)
    return 0
}

@MainActor
final class UsageMenu: NSObject {
    private(set) var statusItem: NSStatusItem?
    var timer: Timer?
    var fetching = false
    var snapshot: [String: Any] = ["enabled": false, "rows": []]
    var openPanel: (() -> Void)?
    var refreshBalances: ((Bool) -> Void)?

    var enabled: Bool {
        UserDefaults.standard.object(forKey: "murMenuBarEnabled") as? Bool ?? true
    }

    override init() {
        super.init()
        configure()
        timer = Timer.scheduledTimer(withTimeInterval: 15, repeats: true) { [weak self] _ in
            Task { @MainActor in await self?.refresh() }
        }
    }

    func setEnabled(_ value: Bool) {
        UserDefaults.standard.set(value, forKey: "murMenuBarEnabled")
        configure()
    }

    func configure() {
        if !enabled {
            if let statusItem { NSStatusBar.system.removeStatusItem(statusItem) }
            statusItem = nil
            return
        }
        if statusItem == nil {
            statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
            let image = NSImage(systemSymbolName: "circle.hexagongrid.fill", accessibilityDescription: "MUR, uso disponível de IA")
            image?.isTemplate = true
            statusItem?.button?.image = image
            statusItem?.button?.imagePosition = .imageLeading
        }
        render(snapshot)
    }

    func refresh() async {
        guard !fetching else { return }
        fetching = true
        defer { fetching = false }
        do {
            var request = URLRequest(url: dashboardURL.appendingPathComponent("api/limits"))
            request.timeoutInterval = 3
            request.cachePolicy = .reloadIgnoringLocalCacheData
            let (data, response) = try await URLSession.shared.data(for: request)
            guard (response as? HTTPURLResponse)?.statusCode == 200,
                  let value = try JSONSerialization.jsonObject(with: data) as? [String: Any] else { return }
            render(value)
        } catch {
            statusItem?.button?.title = " MUR —"
            statusItem?.button?.toolTip = "MUR · aguardando o serviço local"
        }
    }

    func percent(_ value: Double) -> String {
        String(format: "%.0f%%", value)
    }

    func resetDate(_ timestamp: Double) -> String {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "pt_BR")
        formatter.dateFormat = "dd/MM 'às' HH:mm"
        return formatter.string(from: Date(timeIntervalSince1970: timestamp))
    }

    func render(_ value: [String: Any]) {
        snapshot = value
        guard let statusItem else { return }
        let rows = value["enabled"] as? Bool == true ? value["rows"] as? [[String: Any]] ?? [] : []
        let menu = NSMenu()
        menu.autoenablesItems = false
        func line(_ title: String) {
            let item = menu.addItem(withTitle: title, action: nil, keyEquivalent: "")
            item.isEnabled = false
        }
        line("Uso disponível · MUR")
        menu.addItem(.separator())
        var highlights: [(String, Double)] = []
        if value["enabled"] as? Bool != true {
            line("Ative a consulta de saldo no aplicativo")
        } else if rows.isEmpty {
            line("Aguardando os provedores…")
        }
        for row in rows {
            let name = row["name"] as? String ?? "IA"
            line(name + " · " + (row["label"] as? String ?? ""))
            let stale = row["stale"] as? Bool == true
            let windows = row["windows"] as? [[String: Any]] ?? []
            for window in windows {
                guard let remaining = window["remainingPercent"] as? Double else { continue }
                line((window["label"] as? String ?? "Uso") + ": " + percent(remaining) + " restante" + (stale ? " · desatualizado" : ""))
                if let reset = window["resetsAt"] as? Double { line("Renova " + resetDate(reset)) }
                if !stale && row["status"] as? String == "ready" { highlights.append((name, remaining)) }
            }
            for credit in row["credits"] as? [[String: Any]] ?? [] {
                if let balance = credit["balance"] as? Double { line(String(format: "Créditos: %.2f", balance) + (stale ? " · desatualizado" : "")) }
            }
            if row["status"] as? String != "ready" {
                line(row["message"] as? String ?? "Saldo indisponível")
            }
            if let checked = row["lastSuccess"] as? Double { line("Consulta: " + resetDate(checked)) }
            menu.addItem(.separator())
        }
        let codex = highlights.filter { $0.0 == "Codex" }
        let selected = (codex.isEmpty ? highlights : codex).min { $0.1 < $1.1 }
        statusItem.button?.title = selected.map { " MUR · " + String($0.0.prefix(1)) + " " + percent($0.1) } ?? " MUR —"
        statusItem.button?.toolTip = selected.map { $0.0 + ": " + percent($0.1) + " restante no período mais restrito" } ?? "MUR · saldo ainda não disponível"
        let open = menu.addItem(withTitle: "Abrir uso disponível", action: #selector(openUsage), keyEquivalent: "")
        open.target = self
        let refresh = menu.addItem(withTitle: "Atualizar saldos", action: #selector(refreshUsage), keyEquivalent: "")
        refresh.target = self
        refresh.isEnabled = value["enabled"] as? Bool == true && value["refreshing"] as? Bool != true
        if rows.contains(where: { $0["provider"] as? String == "Anthropic" && $0["status"] as? String == "connectionRequired" }) {
            let connect = menu.addItem(withTitle: "Conectar Claude…", action: #selector(connectClaude), keyEquivalent: "")
            connect.target = self
        }
        menu.addItem(.separator())
        let quit = menu.addItem(withTitle: "Sair do MUR", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "")
        quit.target = NSApp
        statusItem.menu = menu
    }

    @objc func openUsage() { openPanel?() }
    @objc func refreshUsage() { refreshBalances?(false) }
    @objc func connectClaude() { refreshBalances?(true) }

    func stop() {
        timer?.invalidate()
        if let statusItem { NSStatusBar.system.removeStatusItem(statusItem) }
        statusItem = nil
    }
}
