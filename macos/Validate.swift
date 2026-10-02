import AppKit
import WebKit

@MainActor
func validateNativeApp(_ app: AppDelegate) async {
    let output = URL(fileURLWithPath: CommandLine.arguments.last!, isDirectory: true)
    var checks: [String] = []
    func check(_ value: Bool, _ label: String) throws {
        if !value { throw NSError(domain: "NativeValidation", code: 1, userInfo: [NSLocalizedDescriptionKey: label]) }
        checks.append(label)
        print("OK: " + label)
        fflush(stdout)
    }
    func wait(_ script: String) async throws {
        let deadline = Date().addingTimeInterval(45)
        while Date() < deadline {
            if (try? await app.primary.browser.evaluateJavaScript(script)) as? Bool == true { return }
            try await Task.sleep(nanoseconds: 250_000_000)
        }
        throw NSError(domain: "NativeValidation", code: 2, userInfo: [NSLocalizedDescriptionKey: "Tempo esgotado: " + script])
    }
    do {
        try FileManager.default.createDirectory(at: output, withIntermediateDirectories: true)
        let browser = app.primary.browser
        try await wait("typeof appState !== 'undefined' && appState?.settings.setupComplete === false && !document.querySelector('#welcome').hidden")
        for _ in 0..<30 {
            if NSApp.activationPolicy() == .regular && !browser.isHidden { break }
            try await Task.sleep(nanoseconds: 100_000_000)
        }
        try check(app.primary.window?.title == "MUR" && app.primary.window?.subtitle == "Model Usage Reports" && app.primary.window?.isVisible == true, "Janela MUR sem endereço, com boas-vindas")
        try check(NSApp.activationPolicy() == .regular && !browser.isHidden, "Aplicativo no Dock com WebKit nativo")
        try check(app.backend?.isRunning == true && app.backend?.executableURL?.path.contains("/Contents/Resources/runtime/") == true, "Serviço iniciado pelo Python incluído no aplicativo")
        try check(!isLocalPage(URL(string: "https://example.com/")!) && !isLocalPage(URL(string: "http://127.0.0.1:1/")!), "Navegação restrita à origem escolhida pelo aplicativo")
        try check(try await browser.evaluateJavaScript("appState.counts.events===0 && Object.values(appState.settings.monthly).every(v=>v===0) && document.querySelector('#saved-report').hidden") as? Bool == true, "Perfil novo sem consumo, mensalidades ou relatório pessoal")
        try check(try await browser.evaluateJavaScript("appState.accounts.length===3 && !JSON.stringify(appState).includes('SYNTHETIC-SECRET') && !JSON.stringify(appState).includes('example-person@example.test')") as? Bool == true, "Contas detectadas sem credenciais ou e-mail completo na interface")
        app.usageMenu.setEnabled(true)
        try check(app.usageMenu.statusItem?.button != nil, "MUR cria um item nativo na barra de menus")
        _ = try await browser.evaluateJavaScript("location.hash='limits'")
        try await wait("!document.querySelector('#limits').hidden && !document.querySelector('#menu-bar-setting').hidden")
        try check(try await browser.evaluateJavaScript("!appState.limits.enabled && !document.querySelector('#live-limits').checked && document.querySelector('#limit-cards').textContent.includes('Ative')") as? Bool == true, "Consulta de saldo começa desativada sem pedir credenciais")
        _ = try await browser.evaluateJavaScript("document.querySelector('#menu-bar').click()")
        try await wait("document.querySelector('#menu-bar').checked===false")
        try check(app.usageMenu.statusItem == nil, "Preferência na interface remove o item da barra de menus")
        _ = try await browser.evaluateJavaScript("document.querySelector('#menu-bar').click()")
        try await wait("document.querySelector('#menu-bar').checked===true")
        try check(app.usageMenu.statusItem != nil, "Preferência na interface restaura o item da barra de menus")
        var quota: [String: Any] = ["provider":"OpenAI", "name":"Codex", "label":"Conta de teste", "status":"ready", "stale":false,
                                  "windows":[["label":"Semana", "remainingPercent":27.0, "resetsAt":Date().addingTimeInterval(3600).timeIntervalSince1970]],
                                  "credits":[["balance":19.5]]]
        app.usageMenu.render(["enabled":true,"rows":[quota]])
        try check(app.usageMenu.statusItem?.button?.title.contains("27%") == true && app.usageMenu.statusItem?.menu?.items.contains(where: {$0.title.hasPrefix("Renova ")}) == true, "Barra de menus mostra saldo e renovação informados pelo serviço")
        quota["stale"] = true
        app.usageMenu.render(["enabled":true,"rows":[quota]])
        try check(app.usageMenu.statusItem?.button?.title == " MUR —" && app.usageMenu.statusItem?.menu?.items.contains(where: {$0.title.contains("desatualizado")}) == true, "Saldo desatualizado não aparece como disponibilidade atual na barra")
        let fixture = "const quotaAccount=appState.accounts.find(a=>a.provider==='OpenAI');appState.limits={enabled:true,refreshing:false,rows:[{accountId:quotaAccount.id,status:'ready',stale:true,lastSuccess:1000,windows:[{label:'Semana',remainingPercent:0,resetsAt:1001}],credits:[{label:'Créditos Codex',balance:19.5}]}]};renderLimits()"
        _ = try await browser.evaluateJavaScript(fixture)
        try check(try await browser.evaluateJavaScript("document.querySelector('#limit-cards').textContent.includes('desatualizado') && document.querySelector('#limit-cards').textContent.includes('0% restante') && document.querySelector('#limit-cards').textContent.includes('créditos') && document.querySelector('#limit-badge').textContent==='' ") as? Bool == true, "Painel distingue saldo esgotado, créditos e leitura desatualizada")
        _ = try await browser.evaluateJavaScript("void loadState()")
        try await wait("appState.limits.enabled===false")
        await app.usageMenu.refresh()
        _ = try await browser.evaluateJavaScript("location.hash='settings'")
        try await wait("!document.querySelector('#settings').hidden && document.querySelector('#detected-accounts button')!==null")
        try await wait("document.querySelector('#update-status').textContent.includes('ainda não foi publicado')")
        try check(app.updates.controller == nil && NSApp.mainMenu?.items.first?.submenu?.items.contains(where: {$0.title == "Verificar atualizações…"}) == true, "Menu e configurações distinguem canal ausente de aplicativo atualizado")
        _ = try await browser.evaluateJavaScript("document.querySelector('#detected-accounts button').click()")
        try check(try await browser.evaluateJavaScript("document.querySelector('#subscription-rows [data-field=accountId]').value.length>0 && document.querySelector('#subscription-rows [data-field=monthlyUsd]').value==='' ") as? Bool == true, "Conta preenche identidade, mas valor pago exige confirmação")
        _ = try await browser.evaluateJavaScript("document.querySelector('#detected-accounts button').click()")
        try check(try await browser.evaluateJavaScript("document.querySelector('#subscription-rows').children.length===1") as? Bool == true, "Usar a mesma conta novamente não duplica a assinatura")
        _ = try await browser.evaluateJavaScript("Object.defineProperty(document,'hidden',{get:()=>true,configurable:true});document.querySelector('#settings-form').requestSubmit()")
        try await wait("appState?.metadata.initial_scan_complete===true && document.querySelector('#metric-tokens').textContent==='110'")
        _ = try await browser.evaluateJavaScript("delete document.hidden")
        try check(true, "Primeira análise conclui e atualiza a tela mesmo com a janela em segundo plano")
        try await wait("document.querySelector('#metric-subscription').textContent==='A confirmar'")
        try check(true, "Mensalidade desconhecida não aparece como gratuita")
        _ = try await browser.evaluateJavaScript("location.hash='settings'")
        try await wait("!document.querySelector('#settings').hidden && document.querySelector('#sources-form [name=machineLabel]').value.length>0")
        _ = try await browser.evaluateJavaScript("document.querySelector('#subscription-rows [data-field=label]').value='Conta de teste';document.querySelector('#subscription-rows [data-field=monthlyUsd]').value='30';document.querySelector('#subscription-rows [data-field=startDate]').value='2000-01-01';document.querySelector('#settings-form').requestSubmit()")
        try await wait("Object.values(appState?.settings.monthly||{}).reduce((a,b)=>a+b,0)===30")
        try check(true, "Mensalidade própria salva pela interface")
        _ = try await browser.evaluateJavaScript("[...document.querySelectorAll('#subscription-rows button')].find(b=>b.textContent==='Registrar pagamento').click();document.querySelector('[data-payment-field=date]').value=new Date().toLocaleDateString('sv-SE',{timeZone:appState.timezone});document.querySelector('[data-payment-field=amountUsd]').value='30';document.querySelector('#settings-form').requestSubmit()")
        try await wait("appState.settings.subscriptions[0].payments?.length===1")
        try check(true, "Pagamento datado é cadastrado separadamente da mensalidade")
        _ = try await browser.evaluateJavaScript("location.hash='flow'")
        try await wait("document.querySelector('#flow').classList.contains('rp-empty')")
        try check(true, "Fluxo vazio explica a ausência de transcrições")
        _ = try await browser.evaluateJavaScript("location.hash='sessions';localStorage.setItem('mur-qa','ok')")
        try await wait("!document.querySelector('#sessions').hidden && document.querySelector('#session-count').textContent.includes('1')")
        browser.reload()
        try await wait("document.querySelector('#sessions')?.hidden===false && localStorage.getItem('mur-qa')==='ok'")
        try check(true, "Conversas, navegação e preferências preservadas na recarga")
        _ = try await browser.evaluateJavaScript("location.hash='overview'")
        try await wait("!document.querySelector('#overview-summary').hidden && document.querySelector('#metric-tokens').textContent==='110'")
        try await wait("document.querySelector('#request-status').hasAttribute('data-idle')")
        let order = try await browser.evaluateJavaScript("document.querySelector('#overview-summary').getBoundingClientRect().bottom <= document.querySelector('#filters').getBoundingClientRect().top")
        try check(order as? Bool == true, "Resumo financeiro acima dos filtros")
        _ = try await browser.evaluateJavaScript("globalThis.oldLoading=startLoading('native-test','Consulta antiga…',[document.querySelector('#overview-summary')]);globalThis.newLoading=startLoading('native-test','Consulta nova…',[document.querySelector('#overview-summary')]);oldLoading()")
        try check(try await browser.evaluateJavaScript("document.querySelector('#overview-summary').getAttribute('aria-busy')==='true' && document.querySelector('#request-label').textContent==='Consulta nova…' && !document.querySelector('[name=provider]').disabled") as? Bool == true, "Carregamento nativo preserva a consulta nova e mantém os filtros disponíveis")
        _ = try await browser.evaluateJavaScript("newLoading()")
        try check(try await browser.evaluateJavaScript("document.querySelector('#overview-summary').getAttribute('aria-busy')==='false' && document.querySelector('#request-status').hasAttribute('data-idle')") as? Bool == true, "Fim da consulta encerra o indicador de carregamento")
        var exportURL: URL?
        app.primary.exportDestination = { exportURL = $0 }
        _ = try await browser.evaluateJavaScript("document.querySelector('#export').click()")
        for _ in 0..<30 {
            if exportURL != nil { break }
            try await Task.sleep(nanoseconds: 100_000_000)
        }
        try check(exportURL?.path == "/api/export", "Exportação interceptada pelo aplicativo")
        try await wait("!document.querySelector('#export').disabled")
        try check(true, "Exportação nativa encerra o loading após a resposta do diálogo")
        _ = try await browser.evaluateJavaScript("globalThis.invalidExportRejected=false;void window.webkit.messageHandlers.murExports.postMessage({action:'save',path:'https://example.com/api/export'}).catch(()=>{invalidExportRejected=true})")
        try await wait("invalidExportRejected===true")
        try check(true, "Ponte de exportação rejeita destinos externos")
        try await app.primary.exportCSV(exportURL!, to: output.appendingPathComponent("export.csv"))
        let csv = try String(contentsOf: output.appendingPathComponent("export.csv"), encoding: .utf8)
        try check(csv.contains("external_id") && csv.contains("portable-native"), "CSV contém o registro sintético")
        try await app.primary.exportCSV(dashboardURL.appendingPathComponent("api/transfer"), to: output.appendingPathComponent("export.mur"))
        try check(FileManager.default.fileExists(atPath: output.appendingPathComponent("export.mur").path), "Coleta portátil salva pelo aplicativo")
        app.primary.close()
        try check(app.backend?.isRunning == true && app.usageMenu.statusItem != nil && !app.applicationShouldTerminateAfterLastWindowClosed(NSApp), "Fechar a janela mantém serviço e item da barra de menus ativos")
        _ = app.applicationShouldHandleReopen(NSApp, hasVisibleWindows: false)
        try check(app.primary.window?.isVisible == true, "Fechar e reabrir pelo Dock mantém o painel")
        _ = try await browser.evaluateJavaScript("scrollTo(0,0)")
        try await Task.sleep(nanoseconds: 250_000_000)
        let snapshot = try await browser.takeSnapshot(configuration: nil)
        if let tiff = snapshot.tiffRepresentation, let bitmap = NSBitmapImageRep(data: tiff), let png = bitmap.representation(using: .png, properties: [:]) {
            try png.write(to: output.appendingPathComponent("native-panel.png"))
        }
        let result = try JSONSerialization.data(withJSONObject: ["ok":true,"checks":checks,"backendPID":app.backend?.processIdentifier ?? 0], options:[.prettyPrinted,.sortedKeys])
        try result.write(to:output.appendingPathComponent("result.json"))
        NSApp.terminate(nil)
    } catch {
        var report: [String:Any] = ["ok":false,"checks":checks,"error":error.localizedDescription]
        report["ui"] = try? await app.primary.browser.evaluateJavaScript("({hash:location.hash,tokens:document.querySelector('#metric-tokens').textContent,error:document.querySelector('#error').textContent,loading:document.querySelector('#request-label').textContent,setup:appState?.settings.setupComplete,scanComplete:appState?.metadata.initial_scan_complete,view:currentView,provider:params.provider,period:params.period})")
        if let result = try? JSONSerialization.data(withJSONObject:report, options:[.prettyPrinted,.sortedKeys]) {
            try? result.write(to:output.appendingPathComponent("result.json"))
        }
        print(error.localizedDescription)
        app.applicationWillTerminate(Notification(name:NSApplication.willTerminateNotification))
        exit(1)
    }
}
