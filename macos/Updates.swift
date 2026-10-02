import AppKit
import Sparkle

@MainActor
final class Updates: NSObject, NSMenuItemValidation {
    var controller: SPUStandardUpdaterController?
    private(set) var failure: String?

    override init() {
        super.init()
        guard let address = Bundle.main.object(forInfoDictionaryKey: "SUFeedURL") as? String,
              let url = URL(string: address), url.scheme == "https", url.host != nil,
              let key = Bundle.main.object(forInfoDictionaryKey: "SUPublicEDKey") as? String,
              Data(base64Encoded: key)?.count == 32 else { return }
        let updater = SPUStandardUpdaterController(startingUpdater: false, updaterDelegate: nil, userDriverDelegate: nil)
        do {
            try updater.updater.start()
            controller = updater
        } catch {
            failure = "Não foi possível iniciar as atualizações. Reabra o MUR e tente novamente."
        }
    }

    var state: [String: Any] {
        ["configured": controller != nil,
         "version": Bundle.main.object(forInfoDictionaryKey: "MURReleaseVersion") as? String ?? "—",
         "canCheck": controller?.updater.canCheckForUpdates ?? false,
         "automatic": controller?.updater.automaticallyChecksForUpdates ?? false,
         "lastCheck": controller?.updater.lastUpdateCheckDate?.timeIntervalSince1970 ?? 0,
         "message": failure ?? (controller == nil ? "O canal de atualizações desta edição ainda não foi publicado. Quando estiver disponível, será necessário instalar a primeira versão conectada ao canal." : "O MUR avisa quando há uma nova versão. Você escolhe quando instalar e reabrir; seu histórico e suas configurações ficam preservados.")]
    }

    @objc func checkForUpdates(_ sender: Any?) {
        if let controller {
            if controller.updater.canCheckForUpdates { controller.checkForUpdates(sender) }
        } else {
            let alert = NSAlert()
            alert.messageText = "Atualizações do MUR"
            alert.informativeText = state["message"] as? String ?? ""
            alert.addButton(withTitle: "Entendi")
            alert.runModal()
        }
    }

    func validateMenuItem(_ item: NSMenuItem) -> Bool {
        controller?.updater.canCheckForUpdates ?? true
    }
}
